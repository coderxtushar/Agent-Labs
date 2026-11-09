"""Experiment lifecycle.

An experiment is the unit of reproducibility: benchmark version, task set, agent
adapter and version, model, prompt/tool versions, sampling parameters, limits,
repetitions, seeds and grading version are all frozen into
``config_snapshot`` when it launches.  Editing the agent record afterwards
cannot retroactively change what a completed experiment measured.

Resumability
------------
Runs are materialised as database rows *before* any work starts, with a unique
constraint on ``(experiment_id, task_id, task_version, attempt_index)``.
Launching, relaunching or resuming an experiment is therefore idempotent:
completed runs are left alone, and only ``PENDING`` or lease-expired runs are
picked up.  Nothing is recomputed and nothing is duplicated.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentbench.adapters.registry import capabilities_for, readiness_for, validate_agent_config
from agentbench.config import get_settings
from agentbench.db.models import AgentConfigRecord, ExperimentRecord, RunRecord, TaskRecord
from agentbench.logging_setup import get_logger
from agentbench.registry.service import get_task_by_key
from agentbench.schemas import provenance
from agentbench.schemas.enums import EventType, ExperimentState, RunOutcome, RunState
from agentbench.schemas.task import LimitsSpec, TaskSpec
from agentbench.trajectory.recorder import EventRecorder

log = get_logger(__name__)


class ExperimentError(ValueError):
    """The experiment cannot be created or launched as described."""


@dataclass(slots=True)
class ExperimentProgress:
    total: int = 0
    pending: int = 0
    active: int = 0
    completed: int = 0
    cancelled: int = 0
    #: Runs left untouched by a non-forced resume because a worker lease is live.
    skipped_live_lease: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)

    @property
    def finished(self) -> int:
        return self.completed + self.cancelled

    @property
    def percent(self) -> float:
        return round(100.0 * self.finished / self.total, 2) if self.total else 0.0

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.pending == 0 and self.active == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "pending": self.pending,
            "active": self.active,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "finished": self.finished,
            "percent": self.percent,
            "is_complete": self.is_complete,
            "skipped_live_lease": self.skipped_live_lease,
            "by_outcome": self.by_outcome,
        }


# --------------------------------------------------------------------------- #
def create_experiment(
    session: Session,
    *,
    name: str,
    agent_config_id: str,
    task_keys: list[str],
    description: str = "",
    repetitions: int = 1,
    concurrency: int | None = None,
    seed: int | None = None,
    include_hints: bool = False,
    limits_override: dict[str, Any] | None = None,
    baseline_experiment_id: str | None = None,
) -> ExperimentRecord:
    settings = get_settings()

    if repetitions < 1 or repetitions > 50:
        raise ExperimentError("repetitions must be between 1 and 50")
    if not task_keys:
        raise ExperimentError("an experiment must include at least one task")

    agent = session.get(AgentConfigRecord, agent_config_id)
    if agent is None:
        raise ExperimentError(f"unknown agent configuration: {agent_config_id}")
    if not agent.enabled:
        raise ExperimentError(f"agent configuration '{agent.label}' is disabled")

    validate_agent_config(agent.adapter, agent.config)

    resolved: list[TaskRecord] = []
    missing: list[str] = []
    for key in task_keys:
        record = get_task_by_key(session, key)
        if record is None:
            missing.append(key)
        else:
            resolved.append(record)
    if missing:
        raise ExperimentError(f"unknown task(s): {', '.join(missing)}")

    disabled = [r.key for r in resolved if not r.enabled]
    if disabled:
        raise ExperimentError(f"disabled task(s) cannot be benchmarked: {', '.join(disabled)}")

    if limits_override:
        try:
            LimitsSpec.model_validate({**LimitsSpec().model_dump(), **limits_override})
        except Exception as exc:
            raise ExperimentError(f"invalid limits override: {exc}") from exc

    benchmarks = {(r.benchmark_id, r.benchmark_version) for r in resolved}
    benchmark_id, benchmark_version = (
        next(iter(benchmarks)) if len(benchmarks) == 1 else ("mixed", "mixed")
    )

    capabilities = capabilities_for(agent.adapter)
    readiness = readiness_for(agent.adapter, agent.config)
    grading_versions = sorted(
        {(r.spec or {}).get("grading", {}).get("version", "1.0") for r in resolved}
    )

    agent_snapshot = {
        "agent_config_id": agent.id,
        "name": agent.name,
        "version": agent.version,
        "adapter": agent.adapter,
        "provider": agent.provider,
        "model": agent.model,
        "prompt_version": agent.prompt_version,
        "tools_version": agent.tools_version,
        "config": dict(agent.config or {}),
        "sampling": dict(agent.sampling or {}),
        "is_simulated": bool(agent.is_simulated or capabilities.is_simulated),
        "provenance": str(
            provenance.classify(
                is_simulated=bool(agent.is_simulated or capabilities.is_simulated),
                requires_credentials=capabilities.requires_credentials,
                readiness_ready=readiness.ready,
                model=agent.model,
            )
        ),
        "include_hints": include_hints,
        "capabilities": capabilities.to_dict(),
        "readiness_at_launch": readiness.to_dict(),
    }

    experiment = ExperimentRecord(
        name=name,
        description=description,
        state=ExperimentState.DRAFT,
        benchmark_id=benchmark_id,
        benchmark_version=benchmark_version,
        agent_config_id=agent.id,
        agent_snapshot=agent_snapshot,
        grading_version=",".join(grading_versions),
        repetitions=repetitions,
        concurrency=max(1, min(concurrency or 1, settings.max_concurrent_runs)),
        seed=seed,
        include_hints=include_hints,
        limits_override=dict(limits_override or {}),
        task_keys=[r.key for r in resolved],
        baseline_experiment_id=baseline_experiment_id,
    )
    experiment.config_snapshot = _config_snapshot(experiment, resolved, agent_snapshot)
    session.add(experiment)
    session.flush()

    log.info(
        "experiment.created",
        extra={
            "experiment_id": experiment.id,
            "tasks": len(resolved),
            "repetitions": repetitions,
            "agent": agent.label,
            "simulated": agent_snapshot["is_simulated"],
        },
    )
    return experiment


def _config_snapshot(
    experiment: ExperimentRecord, tasks: list[TaskRecord], agent_snapshot: dict[str, Any]
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "benchmark": {
            "id": experiment.benchmark_id,
            "version": experiment.benchmark_version,
            "tasks": [
                {
                    "key": record.key,
                    "category": record.category,
                    "difficulty": record.difficulty,
                    "grading_version": (record.spec or {}).get("grading", {}).get("version"),
                    "validation_status": record.validation_status,
                    "image": (record.spec or {}).get("environment", {}).get("image"),
                }
                for record in tasks
            ],
        },
        "agent": agent_snapshot,
        "execution": {
            "repetitions": experiment.repetitions,
            "concurrency": experiment.concurrency,
            "seed": experiment.seed,
            "include_hints": experiment.include_hints,
            "limits_override": experiment.limits_override,
            "sandbox_provider": settings.sandbox_provider,
            "infra_retry_limit": settings.infra_retry_limit,
            "hard_ceilings": {
                "max_agent_timeout_s": settings.max_agent_timeout_s,
                "max_memory_mb": settings.max_memory_mb,
                "max_cpus": settings.max_cpus,
            },
        },
        "platform": {"version": _platform_version()},
    }


def _platform_version() -> str:
    from agentbench import __version__

    return __version__


# --------------------------------------------------------------------------- #
def materialize_runs(session: Session, experiment: ExperimentRecord) -> int:
    """Create missing run rows. Idempotent - existing rows are never touched."""
    existing = {
        (row.task_id, row.task_version, row.attempt_index)
        for row in session.execute(
            select(RunRecord).where(RunRecord.experiment_id == experiment.id)
        ).scalars()
    }

    rng = random.Random(experiment.seed) if experiment.seed is not None else None
    created = 0

    for key in experiment.task_keys:
        record = get_task_by_key(session, key)
        if record is None:
            raise ExperimentError(f"task '{key}' referenced by the experiment no longer exists")
        spec = TaskSpec.model_validate(dict(record.spec or {}))
        limits = {**spec.limits.model_dump(mode="json"), **(experiment.limits_override or {})}
        snapshot = dict(record.spec or {})
        if experiment.limits_override:
            snapshot = {**snapshot, "limits": limits}

        for attempt in range(experiment.repetitions):
            if (record.task_id, record.version, attempt) in existing:
                continue
            seed = (
                experiment.seed + attempt
                if experiment.seed is not None and rng is not None
                else None
            )
            session.add(
                RunRecord(
                    experiment_id=experiment.id,
                    task_db_id=record.id,
                    task_id=record.task_id,
                    task_version=record.version,
                    attempt_index=attempt,
                    state=RunState.PENDING,
                    task_validation_status=record.validation_status,
                    task_snapshot=snapshot,
                    agent_snapshot=dict(experiment.agent_snapshot or {}),
                    limits=limits,
                    seed=seed,
                )
            )
            created += 1

    session.flush()
    if created:
        log.info(
            "experiment.runs_materialized",
            extra={"experiment_id": experiment.id, "created": created},
        )
    return created


def launch(session: Session, experiment_id: str) -> ExperimentProgress:
    experiment = _require(session, experiment_id)
    if ExperimentState(experiment.state) is ExperimentState.RUNNING:
        return progress(session, experiment_id)
    if ExperimentState(experiment.state) is ExperimentState.CANCELLED:
        raise ExperimentError("a cancelled experiment cannot be relaunched; create a new one")

    experiment.cancel_requested = False
    materialize_runs(session, experiment)
    experiment.state = ExperimentState.QUEUED
    experiment.started_at = experiment.started_at or datetime.now(UTC)
    experiment.finished_at = None
    session.flush()
    log.info("experiment.launched", extra={"experiment_id": experiment.id})
    return progress(session, experiment_id)


def cancel(session: Session, experiment_id: str) -> ExperimentProgress:
    """Request cancellation.

    Pending runs are cancelled immediately; active runs observe the flag at their
    next cancellation checkpoint and tear down their own sandbox.
    """
    experiment = _require(session, experiment_id)
    experiment.cancel_requested = True

    pending = session.execute(
        select(RunRecord).where(
            RunRecord.experiment_id == experiment.id, RunRecord.state == RunState.PENDING
        )
    ).scalars()
    now = datetime.now(UTC)
    count = 0
    for run in pending:
        run.state = RunState.CANCELLED
        run.outcome = RunOutcome.CANCELLED
        run.termination_reason = "experiment cancelled before this run started"
        run.finished_at = now
        count += 1
        recorder = EventRecorder(session, run.id)
        recorder.record(
            EventType.RUN_TERMINATED,
            {"outcome": str(RunOutcome.CANCELLED), "reason": "experiment cancelled"},
        )

    experiment.state = ExperimentState.CANCELLED
    experiment.finished_at = now
    session.flush()
    log.info(
        "experiment.cancelled",
        extra={"experiment_id": experiment.id, "pending_cancelled": count},
    )
    return progress(session, experiment_id)


def resume(session: Session, experiment_id: str, *, force: bool = False) -> ExperimentProgress:
    """Re-queue an interrupted experiment.

    Runs left mid-flight by a crashed worker (active state, no live lease) are
    reset to ``PENDING`` and will start over; the events and attempt history
    from the interrupted attempt are retained.  Completed runs are untouched, so
    no work is repeated and no result is lost.
    """
    experiment = _require(session, experiment_id)
    if ExperimentState(experiment.state) is ExperimentState.CANCELLED:
        raise ExperimentError("a cancelled experiment cannot be resumed; create a new one")

    experiment.cancel_requested = False
    now = datetime.now(UTC)
    reclaimed = 0
    skipped_live_lease = 0

    for run in session.execute(
        select(RunRecord).where(RunRecord.experiment_id == experiment.id)
    ).scalars():
        state = RunState(run.state)
        if state.is_terminal or state is RunState.PENDING:
            continue
        lease_live = run.claim_expires_at is not None and _aware(run.claim_expires_at) > now
        if lease_live and not force:
            # Another worker may still be executing this run. Resetting it would
            # duplicate work, so it is left alone unless the caller forces it.
            skipped_live_lease += 1
            continue
        recorder = EventRecorder(session, run.id)
        recorder.record(
            EventType.RETRY,
            {
                "scope": "resume",
                "previous_state": str(state),
                "reason": "experiment resumed after interruption; this attempt was abandoned",
            },
        )
        run.state = RunState.PENDING
        run.claimed_by = None
        run.claimed_at = None
        run.claim_expires_at = None
        run.sandbox_id = None
        reclaimed += 1

    materialize_runs(session, experiment)
    experiment.state = ExperimentState.QUEUED
    experiment.finished_at = None
    session.flush()
    log.info(
        "experiment.resumed",
        extra={
            "experiment_id": experiment.id,
            "reclaimed": reclaimed,
            "skipped_live_lease": skipped_live_lease,
            "forced": force,
        },
    )
    report = progress(session, experiment_id)
    report.skipped_live_lease = skipped_live_lease
    return report


def progress(session: Session, experiment_id: str) -> ExperimentProgress:
    rows = session.execute(
        select(RunRecord.state, RunRecord.outcome, func.count(RunRecord.id))
        .where(RunRecord.experiment_id == experiment_id)
        .group_by(RunRecord.state, RunRecord.outcome)
    ).all()

    report = ExperimentProgress()
    for state, outcome, count in rows:
        count = int(count or 0)
        report.total += count
        run_state = RunState(state)
        if run_state is RunState.PENDING:
            report.pending += count
        elif run_state is RunState.COMPLETED:
            report.completed += count
        elif run_state is RunState.CANCELLED:
            report.cancelled += count
        else:
            report.active += count
        if outcome:
            report.by_outcome[outcome] = report.by_outcome.get(outcome, 0) + count
    return report


def refresh_state(session: Session, experiment_id: str) -> ExperimentState:
    """Recompute the experiment state from its runs."""
    experiment = _require(session, experiment_id)
    current = ExperimentState(experiment.state)
    if current in (ExperimentState.DRAFT, ExperimentState.CANCELLED):
        return current

    report = progress(session, experiment_id)
    if report.total == 0:
        return current

    if report.is_complete:
        experiment.state = ExperimentState.COMPLETED
        experiment.finished_at = experiment.finished_at or datetime.now(UTC)
    elif report.active > 0:
        experiment.state = ExperimentState.RUNNING
        experiment.finished_at = None
    else:
        experiment.state = ExperimentState.QUEUED
    session.flush()
    return ExperimentState(experiment.state)


def runnable_experiment_ids(session: Session) -> list[str]:
    return [
        row
        for row in session.execute(
            select(ExperimentRecord.id).where(
                ExperimentRecord.state.in_([ExperimentState.QUEUED, ExperimentState.RUNNING]),
                ExperimentRecord.cancel_requested.is_(False),
            )
        ).scalars()
    ]


def _require(session: Session, experiment_id: str) -> ExperimentRecord:
    experiment = session.get(ExperimentRecord, experiment_id)
    if experiment is None:
        raise ExperimentError(f"unknown experiment: {experiment_id}")
    return experiment


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def stale_run_ids(session: Session, grace_seconds: int = 0) -> list[str]:
    """Runs whose worker lease has expired - reclaimable after a crash."""
    cutoff = datetime.now(UTC) - timedelta(seconds=grace_seconds)
    rows = session.execute(
        select(RunRecord.id, RunRecord.state, RunRecord.claim_expires_at).where(
            RunRecord.state.notin_([RunState.COMPLETED, RunState.CANCELLED, RunState.PENDING])
        )
    ).all()
    return [
        run_id for run_id, _state, expires in rows if expires is None or _aware(expires) < cutoff
    ]
