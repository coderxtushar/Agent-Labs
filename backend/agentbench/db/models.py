"""Persistence model.

Design notes that matter:

* ``runs`` carries a unique constraint on
  ``(experiment_id, task_id, task_version, attempt_index)``.  That is the
  idempotency key: re-queueing an experiment can never silently duplicate work
  that already completed.
* Infrastructure retries increment ``infra_attempts`` and append a row to
  ``run_attempt_history``; the original failure evidence is never overwritten.
* ``failure_annotations`` is append-only.  A human correction supersedes an
  automatic classification rather than replacing it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentbench.schemas.enums import (
    ClassificationSource,
    ExperimentState,
    FailureCategory,
    RunState,
    TaskValidationStatus,
)


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------- #
class TaskRecord(Base, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_tasks_taskid_version"),
        Index("ix_tasks_benchmark", "benchmark_id", "benchmark_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), index=True)
    difficulty: Mapped[str] = mapped_column(String(16), index=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)

    benchmark_id: Mapped[str] = mapped_column(String(80), index=True, default="custom")
    benchmark_version: Mapped[str] = mapped_column(String(20), default="1.0")

    #: Full validated :class:`agentbench.schemas.task.TaskSpec` as JSON.
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)

    validation_status: Mapped[str] = mapped_column(
        String(16), default=TaskValidationStatus.UNVALIDATED, index=True
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def key(self) -> str:
        return f"{self.task_id}@{self.version}"


# --------------------------------------------------------------------------- #
class AgentConfigRecord(Base, TimestampMixin):
    __tablename__ = "agent_configs"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_agent_name_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    description: Mapped[str] = mapped_column(Text, default="")

    #: Adapter key registered in :mod:`agentbench.adapters.registry`.
    adapter: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)

    prompt_version: Mapped[str] = mapped_column(String(40), default="1.0")
    tools_version: Mapped[str] = mapped_column(String(40), default="1.0")

    #: Adapter-specific, non-secret configuration.
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sampling: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    #: True for deterministic test doubles. Results from simulated agents are
    #: never presented as model benchmark results.
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}"


# --------------------------------------------------------------------------- #
class ExperimentRecord(Base, TimestampMixin):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(16), default=ExperimentState.DRAFT, index=True)

    benchmark_id: Mapped[str] = mapped_column(String(80), default="custom")
    benchmark_version: Mapped[str] = mapped_column(String(20), default="1.0")

    agent_config_id: Mapped[str] = mapped_column(ForeignKey("agent_configs.id"), index=True)
    #: Frozen copy of the agent configuration at launch time, so later edits to
    #: the agent record cannot retroactively change what an experiment measured.
    agent_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    grading_version: Mapped[str] = mapped_column(String(20), default="1.0")
    repetitions: Mapped[int] = mapped_column(Integer, default=1)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    include_hints: Mapped[bool] = mapped_column(Boolean, default=False)

    limits_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Ordered list of ``task_id@version`` strings included in this experiment.
    task_keys: Mapped[list[Any]] = mapped_column(JSON, default=list)
    #: Everything needed to describe the experiment for reproduction.
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    #: Set when this experiment is the candidate side of a baseline comparison.
    baseline_experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiments.id"), nullable=True, index=True
    )

    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_config: Mapped[AgentConfigRecord] = relationship(lazy="joined")
    runs: Mapped[list[RunRecord]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan", lazy="select"
    )


# --------------------------------------------------------------------------- #
class RunRecord(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "task_id",
            "task_version",
            "attempt_index",
            name="uq_runs_idempotency",
        ),
        Index("ix_runs_state_claim", "state", "claim_expires_at"),
        Index("ix_runs_experiment_outcome", "experiment_id", "outcome"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    task_db_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)

    task_id: Mapped[str] = mapped_column(String(80), index=True)
    task_version: Mapped[str] = mapped_column(String(20))
    #: Repetition index, 0-based. Part of the idempotency key.
    attempt_index: Mapped[int] = mapped_column(Integer, default=0)
    #: Validation status of the task at the moment this run was created. Carried
    #: on the run so a later re-validation cannot change how a result reads.
    task_validation_status: Mapped[str] = mapped_column(
        String(16), default=TaskValidationStatus.UNVALIDATED
    )

    state: Mapped[str] = mapped_column(String(20), default=RunState.PENDING, index=True)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    termination_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Frozen task spec, so a later task edit cannot change history.
    task_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    agent_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Worker lease. A stale lease is reclaimable, which is how crashed workers
    #: are recovered without duplicating completed work.
    claimed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Count of infrastructure-caused restarts. Agent failures are never retried.
    infra_attempts: Mapped[int] = mapped_column(Integer, default=0)

    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Where the cost number came from, e.g. "provider_reported" or
    #: "estimated:price_table@2026-10". Never left implicit.
    cost_source: Mapped[str | None] = mapped_column(String(80), nullable=True)

    timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sandbox_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sandbox_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    experiment: Mapped[ExperimentRecord] = relationship(back_populates="runs")
    events: Mapped[list[RunEventRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="select"
    )
    grading: Mapped[GradingResultRecord | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False, lazy="select"
    )

    @property
    def task_key(self) -> str:
        return f"{self.task_id}@{self.task_version}"


class RunAttemptHistoryRecord(Base):
    """Append-only record of every abandoned attempt.

    Guarantees that an infrastructure retry does not erase the original failure.
    """

    __tablename__ = "run_attempt_history"
    __table_args__ = (Index("ix_attempt_history_run", "run_id", "attempt_no"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_events_seq"),
        Index("ix_run_events_run_seq", "run_id", "seq"),
        Index("ix_run_events_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    #: Monotonic per run, starting at 1. Ordering is never inferred from
    #: timestamps, which can collide.
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: True when the payload was truncated for retention reasons, so a consumer
    #: can tell "empty" apart from "not stored".
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[RunRecord] = relationship(back_populates="events")


# --------------------------------------------------------------------------- #
class GradingResultRecord(Base):
    __tablename__ = "grading_results"
    __table_args__ = (UniqueConstraint("run_id", name="uq_grading_run"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    grader_version: Mapped[str] = mapped_column(String(20), default="1.0")

    #: The deterministic verdict. ``None`` means grading could not conclude.
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    tests_passed: Mapped[int] = mapped_column(Integer, default=0)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0)
    tests_errored: Mapped[int] = mapped_column(Integer, default=0)
    tests_skipped: Mapped[int] = mapped_column(Integer, default=0)

    #: One entry per grading layer, with status, exit code and evidence.
    layers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Model-assisted review, stored separately and never folded into ``passed``.
    judge: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[RunRecord] = relationship(back_populates="grading")


# --------------------------------------------------------------------------- #
class FailureAnnotationRecord(Base):
    """Append-only failure classification.

    The newest non-superseded row for a run is the effective classification.
    Raw evidence in ``grading_results`` and ``run_events`` is never mutated.
    """

    __tablename__ = "failure_annotations"
    __table_args__ = (Index("ix_failure_run_created", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    category: Mapped[str] = mapped_column(String(48), default=FailureCategory.UNKNOWN, index=True)
    source: Mapped[str] = mapped_column(String(16), default=ClassificationSource.RULE, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_kind", "run_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
