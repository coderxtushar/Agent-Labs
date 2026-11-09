"""Workspace preparation and patch capture.

Lifecycle of a workspace
------------------------
1. **Materialise** the repository at its pinned revision on the host, into a
   per-run directory.  Local-path repos are copied; git repos are cloned into a
   revision-keyed cache and then copied, so the same revision is fetched once.
2. **Snapshot** that pristine tree.  This snapshot is the ``before`` side of the
   diff and never leaves the host.
3. **Upload** it into the sandbox and run the task's setup commands.
4. Hand the sandbox to the agent.
5. **Download** the workspace and diff it against the snapshot.

Why the diff is computed on the host: the sandbox image needs no ``git``, the
agent cannot influence the baseline, and new/deleted files are captured the same
way as edits.

Separation of concerns: the acceptance tests are *not* present at any point in
this module.  They are injected by the grading engine after
:func:`capture_patch` has already run, so they can never appear in the diff or
be read by the agent.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentbench.config import get_settings
from agentbench.grading.patch import DEFAULT_IGNORE, PatchResult, compute_patch
from agentbench.logging_setup import get_logger
from agentbench.sandbox.base import ExecResult, Sandbox, SandboxError, sh_quote
from agentbench.schemas.enums import EventType
from agentbench.schemas.task import GitRepoSource, LocalRepoSource, TaskSpec

log = get_logger(__name__)


class WorkspaceError(SandboxError):
    """Repository or workspace preparation failed.

    Subclasses :class:`SandboxError` so the orchestrator treats it as an
    infrastructure problem by default; a *task*-caused failure is reported
    through :class:`SetupResult` instead.
    """


class TaskEnvironmentError(RuntimeError):
    """The task's own environment definition is broken (``INVALID_TASK``)."""


@dataclass(slots=True)
class SetupResult:
    ok: bool
    summary: str
    exit_code: int | None = None
    results: list[ExecResult] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.results is None:
            self.results = []


@dataclass(slots=True)
class PreparedWorkspace:
    """Host-side state for one run's workspace."""

    root: Path
    #: Pristine copy used as the diff baseline.
    baseline: Path
    repo_identity: str
    revision: str
    file_count: int

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# materialise
# --------------------------------------------------------------------------- #
def _resolve_local_repo(source: LocalRepoSource) -> Path:
    settings = get_settings()
    root = settings.benchmarks_dir.resolve()
    candidate = (root / source.path).resolve()
    if root not in candidate.parents and candidate != root:
        raise TaskEnvironmentError(
            f"repository path '{source.path}' resolves outside the benchmarks directory"
        )
    if not candidate.is_dir():
        raise TaskEnvironmentError(f"repository path does not exist: {source.path}")
    return candidate


def _clone_git_repo(source: GitRepoSource) -> Path:
    """Clone into a revision-keyed cache directory.

    ``git clone`` does not execute repository code, and hooks are not installed
    for a fresh clone, so this is safe to do on the host.  Building or testing
    the repository only ever happens inside a sandbox.
    """
    settings = get_settings()
    digest = hashlib.sha256(f"{source.url}@{source.revision}".encode()).hexdigest()[:16]
    cache = settings.data_dir / "repo_cache" / digest
    marker = cache / ".agentbench-complete"

    if marker.is_file():
        return cache

    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(  # noqa: S603 - fixed argv
            ["git", "init", "--quiet"], cwd=cache, check=True, capture_output=True, timeout=120
        )
        subprocess.run(  # noqa: S603
            ["git", "remote", "add", "origin", source.url],
            cwd=cache,
            check=True,
            capture_output=True,
            timeout=120,
        )
        subprocess.run(  # noqa: S603
            ["git", "fetch", "--depth", "1", "--quiet", "origin", source.revision],
            cwd=cache,
            check=True,
            capture_output=True,
            timeout=900,
        )
        subprocess.run(  # noqa: S603
            ["git", "checkout", "--quiet", "FETCH_HEAD"],
            cwd=cache,
            check=True,
            capture_output=True,
            timeout=300,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("git is not installed on the host; cannot clone git tasks") from exc
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(cache, ignore_errors=True)
        raise WorkspaceError(f"git clone timed out for {source.url}@{source.revision}") from exc
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(cache, ignore_errors=True)
        stderr = (exc.stderr or b"").decode(errors="replace")[:800]
        raise TaskEnvironmentError(
            f"could not fetch {source.url} at revision '{source.revision}': {stderr}"
        ) from exc

    shutil.rmtree(cache / ".git", ignore_errors=True)
    marker.write_text("ok", encoding="utf-8")
    return cache


def materialize_repository(task: TaskSpec, run_id: str) -> PreparedWorkspace:
    """Create the host-side pristine copy for a run."""
    settings = get_settings()
    source = task.repo

    if isinstance(source, LocalRepoSource):
        origin = _resolve_local_repo(source)
        identity = f"local:{source.path}"
    else:
        origin = _clone_git_repo(source)
        identity = f"git:{source.url}"

    if source.subdir:
        origin = (origin / source.subdir).resolve()
        if not origin.is_dir():
            raise TaskEnvironmentError(f"repository subdir does not exist: {source.subdir}")

    root = Path(tempfile.mkdtemp(prefix=f"ab-ws-{run_id[:8]}-", dir=settings.workspaces_dir))
    baseline = root / "baseline"
    shutil.copytree(
        origin,
        baseline,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", ".git", ".ruff_cache", ".mypy_cache", ".venv"
        ),
        symlinks=False,
    )

    file_count = sum(1 for p in baseline.rglob("*") if p.is_file())
    if file_count == 0:
        shutil.rmtree(root, ignore_errors=True)
        raise TaskEnvironmentError(f"repository '{identity}' materialised with no files")

    log.info(
        "workspace.materialized",
        extra={"run_id": run_id, "repo": identity, "files": file_count, "root": str(root)},
    )
    return PreparedWorkspace(root, baseline, identity, source.revision, file_count)


