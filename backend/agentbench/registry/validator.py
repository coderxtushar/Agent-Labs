"""Task validation.

A task description existing is not evidence that the task works.  Validation
builds the task's real environment and answers four questions that cannot be
answered by reading the spec:

1. **Can the environment be prepared?**  Image present, repository
   materialisable, setup commands exit 0.
2. **Are the graded tests actually collected?**  Every declared node ID must be
   reported by the runner. A typo in a node ID would otherwise silently make a
   task unpassable.
3. **Is the task falsifiable?**  Every ``fail_to_pass`` test must *fail* on the
   pristine repository. If they already pass, the task is either already solved
   or mis-specified, and any agent would score a free success.
4. **Is the baseline healthy?**  Every ``pass_to_pass`` test must pass on the
   pristine repository, otherwise "regression" can never be distinguished from
   "already broken".

Validation runs in its own throwaway sandbox and injects the hidden acceptance
tests there.  That workspace is destroyed afterwards, so nothing leaks into a
later agent run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agentbench.config import get_settings
from agentbench.grading.patch import check_forbidden_paths
from agentbench.grading.reports import TestStatus, classify_pytest_exit, parse_junit_xml
from agentbench.logging_setup import get_logger
from agentbench.orchestrator import workspace as ws
from agentbench.sandbox.base import SandboxError, SandboxSpec, sh_quote
from agentbench.sandbox.factory import create_sandbox, image_present
from agentbench.schemas.enums import TaskValidationStatus
from agentbench.schemas.task import TaskSpec, TestRunner

log = get_logger(__name__)

REPORT_NAME = ".agentbench-validate.xml"


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "data": self.data}


@dataclass(slots=True)
class ValidationReport:
    task_key: str
    status: TaskValidationStatus
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sandbox_provider: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is TaskValidationStatus.VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_key": self.task_key,
            "status": str(self.status),
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "warnings": self.warnings,
            "validated_at": self.validated_at,
            "sandbox_provider": self.sandbox_provider,
            "failed_checks": [check.name for check in self.checks if not check.ok],
        }


def static_checks(task: TaskSpec) -> list[Check]:
    """Checks that need no sandbox. Run first so obvious problems fail fast."""
    checks: list[Check] = []

    unprotected = [
        path
        for path in task.grading.acceptance_tests
        if not check_forbidden_paths([path], task.grading.forbidden_paths)
    ]
    checks.append(
        Check(
            "acceptance_tests_protected",
            not unprotected,
            (
                "all hidden acceptance test paths are covered by forbidden_paths"
                if not unprotected
                else f"acceptance tests the agent could overwrite: {unprotected}"
            ),
            {"unprotected": unprotected},
        )
    )

    declared_files = {node.split("::")[0] for node in task.tests.fail_to_pass}
    acceptance_files = set(task.grading.acceptance_tests)
    orphan_acceptance = sorted(acceptance_files - declared_files)
    checks.append(
        Check(
            "acceptance_tests_referenced",
            not orphan_acceptance,
            (
                "every injected acceptance file is referenced by fail_to_pass"
                if not orphan_acceptance
                else f"injected but never graded: {orphan_acceptance}"
            ),
            {"orphans": orphan_acceptance},
        )
    )

    hints_leak = [
        hint
        for hint in task.hints
        if hint and hint.lower() in (task.problem_statement or "").lower()
    ]
    checks.append(
        Check(
            "hints_not_in_problem_statement",
            not hints_leak,
            "hints are kept out of the agent-visible problem statement"
            if not hints_leak
            else "a hint is duplicated verbatim in the problem statement",
            {"duplicated": hints_leak},
        )
    )
    return checks


def validate_task(task: TaskSpec, *, run_id: str | None = None) -> ValidationReport:
    """Build the task's environment and check it end to end."""
    settings = get_settings()
    report = ValidationReport(task.key, TaskValidationStatus.VALIDATING)
    report.checks.extend(static_checks(task))
    report.sandbox_provider = settings.sandbox_provider
    identifier = run_id or f"validate-{task.task_id[:16]}"

    if getattr(task.repo, "kind", "") == "git" and not getattr(
        task.repo, "revision_is_immutable", False
    ):
        report.warnings.append(
            f"repository revision '{task.repo.revision}' is not a full commit SHA, so the "
            "task is not reproducible over time"
        )

    if settings.sandbox_provider == "docker" and not image_present(task.environment.image):
        report.checks.append(
            Check(
                "image_present",
                False,
                f"image '{task.environment.image}' is not available locally; "
                f"run `docker pull {task.environment.image}` or build it first",
            )
        )
        report.status = TaskValidationStatus.INVALID
        return report
    report.checks.append(
        Check("image_present", True, f"image '{task.environment.image}' is available")
    )

    workspace = None
    sandbox = None
    try:
        try:
            workspace = ws.materialize_repository(task, identifier)
        except ws.TaskEnvironmentError as exc:
            report.checks.append(Check("repository_materialised", False, str(exc)))
            report.status = TaskValidationStatus.INVALID
            return report
        report.checks.append(
            Check(
                "repository_materialised",
                True,
                f"{workspace.file_count} file(s) from {workspace.repo_identity} "
                f"at revision '{workspace.revision}'",
                {"files": workspace.file_count, "repo": workspace.repo_identity},
            )
        )

        spec = SandboxSpec(
            image=task.environment.image,
            workdir=task.environment.workdir,
            memory_mb=task.limits.memory_mb,
            cpus=task.limits.cpus,
            disk_mb=task.limits.disk_mb,
            network=task.limits.network,
            env=dict(task.environment.env),
            labels={"purpose": "validation", "task_id": task.task_id},
        )
        sandbox = create_sandbox(spec, identifier)
        sandbox.start()
        ws.upload_workspace(sandbox, workspace, task)

        setup = ws.run_setup(sandbox, task)
        report.checks.append(
            Check(
                "setup_commands",
                setup.ok,
                setup.summary,
                {"exit_code": setup.exit_code},
            )
        )
        if not setup.ok:
            report.status = TaskValidationStatus.INVALID
            return report

        for rel_path, content in task.grading.acceptance_tests.items():
            sandbox.write_file(f"{task.environment.workdir}/{rel_path}", content)

        if task.tests.runner is not TestRunner.PYTEST:
            report.warnings.append(
                "baseline falsifiability cannot be checked for non-pytest runners; only the "
                "environment was validated"
            )
            report.status = TaskValidationStatus.VALID
            return report

        baseline = _run_baseline(sandbox, task)
        if baseline is None:
            report.checks.append(
                Check(
                    "baseline_report_usable",
                    False,
                    "the test runner produced no usable JUnit report on the pristine repository",
                )
            )
            report.status = TaskValidationStatus.INVALID
            return report
        report.checks.append(
            Check(
                "baseline_report_usable",
                True,
                f"collected {len(baseline.outcomes)} test(s) on the pristine repository",
            )
        )

        declared = list(task.tests.fail_to_pass) + list(task.tests.pass_to_pass)
        missing = [node for node in declared if baseline.status_of(node) is TestStatus.MISSING]
        report.checks.append(
            Check(
                "declared_tests_collected",
                not missing,
                "every declared test node was collected"
                if not missing
                else f"{len(missing)} declared node(s) were never collected: {missing[:6]}",
                {"missing": missing, "collected_sample": sorted(baseline.outcomes)[:40]},
            )
        )

        already_passing = [
            node
            for node in task.tests.fail_to_pass
            if baseline.status_of(node) is TestStatus.PASSED
        ]
        report.checks.append(
            Check(
                "fail_to_pass_fails_on_baseline",
                not already_passing and not missing,
                (
                    f"all {len(task.tests.fail_to_pass)} target test(s) fail before the fix, "
                    "so the task is falsifiable"
                )
                if not already_passing and not missing
                else (
                    f"{len(already_passing)} target test(s) already pass on the pristine "
                    f"repository: {already_passing[:6]}"
                ),
                {"already_passing": already_passing},
            )
        )

        broken_baseline = [
            node
            for node in task.tests.pass_to_pass
            if baseline.status_of(node) is not TestStatus.PASSED
        ]
        report.checks.append(
            Check(
                "pass_to_pass_passes_on_baseline",
                not broken_baseline,
                f"all {len(task.tests.pass_to_pass)} regression guard(s) pass before the fix"
                if not broken_baseline
                else f"regression guard(s) already failing: {broken_baseline[:6]}",
                {
                    "failing": broken_baseline,
                    "statuses": {
                        node: str(baseline.status_of(node)) for node in broken_baseline[:10]
                    },
                },
            )
        )

        if not task.tests.pass_to_pass:
            report.warnings.append(
                "the task declares no pass_to_pass tests, so regressions cannot be detected"
            )

        report.status = (
            TaskValidationStatus.VALID
            if all(check.ok for check in report.checks)
            else TaskValidationStatus.INVALID
        )
        return report

    except SandboxError as exc:
        report.checks.append(Check("sandbox", False, f"sandbox failure during validation: {exc}"))
        report.status = TaskValidationStatus.ERROR
        return report
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            Check("validator", False, f"unexpected validator failure: {type(exc).__name__}: {exc}")
        )
        report.status = TaskValidationStatus.ERROR
        log.exception("validator.unexpected_failure", extra={"task_key": task.key})
        return report
    finally:
        if sandbox is not None:
            sandbox.destroy()
        if workspace is not None:
            workspace.cleanup()


