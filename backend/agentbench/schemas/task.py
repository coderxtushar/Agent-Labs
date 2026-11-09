"""Versioned benchmark task specification.

A task is a *contract*: it says how to build a clean environment, what the agent
is allowed to see, which tests define success, and what the resource budget is.

Two invariants are enforced by this schema rather than left to convention:

1. **Hidden grading assets.**  ``grading.acceptance_tests`` files are injected
   into the workspace only during grading and are listed in
   ``grading.forbidden_paths`` automatically, so an agent cannot read or edit
   the tests that grade it.
2. **Non-empty success criterion.**  A task must declare at least one
   ``fail_to_pass`` test node, otherwise "success" would be unfalsifiable.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TASK_SCHEMA_VERSION = "1.0"

_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_SEMVER_LIKE_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
_GIT_REVISION_RE = re.compile(r"^[A-Za-z0-9._/@-]{1,120}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, str_strip_whitespace=True)


class TaskCategory(StrEnum):
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    REGRESSION_FIX = "regression_fix"
    TEST_AUTHORING = "test_authoring"
    PERFORMANCE = "performance"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class NetworkPolicy(StrEnum):
    #: No network namespace at all. The default, and the only safe option for
    #: untrusted repositories.
    NONE = "none"
    #: Network enabled so the agent can reach a model endpoint. Still no host
    #: filesystem or secret access. Weaker isolation - see docs/security.md.
    MODEL_ONLY = "model_only"
    #: Unrestricted egress. Requires explicit per-task opt-in.
    FULL = "full"


# --------------------------------------------------------------------------- #
# repository
# --------------------------------------------------------------------------- #
class LocalRepoSource(StrictModel):
    kind: Literal["local_path"] = "local_path"
    #: Path relative to ``AGENTBENCH_BENCHMARKS_DIR``. Absolute paths and any
    #: ``..`` component are rejected to prevent escaping the benchmark root.
    path: str
    revision: str = "local"
    subdir: str | None = None

    @field_validator("path")
    @classmethod
    def _safe_relative(cls, v: str) -> str:
        if v.startswith("/") or v.startswith("~"):
            raise ValueError("local_path must be relative to the benchmarks directory")
        parts = [p for p in v.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError("local_path must not contain '..'")
        if not parts:
            raise ValueError("local_path must not be empty")
        return "/".join(parts)


class GitRepoSource(StrictModel):
    kind: Literal["git"] = "git"
    url: str
    #: Must pin an immutable revision for reproducibility. A 40-char SHA is
    #: strongly preferred; branch names are allowed but warned about during
    #: validation.
    revision: str
    subdir: str | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if not (v.startswith(("https://", "http://", "git@", "ssh://"))):
            raise ValueError("git url must be http(s)/ssh")
        return v

    @field_validator("revision")
    @classmethod
    def _check_rev(cls, v: str) -> str:
        if not _GIT_REVISION_RE.match(v):
            raise ValueError("revision contains unsupported characters")
        return v

    @property
    def revision_is_immutable(self) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{40}", self.revision))


RepoSource = Annotated[LocalRepoSource | GitRepoSource, Field(discriminator="kind")]


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #
class EnvironmentSpec(StrictModel):
    #: Container image used for both the agent phase and the grading phase, so
    #: that a patch is graded in exactly the environment it was produced in.
    image: str = "python:3.12-slim"
    #: Commands run once after the repo is materialised, before the agent starts.
    #: A non-zero exit here yields ``INVALID_TASK``, never ``UNSOLVED``.
    setup_commands: list[str] = Field(default_factory=list)
    #: Non-secret environment variables. Values are recorded in the event log,
    #: so never put credentials here - use the adapter's provider config.
    env: dict[str, str] = Field(default_factory=dict)
    workdir: str = "/workspace/repo"
    #: Optional list of extra apt/pip packages, applied before setup_commands.
    pip_packages: list[str] = Field(default_factory=list)

    @field_validator("workdir")
    @classmethod
    def _abs_workdir(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("workdir must be an absolute path")
        return v.rstrip("/") or "/"

    @field_validator("env")
    @classmethod
    def _no_obvious_secrets(cls, v: dict[str, str]) -> dict[str, str]:
        banned = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY")
        for key in v:
            if any(b in key.upper() for b in banned):
                raise ValueError(
                    f"environment.env may not carry credentials ({key}); "
                    "configure provider secrets on the agent instead"
                )
        return v


# --------------------------------------------------------------------------- #
# tests / grading
# --------------------------------------------------------------------------- #
class TestRunner(StrEnum):
    PYTEST = "pytest"
    #: Opaque shell command; success is defined purely by exit code 0.
    COMMAND = "command"


class TestSpec(StrictModel):
    runner: TestRunner = TestRunner.PYTEST
    #: Command used to invoke the runner. ``{selection}`` is substituted with
    #: runner-specific test selectors; omit it to always run the whole suite.
    command: str = "python -m pytest -p no:cacheprovider -q {selection}"
    #: Tests that must FAIL before the patch and PASS after it. This is the
    #: task's success criterion and must be non-empty.
    fail_to_pass: list[str] = Field(min_length=1)
    #: Tests that must pass both before and after. Catching a change here is how
    #: regressions are detected.
    pass_to_pass: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _disjoint(self) -> TestSpec:
        overlap = set(self.fail_to_pass) & set(self.pass_to_pass)
        if overlap:
            raise ValueError(f"fail_to_pass and pass_to_pass overlap: {sorted(overlap)}")
        if self.runner is TestRunner.PYTEST and "{selection}" not in self.command:
            raise ValueError("pytest runner command must contain '{selection}'")
        return self


class StaticCheck(StrictModel):
    name: str
    command: str
    #: Advisory checks are reported but never affect the deterministic verdict.
    required: bool = False


class JudgeSpec(StrictModel):
    """Optional model-assisted review.

    Always reported as a *separate, labelled* signal.  It can never change the
    deterministic pass/fail verdict - see :mod:`agentbench.grading.engine`.
    """

    enabled: bool = False
    rubric: str = ""
    model: str | None = None


class GradingSpec(StrictModel):
    #: Bumped whenever grading semantics change, so results stay comparable.
    version: str = "1.0"
    #: Files injected into the workspace at grading time only. Keys are repo
    #: relative paths, values are file contents. These are hidden from the agent.
    acceptance_tests: dict[str, str] = Field(default_factory=dict)
    #: A run with an empty diff is graded ``UNSOLVED`` without running tests.
    require_non_empty_patch: bool = True
    #: Glob patterns the agent must not modify. Touching one fails the patch
    #: layer (prevents "fix" by deleting the test).
    #:
    #: The default also covers test-runner configuration files, because
    #: ``addopts = -p <module>`` in any of them loads an agent-authored plugin
    #: and makes every graded test pass. The grading engine enforces that set
    #: independently (see ``grading.patch.GRADING_INTEGRITY_PATHS``), so
    #: shortening this list cannot re-open that hole - it only changes what the
    #: agent is told up front.
    forbidden_paths: list[str] = Field(
        default_factory=lambda: [
            "tests/**",
            "**/conftest.py",
            "pytest.ini",
            "setup.cfg",
            "tox.ini",
            "pyproject.toml",
        ]
    )
    #: Require that fail_to_pass tests genuinely fail on the pristine repo. This
    #: is what detects an already-solved or mis-specified task.
    verify_baseline: bool = True
    static_checks: list[StaticCheck] = Field(default_factory=list)
    judge: JudgeSpec = Field(default_factory=JudgeSpec)

    @field_validator("acceptance_tests")
    @classmethod
    def _safe_paths(cls, v: dict[str, str]) -> dict[str, str]:
        for path in v:
            if path.startswith("/") or ".." in path.replace("\\", "/").split("/"):
                raise ValueError(f"acceptance_tests path must be a safe relative path: {path}")
        return v


class LimitsSpec(StrictModel):
    agent_timeout_s: int = Field(default=900, ge=1)
    setup_timeout_s: int = Field(default=600, ge=1)
    test_timeout_s: int = Field(default=600, ge=1)
    memory_mb: int = Field(default=2048, ge=128)
    cpus: float = Field(default=2.0, gt=0)
    disk_mb: int = Field(default=4096, ge=256)
    network: NetworkPolicy = NetworkPolicy.NONE
    #: Maximum number of agent tool calls / steps, where the adapter supports it.
    max_agent_steps: int = Field(default=60, ge=1)


# --------------------------------------------------------------------------- #
# the task
# --------------------------------------------------------------------------- #
class TaskSpec(StrictModel):
    """A single, fully reproducible benchmark task."""

    schema_version: str = TASK_SCHEMA_VERSION
    task_id: str
    version: str = "1.0"
    title: str = Field(min_length=3, max_length=200)
    #: Short human-facing summary. Shown in the UI, not given to the agent.
    description: str = ""
    #: The text handed to the agent verbatim. This is the agent's only
    #: natural-language input.
    problem_statement: str = Field(min_length=10)

    repo: RepoSource
    category: TaskCategory
    difficulty: Difficulty = Difficulty.MEDIUM
    tags: list[str] = Field(default_factory=list)

    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    tests: TestSpec
    grading: GradingSpec = Field(default_factory=GradingSpec)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)

    #: Paths surfaced to the agent as "probably relevant". Advisory only.
    context_files: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    #: Never included in agent input unless an experiment explicitly opts in.
    hints: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _TASK_ID_RE.match(v):
            raise ValueError(
                "task_id must be lowercase alphanumeric with '.', '_' or '-' (3-80 chars)"
            )
        return v

    @field_validator("version", "schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        if not _SEMVER_LIKE_RE.match(v):
            raise ValueError("version must look like MAJOR.MINOR or MAJOR.MINOR.PATCH")
        return v

    @field_validator("schema_version")
    @classmethod
    def _supported_schema(cls, v: str) -> str:
        if v.split(".")[0] != TASK_SCHEMA_VERSION.split(".")[0]:
            raise ValueError(f"unsupported task schema major version: {v}")
        return v

    @model_validator(mode="after")
    def _coherence(self) -> TaskSpec:
        # Acceptance-test files are automatically protected from the agent.
        protected = set(self.grading.forbidden_paths)
        protected.update(self.grading.acceptance_tests.keys())
        self.grading.forbidden_paths = sorted(protected)

        # A hidden acceptance test is only useful if some fail_to_pass node
        # actually lives in it; warn-level problems are surfaced by the
        # validator, but an obviously inconsistent task is rejected outright.
        if self.grading.require_non_empty_patch is False and self.category is TaskCategory.BUG_FIX:
            raise ValueError("bug_fix tasks must require a non-empty patch")
        return self

    @property
    def key(self) -> str:
        """Identity used for comparability: results are only comparable within a key."""
        return f"{self.task_id}@{self.version}"

    def agent_visible_payload(self, include_hints: bool = False) -> dict[str, Any]:
        """Exactly what an adapter is permitted to show the agent."""
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "task_version": self.version,
            "title": self.title,
            "problem_statement": self.problem_statement,
            "category": str(self.category),
            "relevant_files": list(self.context_files),
            "acceptance_criteria": list(self.acceptance_criteria),
            "workdir": self.environment.workdir,
            "max_steps": self.limits.max_agent_steps,
            "forbidden_paths": list(self.grading.forbidden_paths),
        }
        if include_hints:
            payload["hints"] = list(self.hints)
        return payload


class TaskSpecFile(StrictModel):
    """Envelope for a multi-task JSON benchmark file."""

    schema_version: str = TASK_SCHEMA_VERSION
    benchmark_id: str
    benchmark_version: str = "1.0"
    description: str = ""
    tasks: list[TaskSpec]

    @model_validator(mode="after")
    def _unique_keys(self) -> TaskSpecFile:
        seen: set[str] = set()
        for task in self.tasks:
            if task.key in seen:
                raise ValueError(f"duplicate task key in benchmark file: {task.key}")
            seen.add(task.key)
        return self
