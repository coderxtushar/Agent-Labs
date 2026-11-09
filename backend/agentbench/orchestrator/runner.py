"""Execution of a single benchmark run.

This module owns the run state machine and is the only place that decides a
run's :class:`~agentbench.schemas.enums.RunOutcome`.  The rules it enforces:

* **The agent never reports its own success.**  An adapter returning
  ``COMPLETED`` only means it stopped; the verdict comes from the grading engine.
* **Failures are attributed, not merged.**  A broken task environment is
  ``INVALID_TASK``, a dead Docker daemon is ``INFRA_ERROR``, a crashed agent is
  ``AGENT_ERROR``, an exhausted budget is ``TIMEOUT``, and only a graded run is
  ``SOLVED``/``UNSOLVED``.
* **Retries never erase evidence.**  An infrastructure retry appends to
  ``run_attempt_history`` and keeps the existing event stream; it does not
  delete the failed attempt.
* **Teardown always happens.**  The sandbox and the host workspace are destroyed
  in a ``finally`` block, including on cancellation and unexpected exceptions.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.adapters.base import (
    AgentAdapterError,
    AgentRunContext,
    AgentRunResult,
    AgentStatus,
)
from agentbench.adapters.registry import UnknownAdapter, build_adapter
from agentbench.config import get_settings
from agentbench.db.models import (
    ExperimentRecord,
    GradingResultRecord,
    RunAttemptHistoryRecord,
    RunRecord,
)
from agentbench.db.session import session_scope
from agentbench.grading.engine import EnvironmentContext, GradingEngine, GradingOutcome
from agentbench.grading.patch import PatchResult
from agentbench.logging_setup import get_logger
from agentbench.metrics.failure import classify_failure, store_classification
from agentbench.orchestrator import workspace as ws
from agentbench.sandbox.base import Sandbox, SandboxError, SandboxSpec, SandboxUnavailable
from agentbench.sandbox.factory import create_sandbox
from agentbench.schemas.enums import (
    EventType,
    RunOutcome,
    RunState,
    TaskValidationStatus,
    assert_transition,
)
from agentbench.schemas.task import TaskSpec
from agentbench.trajectory.recorder import EventRecorder

log = get_logger(__name__)

_CANCEL_CHECK_INTERVAL_S = 2.0


@dataclass(slots=True)
class RunReport:
    run_id: str
    outcome: RunOutcome
    state: RunState
    reason: str
    retry_scheduled: bool = False
    grading: GradingOutcome | None = None

    @property
    def solved(self) -> bool:
        return self.outcome is RunOutcome.SOLVED


class _Cancellation:
    """Throttled cancellation probe that also refreshes the worker lease."""

    def __init__(self, run_id: str, experiment_id: str) -> None:
        self._run_id = run_id
        self._experiment_id = experiment_id
        self._last_check = 0.0
        self._cancelled = False
        self._lease_s = get_settings().run_lease_seconds

    def __call__(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if now - self._last_check < _CANCEL_CHECK_INTERVAL_S:
            return False
        self._last_check = now
        try:
            with session_scope() as session:
                experiment = session.get(ExperimentRecord, self._experiment_id)
                run = session.get(RunRecord, self._run_id)
                if experiment is not None and experiment.cancel_requested:
                    self._cancelled = True
                if run is not None:
                    if run.state == RunState.CANCELLED:
                        self._cancelled = True
                    else:
                        stamp = datetime.now(UTC)
                        run.heartbeat_at = stamp
                        run.claim_expires_at = stamp + timedelta(seconds=self._lease_s)
        except Exception as exc:  # pragma: no cover - never let a probe kill a run
            log.warning(
                "run.cancel_probe_failed", extra={"run_id": self._run_id, "error": str(exc)}
            )
        return self._cancelled


class RunExecutor:
    """Executes one claimed run to a terminal state."""

    def __init__(self, run_id: str, worker_id: str = "local") -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self._settings = get_settings()

    # -- public ------------------------------------------------------------
    def execute(self) -> RunReport:
        started = time.monotonic()
        with session_scope() as session:
            run = session.get(RunRecord, self.run_id)
            if run is None:
                raise LookupError(f"run not found: {self.run_id}")
            if RunState(run.state).is_terminal:
                return RunReport(
                    run.id,
                    RunOutcome(run.outcome or RunOutcome.CANCELLED),
                    RunState(run.state),
                    "run already terminal",
                )

            recorder = EventRecorder(session, run.id)
            try:
                task = TaskSpec.model_validate(run.task_snapshot)
            except Exception as exc:
                recorder.error("task snapshot could not be validated", detail=str(exc))
                return self._terminate(
                    session,
                    run,
                    recorder,
                    RunOutcome.INVALID_TASK,
                    f"stored task snapshot is not a valid TaskSpec: {exc}",
                    started,
                )

            experiment_id = run.experiment_id
            agent_snapshot = dict(run.agent_snapshot or {})
            cancellation = _Cancellation(run.id, experiment_id)

            recorder.record(
                EventType.RUN_CLAIMED,
                {
                    "worker_id": self.worker_id,
                    "task_key": run.task_key,
                    "attempt_index": run.attempt_index,
                    "infra_attempts": run.infra_attempts,
                    "adapter": agent_snapshot.get("adapter"),
                },
            )

            sandbox: Sandbox | None = None
            workspace: ws.PreparedWorkspace | None = None
            timings: dict[str, float] = {}

            try:
                # -- prepare ------------------------------------------------
                self._advance(session, run, RunState.PREPARING)
                t0 = time.monotonic()
                try:
                    workspace = ws.materialize_repository(task, run.id)
                except ws.TaskEnvironmentError as exc:
                    recorder.error("repository could not be materialised", detail=str(exc))
                    return self._terminate(
                        session, run, recorder, RunOutcome.INVALID_TASK, str(exc), started, timings
                    )

                sandbox_spec = self._sandbox_spec(task, run)
                recorder.record(
                    EventType.SANDBOX_CREATE,
                    {
                        "provider": self._settings.sandbox_provider,
                        "image": sandbox_spec.image,
                        "memory_mb": sandbox_spec.memory_mb,
                        "cpus": sandbox_spec.cpus,
                        "network": str(sandbox_spec.network),
                    },
                )
                sandbox = create_sandbox(sandbox_spec, run.id)
                sandbox.start()
                run.sandbox_id = sandbox.sandbox_id
                run.sandbox_provider = sandbox.provider
                recorder.record(
                    EventType.SANDBOX_READY,
                    {"sandbox_id": sandbox.sandbox_id, "provider": sandbox.provider},
                )

                ws.upload_workspace(sandbox, workspace, task)
                timings["prepare_s"] = round(time.monotonic() - t0, 3)

                t0 = time.monotonic()
                setup = ws.run_setup(sandbox, task, recorder)
                timings["setup_s"] = round(time.monotonic() - t0, 3)
                if not setup.ok:
                    return self._terminate(
                        session,
                        run,
                        recorder,
                        RunOutcome.INVALID_TASK,
                        setup.summary,
                        started,
                        timings,
                    )

                if cancellation():
                    return self._terminate(
                        session,
                        run,
                        recorder,
                        RunOutcome.CANCELLED,
                        "cancelled before the agent started",
                        started,
                        timings,
                    )

                # -- agent --------------------------------------------------
                self._advance(session, run, RunState.AGENT_RUNNING)
                run.started_at = run.started_at or datetime.now(UTC)
                session.flush()

                t0 = time.monotonic()
                agent_result = self._run_agent(
                    task, run, agent_snapshot, sandbox, recorder, cancellation
                )
                timings["agent_s"] = round(time.monotonic() - t0, 3)

                self._store_agent_result(run, agent_result)

                # -- collect ------------------------------------------------
                self._advance(session, run, RunState.COLLECTING)
                t0 = time.monotonic()
                patch = ws.capture_patch(sandbox, workspace, task, recorder)
                run.patch = patch.diff or None
                run.patch_stats = patch.stats()
                ws.write_patch_artifact(run.id, patch)
                timings["collect_s"] = round(time.monotonic() - t0, 3)

                # A non-completed agent is terminal: the patch is kept as
                # evidence, but grading it would mislabel the failure.
                early = self._outcome_for_agent_status(agent_result)
                if early is not None:
                    return self._terminate(
                        session, run, recorder, early, agent_result.reason, started, timings
                    )

                # -- grade --------------------------------------------------
                self._advance(session, run, RunState.GRADING)
                t0 = time.monotonic()
                grading = self._grade(session, run, task, sandbox, recorder, patch, setup)
                timings["grading_s"] = round(time.monotonic() - t0, 3)

                report = self._terminate(
                    session,
                    run,
                    recorder,
                    grading.run_outcome,
                    self._verdict_reason(grading),
                    started,
                    timings,
                    grading=grading,
                )
                return report

            except SandboxUnavailable as exc:
                recorder.error("sandbox provider unavailable", detail=str(exc))
                return self._handle_infra_failure(
                    session, run, recorder, str(exc), started, timings
                )
            except SandboxError as exc:
                recorder.error("sandbox failure", detail=str(exc))
                return self._handle_infra_failure(
                    session, run, recorder, str(exc), started, timings
                )
            except AgentAdapterError as exc:
                recorder.error("agent adapter failure", detail=str(exc))
                return self._terminate(
                    session, run, recorder, RunOutcome.AGENT_ERROR, str(exc), started, timings
                )
            except Exception as exc:  # noqa: BLE001 - last resort, never lose the run
                detail = f"{type(exc).__name__}: {exc}"
                recorder.error(
                    "unexpected orchestrator failure",
                    detail=detail,
                    traceback=traceback.format_exc()[-6000:],
                )
                log.exception("run.unexpected_failure", extra={"run_id": run.id})
                return self._handle_infra_failure(session, run, recorder, detail, started, timings)
            finally:
                if sandbox is not None:
                    recorder.record(EventType.SANDBOX_DESTROY, {"sandbox_id": sandbox.sandbox_id})
                    sandbox.destroy()
                if workspace is not None:
                    workspace.cleanup()

    # -- phases ------------------------------------------------------------
    def _sandbox_spec(self, task: TaskSpec, run: RunRecord) -> SandboxSpec:
        limits = task.limits
        return SandboxSpec(
            image=task.environment.image,
            workdir=task.environment.workdir,
            memory_mb=limits.memory_mb,
            cpus=limits.cpus,
            disk_mb=limits.disk_mb,
            network=limits.network,
            env=dict(task.environment.env),
            labels={"task_id": task.task_id, "experiment_id": run.experiment_id},
        )

    def _run_agent(
        self,
        task: TaskSpec,
        run: RunRecord,
        agent_snapshot: dict[str, Any],
        sandbox: Sandbox,
        recorder: EventRecorder,
        cancellation: _Cancellation,
    ) -> AgentRunResult:
        adapter_key = agent_snapshot.get("adapter")
        if not adapter_key:
            raise AgentAdapterError("agent snapshot does not name an adapter")
        try:
            adapter = build_adapter(adapter_key, agent_snapshot.get("config") or {})
        except UnknownAdapter as exc:
            raise AgentAdapterError(str(exc)) from exc

        include_hints = bool(agent_snapshot.get("include_hints", False))
        timeout = min(task.limits.agent_timeout_s, self._settings.max_agent_timeout_s)
        brief = task.agent_visible_payload(include_hints=include_hints)

        ctx = AgentRunContext(
            run_id=run.id,
            sandbox=sandbox,
            task=task,
            brief=brief,
            workdir=task.environment.workdir,
            events=recorder,
            deadline=time.monotonic() + timeout,
            max_steps=task.limits.max_agent_steps,
            config=dict(agent_snapshot.get("config") or {}),
            sampling=dict(agent_snapshot.get("sampling") or {}),
            seed=run.seed,
            is_cancelled=cancellation,
        )
        recorder.record(
            EventType.TASK_CONTEXT_SUPPLIED,
            {
                "hints_included": include_hints,
                "brief_keys": sorted(brief),
                "agent_timeout_s": timeout,
                "max_steps": ctx.max_steps,
                "note": "hidden acceptance tests are not present in the workspace at this point",
            },
        )
        return adapter.run(ctx)

    def _grade(
        self,
        session: Session,
        run: RunRecord,
        task: TaskSpec,
        sandbox: Sandbox,
        recorder: EventRecorder,
        patch: PatchResult,
        setup: ws.SetupResult,
    ) -> GradingOutcome:
        validated = run.task_validation_status == TaskValidationStatus.VALID
        note = (
            ""
            if validated
            else (
                f"task validation status at run creation was "
                f"'{run.task_validation_status}', so its baseline falsifiability is unverified"
            )
        )
        env = EnvironmentContext(
            setup_ok=setup.ok,
            setup_summary=setup.summary,
            setup_exit_code=setup.exit_code,
            task_validated=validated,
            validation_note=note,
        )
        engine = GradingEngine(sandbox, task, recorder)
        outcome = engine.grade(patch, env)

        existing = session.execute(
            select(GradingResultRecord).where(GradingResultRecord.run_id == run.id)
        ).scalar_one_or_none()
        record = existing or GradingResultRecord(run_id=run.id)
        record.grader_version = outcome.grader_version
        record.passed = outcome.passed
        record.score = outcome.score
        record.tests_passed = outcome.tests_passed
        record.tests_failed = outcome.tests_failed
        record.tests_errored = outcome.tests_errored
        record.tests_skipped = outcome.tests_skipped
        record.layers = [layer.to_dict() for layer in outcome.layers]
        record.evidence = outcome.evidence
        record.judge = outcome.judge
        if existing is None:
            session.add(record)
        session.flush()
        return outcome

    # -- outcomes ----------------------------------------------------------
    @staticmethod
    def _outcome_for_agent_status(result: AgentRunResult) -> RunOutcome | None:
        mapping = {
            AgentStatus.TIMEOUT: RunOutcome.TIMEOUT,
            AgentStatus.CANCELLED: RunOutcome.CANCELLED,
            AgentStatus.ERROR: RunOutcome.AGENT_ERROR,
        }
        return mapping.get(result.status)

    @staticmethod
    def _verdict_reason(grading: GradingOutcome) -> str:
        if grading.passed is True:
            return "all required grading layers passed"
        if grading.passed is False:
            blocking = grading.evidence.get("blocking_layers") or []
            failing = ", ".join(str(name) for name in blocking) or "unknown layer"
            return f"grading failed at: {failing}"
        errored = grading.evidence.get("errored_layers") or []
        return "grading could not reach a verdict; errored layers: " + (
            ", ".join(str(name) for name in errored) or "unknown"
        )

    def _store_agent_result(self, run: RunRecord, result: AgentRunResult) -> None:
        usage = result.usage.to_dict()
        usage["steps"] = result.steps
        usage["agent_status"] = str(result.status)
        run.usage = usage
        run.cost_usd = result.cost_usd
        run.cost_source = result.cost_source
        snapshot = dict(run.agent_snapshot or {})
        snapshot["observed_model"] = result.model or snapshot.get("model")
        snapshot["observed_provider"] = result.provider or snapshot.get("provider")
        snapshot["agent_metadata"] = result.metadata
        run.agent_snapshot = snapshot

    # -- state -------------------------------------------------------------
    @staticmethod
    def _advance(session: Session, run: RunRecord, target: RunState) -> None:
        assert_transition(RunState(run.state), target)
        run.state = target
        session.flush()

    def _terminate(
        self,
        session: Session,
        run: RunRecord,
        recorder: EventRecorder,
        outcome: RunOutcome,
        reason: str,
        started: float,
        timings: dict[str, float] | None = None,
        grading: GradingOutcome | None = None,
    ) -> RunReport:
        state = RunState.CANCELLED if outcome is RunOutcome.CANCELLED else RunState.COMPLETED
        current = RunState(run.state)
        if current is not state:
            if state is RunState.COMPLETED and current is not RunState.FINALIZING:
                # Jump straight to the terminal state; every intermediate state
                # legally transitions to COMPLETED.
                assert_transition(current, RunState.COMPLETED)
            elif state is RunState.CANCELLED:
                assert_transition(current, RunState.CANCELLED)
        run.state = state
        run.outcome = outcome
        run.termination_reason = reason[:4000]
        run.finished_at = datetime.now(UTC)
        merged = dict(run.timings or {})
        merged.update(timings or {})
        merged["total_s"] = round(time.monotonic() - started, 3)
        run.timings = merged
        run.claimed_by = None
        run.claim_expires_at = None
        session.flush()

        recorder.record(
            EventType.RUN_FINALIZED if state is RunState.COMPLETED else EventType.RUN_TERMINATED,
            {
                "outcome": str(outcome),
                "reason": reason[:2000],
                "timings": merged,
                "counts_toward_success_rate": outcome.counts_toward_success_rate,
                "attributable_to_agent": outcome.is_attributable_to_agent,
            },
        )

        if outcome is not RunOutcome.SOLVED:
            classification = classify_failure(session, run, grading)
            store_classification(session, run.id, classification)

        log.info(
            "run.finished",
            extra={
                "run_id": run.id,
                "task_key": run.task_key,
                "outcome": str(outcome),
                "timings": merged,
            },
        )
        return RunReport(run.id, outcome, state, reason, grading=grading)

    def _handle_infra_failure(
        self,
        session: Session,
        run: RunRecord,
        recorder: EventRecorder,
        reason: str,
        started: float,
        timings: dict[str, float] | None = None,
    ) -> RunReport:
        """Record an infrastructure failure, retrying if budget remains.

        The failed attempt is written to ``run_attempt_history`` first, so a
        retry can never make the original failure disappear.
        """
        limit = self._settings.infra_retry_limit
        attempt_no = run.infra_attempts + 1

        session.add(
            RunAttemptHistoryRecord(
                run_id=run.id,
                attempt_no=attempt_no,
                outcome=str(RunOutcome.INFRA_ERROR),
                reason=reason[:4000],
                detail={"timings": timings or {}, "worker_id": self.worker_id},
            )
        )
        run.infra_attempts = attempt_no
        run.error_message = reason[:4000]

        if attempt_no <= limit:
            recorder.record(
                EventType.RETRY,
                {
                    "scope": "run",
                    "attempt": attempt_no,
                    "of": limit,
                    "reason": reason[:2000],
                    "note": "infrastructure failure; the failed attempt is retained in history",
                },
            )
            run.state = RunState.PENDING
            run.claimed_by = None
            run.claimed_at = None
            run.claim_expires_at = None
            run.sandbox_id = None
            session.flush()
            log.warning(
                "run.infra_retry_scheduled",
                extra={"run_id": run.id, "attempt": attempt_no, "limit": limit},
            )
            return RunReport(
                run.id, RunOutcome.INFRA_ERROR, RunState.PENDING, reason, retry_scheduled=True
            )

        return self._terminate(
            session,
            run,
            recorder,
            RunOutcome.INFRA_ERROR,
            f"infrastructure failure after {attempt_no} attempt(s): {reason}",
            started,
            timings,
        )