def _run_baseline(sandbox: Any, task: TaskSpec) -> Any:
    """Run every declared node on the pristine repository and parse the report."""
    nodes = list(task.tests.fail_to_pass) + list(task.tests.pass_to_pass)
    selection = " ".join(sh_quote(node) for node in nodes)
    command = task.tests.command.replace("{selection}", selection)
    if "{report}" in command:
        command = command.replace("{report}", sh_quote(REPORT_NAME))
    else:
        command = f"{command} --junitxml={sh_quote(REPORT_NAME)}"
    # A failing baseline is the expected case, so a non-zero exit is not an error
    # here; only an unreadable report is.
    command = f"{command} || true"

    sandbox.exec(
        f"rm -f {sh_quote(REPORT_NAME)}",
        timeout_s=30,
        workdir=task.environment.workdir,
    )
    result = sandbox.exec(
        command, timeout_s=task.limits.test_timeout_s, workdir=task.environment.workdir
    )
    kind = classify_pytest_exit(result.exit_code, result.timed_out)
    log.info(
        "validator.baseline_run",
        extra={"task_key": task.key, "exit": result.exit_code, "kind": str(kind)},
    )
    try:
        xml = sandbox.read_file(f"{task.environment.workdir}/{REPORT_NAME}", max_bytes=4_000_000)
    except (FileNotFoundError, SandboxError):
        return None
    report = parse_junit_xml(xml)
    return None if report.parse_error else report