# --------------------------------------------------------------------------- #
# upload + setup
# --------------------------------------------------------------------------- #
def upload_workspace(sandbox: Sandbox, workspace: PreparedWorkspace, task: TaskSpec) -> None:
    sandbox.copy_in(workspace.baseline, task.environment.workdir)
    # Verified relative to the working directory, not by absolute path: a
    # provider is free to map the task's workdir somewhere else (the host-local
    # provider puts it under a temporary root), so an absolute `test -d` would
    # only ever pass for containers.
    probe = sandbox.exec("ls -1A", timeout_s=60, workdir=task.environment.workdir)
    if not probe.ok:
        raise WorkspaceError(f"workspace upload could not be verified: {probe.stderr[:500]}")
    if not probe.stdout.strip():
        raise WorkspaceError(
            f"workspace at {task.environment.workdir} is empty after upload of "
            f"{workspace.file_count} file(s) from {workspace.repo_identity}"
        )


def run_setup(sandbox: Sandbox, task: TaskSpec, recorder: object | None = None) -> SetupResult:
    """Run the task's setup commands.

    A non-zero exit here is the *task's* fault, so it is reported as a failed
    :class:`SetupResult` (leading to ``INVALID_TASK``), not raised as an
    infrastructure error.
    """
    settings = get_settings()
    timeout = min(task.limits.setup_timeout_s, settings.max_setup_timeout_s)
    commands: list[str] = []

    if task.environment.pip_packages:
        packages = " ".join(sh_quote(pkg) for pkg in task.environment.pip_packages)
        commands.append(f"python -m pip install --no-input --disable-pip-version-check {packages}")
    commands.extend(task.environment.setup_commands)

    if recorder is not None:
        recorder.record(  # type: ignore[attr-defined]
            EventType.ENV_SETUP_START,
            {"command_count": len(commands), "timeout_s": timeout, "image": task.environment.image},
        )

    results: list[ExecResult] = []
    for command in commands:
        result = sandbox.exec(
            command, timeout_s=timeout, workdir=task.environment.workdir, env=task.environment.env
        )
        results.append(result)
        if recorder is not None:
            recorder.command(result, phase="setup")  # type: ignore[attr-defined]
        if not result.ok:
            summary = (
                f"setup command failed (exit {result.exit_code}"
                f"{', timed out' if result.timed_out else ''}): {command}"
            )
            if recorder is not None:
                recorder.record(  # type: ignore[attr-defined]
                    EventType.ENV_SETUP_END,
                    {"ok": False, "summary": summary, "stderr_tail": result.stderr[-4000:]},
                )
            return SetupResult(False, summary, result.exit_code, results)

    summary = f"{len(commands)} setup command(s) succeeded"
    if recorder is not None:
        recorder.record(EventType.ENV_SETUP_END, {"ok": True, "summary": summary})  # type: ignore[attr-defined]
    return SetupResult(True, summary, 0, results)


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #
def capture_patch(
    sandbox: Sandbox,
    workspace: PreparedWorkspace,
    task: TaskSpec,
    recorder: object | None = None,
) -> PatchResult:
    """Download the workspace and diff it against the pristine baseline."""
    after = workspace.root / "after"
    try:
        sandbox.copy_out(task.environment.workdir, after)
    except SandboxError as exc:
        raise WorkspaceError(f"could not download the workspace for diffing: {exc}") from exc

    patch = compute_patch(workspace.baseline, after, ignore=DEFAULT_IGNORE)
    if recorder is not None:
        recorder.record(  # type: ignore[attr-defined]
            EventType.PATCH_GENERATED,
            {
                "empty": patch.empty,
                "files_changed": patch.files_changed,
                "insertions": patch.insertions,
                "deletions": patch.deletions,
                "paths": patch.modified_paths[:50],
                "diff_truncated": patch.truncated,
            },
        )
    log.info(
        "workspace.patch_captured",
        extra={
            "repo": workspace.repo_identity,
            "files_changed": patch.files_changed,
            "empty": patch.empty,
        },
    )
    return patch


def write_patch_artifact(run_id: str, patch: PatchResult) -> Path | None:
    """Persist the diff next to the run's other artifacts."""
    if patch.empty:
        return None
    settings = get_settings()
    directory = settings.artifacts_dir / run_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "patch.diff"
    path.write_text(patch.diff, encoding="utf-8")
    return path
