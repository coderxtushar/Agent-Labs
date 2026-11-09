"""Layered grading engine.

Grading runs **after** and **independently of** the agent.  The agent cannot
influence the verdict except through the contents of the workspace: it never
reports its own success, and the hidden acceptance tests are injected only once
the patch has already been captured.

Layers, in order
----------------
1. ``environment`` - did repository setup succeed? A failure here is the task's
   problem, not the agent's.
2. ``patch`` - is there a diff at all, and does it stay out of ``forbidden_paths``?
3. ``fail_to_pass`` - do the tests that define the task now pass?
4. ``pass_to_pass`` - does the rest of the suite still pass? (regressions)
5. ``static_checks`` - optional lint/type gates; advisory unless ``required``.
6. ``judge`` - optional model-assisted review, recorded as a **separate**
   signal.

Verdict rules
-------------
``passed`` is ``True`` only when every *required* deterministic layer passed.
``passed`` is ``None`` when any layer could not produce a verdict (a crashed
runner, a malformed report, a pytest usage error); that becomes
``GRADING_ERROR`` rather than a silent failure.  The model judge can never
change ``passed`` - it is stored alongside it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from agentbench.grading.patch import (
    GRADING_INTEGRITY_PATHS,
    PatchResult,
    check_forbidden_paths,
)
from agentbench.grading.reports import (
    PytestExit,
    TestReport,
    TestStatus,
    classify_pytest_exit,
    detect_missing_dependency,
    parse_junit_xml,
)
from agentbench.logging_setup import get_logger
from agentbench.sandbox.base import ExecResult, Sandbox, SandboxError, sh_quote
from agentbench.schemas.enums import EventType, GradingLayerStatus, RunOutcome
from agentbench.schemas.task import TaskSpec, TestRunner

log = get_logger(__name__)

GRADER_IMPLEMENTATION_VERSION = "1.0"


@dataclass(slots=True)
class LayerResult:
    name: str
    status: GradingLayerStatus
    #: Only required layers contribute to the deterministic verdict.
    required: bool = True
    summary: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_pass(self) -> bool:
        return self.required and self.status is not GradingLayerStatus.PASSED

    @property
    def is_error(self) -> bool:
        return self.required and self.status is GradingLayerStatus.ERRORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": str(self.status),
            "required": self.required,
            "summary": self.summary,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class GradingOutcome:
    grader_version: str
    passed: bool | None
    score: float | None
    layers: list[LayerResult] = field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    tests_errored: int = 0
    tests_skipped: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    judge: dict[str, Any] | None = None

    @property
    def run_outcome(self) -> RunOutcome:
        """Translate the verdict into the run-level outcome."""
        if self.passed is None:
            return RunOutcome.GRADING_ERROR
        return RunOutcome.SOLVED if self.passed else RunOutcome.UNSOLVED

    def layer(self, name: str) -> LayerResult | None:
        return next((layer for layer in self.layers if layer.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader_version": self.grader_version,
            "passed": self.passed,
            "score": self.score,
            "layers": [layer.to_dict() for layer in self.layers],
            "tests": {
                "passed": self.tests_passed,
                "failed": self.tests_failed,
                "errored": self.tests_errored,
                "skipped": self.tests_skipped,
            },
            "evidence": self.evidence,
            "judge": self.judge,
        }


@dataclass(slots=True)
class EnvironmentContext:
    """What the orchestrator observed while preparing the workspace."""

    setup_ok: bool
    setup_summary: str = ""
    setup_exit_code: int | None = None
    task_validated: bool = False
    validation_note: str = ""


class GradingEngine:
    """Runs the grading layers inside an already-prepared sandbox."""

    def __init__(self, sandbox: Sandbox, task: TaskSpec, recorder: Any | None = None) -> None:
        self._sandbox = sandbox
        self._task = task
        self._recorder = recorder
        # Relative to the task workdir, not an absolute path: a sandbox provider
        # may map the workdir elsewhere, and the test runner's cwd is the only
        # location both sides agree on. Written after the patch is captured, so
        # it can never appear in the diff.
        self._report_name = f".agentbench-report-{uuid.uuid4().hex[:8]}.xml"

    # -- public ------------------------------------------------------------
    def grade(self, patch: PatchResult, env: EnvironmentContext) -> GradingOutcome:
        task = self._task
        layers: list[LayerResult] = []

        self._emit(
            EventType.EVAL_START,
            {
                "grader_version": task.grading.version,
                "grader_implementation": GRADER_IMPLEMENTATION_VERSION,
                "task_validated": env.task_validated,
            },
        )

        # 1. environment ---------------------------------------------------
        env_layer = LayerResult(
            name="environment",
            status=GradingLayerStatus.PASSED if env.setup_ok else GradingLayerStatus.ERRORED,
            summary=env.setup_summary or ("setup succeeded" if env.setup_ok else "setup failed"),
            exit_code=env.setup_exit_code,
            evidence={
                "task_validated": env.task_validated,
                "validation_note": env.validation_note,
            },
        )
        layers.append(self._emit_layer(env_layer))
        if not env.setup_ok:
            # Nothing downstream can be interpreted, so stop here with no verdict.
            return self._finish(layers, None, patch, env, stopped_at="environment")

        # 2. patch ---------------------------------------------------------
        patch_layer = self._grade_patch(patch)
        layers.append(self._emit_layer(patch_layer))
        if patch_layer.blocks_pass:
            # A missing or illegal patch is a real agent failure, so the verdict
            # is a definite False - not an error.
            return self._finish(layers, False, patch, env, stopped_at="patch")

        # 3-4. tests -------------------------------------------------------
        self._inject_acceptance_tests()

        ftp_layer, ftp_report = self._grade_test_group(
            "fail_to_pass", task.tests.fail_to_pass, required=True
        )
        layers.append(self._emit_layer(ftp_layer))

        ptp_layer, ptp_report = (
            self._grade_test_group("pass_to_pass", task.tests.pass_to_pass, required=True)
            if task.tests.pass_to_pass
            else (
                LayerResult(
                    "pass_to_pass",
                    GradingLayerStatus.SKIPPED,
                    required=False,
                    summary="task declares no pass_to_pass tests",
                ),
                TestReport(),
            )
        )
        layers.append(self._emit_layer(ptp_layer))

        # 5. static checks -------------------------------------------------
        for check in task.grading.static_checks:
            layers.append(self._emit_layer(self._grade_static_check(check)))

        verdict: bool | None
        if any(layer.is_error for layer in layers):
            verdict = None
        else:
            verdict = not any(layer.blocks_pass for layer in layers)

        outcome = self._finish(
            layers,
            verdict,
            patch,
            env,
            reports={"fail_to_pass": ftp_report, "pass_to_pass": ptp_report},
        )

        # 6. judge (separate signal, never folded into `passed`) -----------
        if task.grading.judge.enabled:
            outcome.judge = self._run_judge(outcome)
        return outcome

    # -- layers ------------------------------------------------------------
    def _grade_patch(self, patch: PatchResult) -> LayerResult:
        grading = self._task.grading
        if grading.require_non_empty_patch and patch.empty:
            return LayerResult(
                "patch",
                GradingLayerStatus.FAILED,
                summary="the agent produced no changes to the workspace",
                evidence={"files_changed": 0},
            )

        # Enforced on top of the task's own list and not overridable: a patch
        # that can change test selection or plugin loading makes the verdict
        # meaningless, so it is rejected before any test runs.
        integrity_violations = check_forbidden_paths(
            patch.modified_paths, list(GRADING_INTEGRITY_PATHS)
        )
        if integrity_violations:
            return LayerResult(
                "patch",
                GradingLayerStatus.FAILED,
                summary=(
                    "patch modifies test-runner configuration, which would invalidate "
                    f"grading: {', '.join(integrity_violations[:8])}"
                ),
                evidence={
                    "violations": integrity_violations,
                    "grading_integrity_violation": True,
                    "integrity_paths": list(GRADING_INTEGRITY_PATHS),
                    "note": (
                        "these paths are protected by the grading engine itself and cannot "
                        "be opted out of by a task; use environment.pip_packages or "
                        "environment.setup_commands for legitimate dependency changes"
                    ),
                },
            )

        violations = check_forbidden_paths(patch.modified_paths, grading.forbidden_paths)
        if violations:
            return LayerResult(
                "patch",
                GradingLayerStatus.FAILED,
                summary=f"patch modifies protected paths: {', '.join(violations[:8])}",
                evidence={"violations": violations, "forbidden_paths": grading.forbidden_paths},
            )

        return LayerResult(
            "patch",
            GradingLayerStatus.PASSED,
            summary=(
                f"{patch.files_changed} file(s) changed, +{patch.insertions}/-{patch.deletions}"
            ),
            evidence=patch.stats(),
        )

    def _grade_test_group(
        self, name: str, node_ids: list[str], *, required: bool
    ) -> tuple[LayerResult, TestReport]:
        if not node_ids:
            return (
                LayerResult(
                    name, GradingLayerStatus.SKIPPED, required=False, summary="no tests declared"
                ),
                TestReport(),
            )

        try:
            result = self._run_tests(node_ids)
        except SandboxError as exc:
            return (
                LayerResult(
                    name,
                    GradingLayerStatus.ERRORED,
                    required=required,
                    summary=f"test runner could not be executed: {exc}",
                    evidence={"error": str(exc)},
                ),
                TestReport(),
            )

        combined_output = f"{result.stdout}\n{result.stderr}"

        if self._task.tests.runner is TestRunner.COMMAND:
            status = GradingLayerStatus.PASSED if result.ok else GradingLayerStatus.FAILED
            if result.timed_out:
                status = GradingLayerStatus.ERRORED
            return (
                LayerResult(
                    name,
                    status,
                    required=required,
                    summary=f"command exited {result.exit_code}",
                    exit_code=result.exit_code,
                    duration_s=result.duration_s,
                    evidence={"output_tail": combined_output[-6000:]},
                ),
                TestReport(),
            )

        exit_kind = classify_pytest_exit(result.exit_code, result.timed_out)
        report = self._read_report()

        # An infrastructure-shaped exit means we cannot judge the patch.
        if exit_kind.is_infrastructure_problem or report.parse_error:
            missing_dep = detect_missing_dependency(combined_output)
            summary = (
                f"test runner failed to produce a usable result ({exit_kind})"
                if not report.parse_error
                else f"test report unusable: {report.parse_error}"
            )
            if missing_dep:
                summary += f"; missing dependency '{missing_dep}'"
            return (
                LayerResult(
                    name,
                    GradingLayerStatus.ERRORED,
                    required=required,
                    summary=summary,
                    exit_code=result.exit_code,
                    duration_s=result.duration_s,
                    evidence={
                        "pytest_exit": str(exit_kind),
                        "missing_dependency": missing_dep,
                        "parse_error": report.parse_error,
                        "output_tail": combined_output[-8000:],
                    },
                ),
                report,
            )

        if exit_kind is PytestExit.TIMED_OUT:
            return (
                LayerResult(
                    name,
                    GradingLayerStatus.ERRORED,
                    required=required,
                    summary="test run exceeded the task's test timeout",
                    exit_code=result.exit_code,
                    duration_s=result.duration_s,
                    evidence={"output_tail": combined_output[-4000:]},
                ),
                report,
            )

        per_node = {node: report.status_of(node) for node in node_ids}
        missing = [n for n, s in per_node.items() if s is TestStatus.MISSING]
        failing = [n for n, s in per_node.items() if s in (TestStatus.FAILED, TestStatus.ERRORED)]
        skipped = [n for n, s in per_node.items() if s is TestStatus.SKIPPED]

        if missing:
            # A declared node that the runner never reported means the task
            # selection is wrong - a grading problem, not an agent failure.
            return (
                LayerResult(
                    name,
                    GradingLayerStatus.ERRORED,
                    required=required,
                    summary=f"{len(missing)} declared test node(s) were not collected",
                    exit_code=result.exit_code,
                    duration_s=result.duration_s,
                    evidence={
                        "missing_nodes": missing,
                        "collected": sorted(report.outcomes)[:50],
                        "output_tail": combined_output[-6000:],
                    },
                ),
                report,
            )

        status = (
            GradingLayerStatus.PASSED if not failing and not skipped else GradingLayerStatus.FAILED
        )
        summary = (
            f"{report.passed} passed, {report.failed} failed, "
            f"{report.errored} errored, {report.skipped} skipped"
        )
        if failing:
            summary += f" | failing: {', '.join(failing[:5])}"
        if skipped:
            summary += f" | unexpectedly skipped: {', '.join(skipped[:5])}"

        return (
            LayerResult(
                name,
                status,
                required=required,
                summary=summary,
                exit_code=result.exit_code,
                duration_s=result.duration_s,
                evidence={
                    "per_node": {n: str(s) for n, s in per_node.items()},
                    "failing_nodes": failing,
                    "skipped_nodes": skipped,
                    "failure_messages": {n: report.message_of(n)[:2000] for n in failing[:10]},
                    "output_tail": combined_output[-6000:],
                },
            ),
            report,
        )

    def _grade_static_check(self, check: Any) -> LayerResult:
        try:
            result = self._sandbox.exec(
                check.command,
                timeout_s=self._task.limits.test_timeout_s,
                workdir=self._task.environment.workdir,
            )
        except SandboxError as exc:
            return LayerResult(
                f"static:{check.name}",
                GradingLayerStatus.ERRORED,
                required=check.required,
                summary=str(exc),
            )
        return LayerResult(
            f"static:{check.name}",
            GradingLayerStatus.PASSED if result.ok else GradingLayerStatus.FAILED,
            required=check.required,
            summary=f"{check.name} exited {result.exit_code}",
            exit_code=result.exit_code,
            duration_s=result.duration_s,
            evidence={"output_tail": f"{result.stdout}\n{result.stderr}"[-4000:]},
        )

    # -- execution helpers -------------------------------------------------
    def _inject_acceptance_tests(self) -> None:
        """Write hidden grading tests into the workspace.

        Called only after the patch has been captured, so these files can never
        appear in the agent's diff, and never exist while the agent is running.
        """
        acceptance = self._task.grading.acceptance_tests
        if not acceptance:
            return
        workdir = self._task.environment.workdir
        for rel_path, content in acceptance.items():
            self._sandbox.write_file(f"{workdir}/{rel_path}", content)
        self._emit(
            EventType.EVAL_LAYER,
            {
                "layer": "acceptance_injection",
                "files": sorted(acceptance),
                "note": "hidden grading tests injected after patch capture",
            },
        )

    def _build_test_command(self, node_ids: list[str]) -> str:
        tests = self._task.tests
        selection = " ".join(sh_quote(node) for node in node_ids)
        command = tests.command.replace("{selection}", selection)
        if "{report}" in command:
            return command.replace("{report}", sh_quote(self._report_name))
        if tests.runner is TestRunner.PYTEST:
            command = f"{command} --junitxml={sh_quote(self._report_name)}"
        return command

    def _run_tests(self, node_ids: list[str]) -> ExecResult:
        command = self._build_test_command(node_ids)
        self._sandbox.exec(
            f"rm -f {sh_quote(self._report_name)}",
            timeout_s=30,
            workdir=self._task.environment.workdir,
        )
        result = self._sandbox.exec(
            command,
            timeout_s=self._task.limits.test_timeout_s,
            workdir=self._task.environment.workdir,
        )
        if self._recorder is not None:
            self._recorder.command(result, phase="grading")
        return result

    def _read_report(self) -> TestReport:
        try:
            xml = self._sandbox.read_file(
                f"{self._task.environment.workdir}/{self._report_name}", max_bytes=4_000_000
            )
        except FileNotFoundError:
            report = TestReport()
            report.parse_error = "the test runner did not write a JUnit report"
            return report
        except SandboxError as exc:
            report = TestReport()
            report.parse_error = f"could not read the JUnit report: {exc}"
            return report
        return parse_junit_xml(xml)

    # -- judge -------------------------------------------------------------
    def _run_judge(self, outcome: GradingOutcome) -> dict[str, Any]:
        """Model-assisted review.

        No provider integration is wired in yet, so this records an explicit
        *unavailable* result rather than inventing a score.  The field exists so
        that the separation between deterministic and subjective signals is
        structural, not something a future change has to remember.
        """
        return {
            "enabled": True,
            "available": False,
            "verdict": None,
            "reason": "no judge provider configured; deterministic verdict is unaffected",
            "rubric": self._task.grading.judge.rubric[:2000],
            "model": self._task.grading.judge.model,
            "affects_pass_fail": False,
        }

    # -- assembly ----------------------------------------------------------
    def _finish(
        self,
        layers: list[LayerResult],
        verdict: bool | None,
        patch: PatchResult,
        env: EnvironmentContext,
        *,
        stopped_at: str | None = None,
        reports: dict[str, TestReport] | None = None,
    ) -> GradingOutcome:
        reports = reports or {}
        totals = {"passed": 0, "failed": 0, "errored": 0, "skipped": 0}
        for report in reports.values():
            totals["passed"] += report.passed
            totals["failed"] += report.failed
            totals["errored"] += report.errored
            totals["skipped"] += report.skipped

        required = [layer for layer in layers if layer.required]
        passed_required = [layer for layer in required if layer.status is GradingLayerStatus.PASSED]
        score = round(len(passed_required) / len(required), 4) if required else None

        outcome = GradingOutcome(
            grader_version=self._task.grading.version,
            passed=verdict,
            score=score if verdict is not None else None,
            layers=layers,
            tests_passed=totals["passed"],
            tests_failed=totals["failed"],
            tests_errored=totals["errored"],
            tests_skipped=totals["skipped"],
            evidence={
                "grader_implementation": GRADER_IMPLEMENTATION_VERSION,
                "stopped_at": stopped_at,
                "task_key": self._task.key,
                "task_validated": env.task_validated,
                "validation_note": env.validation_note,
                "patch_stats": patch.stats(),
                "required_layers": [layer.name for layer in required],
                "blocking_layers": [layer.name for layer in layers if layer.blocks_pass],
                "errored_layers": [layer.name for layer in layers if layer.is_error],
            },
        )
        self._emit(
            EventType.EVAL_END,
            {
                "passed": outcome.passed,
                "score": outcome.score,
                "layers": [{"name": layer.name, "status": str(layer.status)} for layer in layers],
                "tests": totals,
            },
        )
        return outcome

    def _emit_layer(self, layer: LayerResult) -> LayerResult:
        self._emit(
            EventType.EVAL_LAYER,
            {
                "layer": layer.name,
                "status": str(layer.status),
                "required": layer.required,
                "summary": layer.summary,
                "exit_code": layer.exit_code,
                "duration_s": round(layer.duration_s, 3),
            },
        )
        return layer

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._recorder is not None:
            self._recorder.record(event_type, payload)
