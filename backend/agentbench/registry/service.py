"""Task registry persistence.

Task records are immutable per ``(task_id, version)``: importing a changed task
under an existing version is refused unless explicitly allowed, because silently
mutating a task would invalidate every result already recorded against it.  The
intended workflow is to bump ``version`` and let old results keep referring to
the old definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from agentbench.db.models import RunRecord, TaskRecord
from agentbench.logging_setup import get_logger
from agentbench.registry.loader import (
    LoadedBenchmark,
    export_benchmark_json,
    export_benchmark_jsonl,
)
from agentbench.schemas.enums import TaskValidationStatus
from agentbench.schemas.task import TaskSpec

log = get_logger(__name__)


class TaskConflict(ValueError):
    """An existing task version would be changed by this import."""


@dataclass(slots=True)
class ImportReport:
    benchmark_id: str
    benchmark_version: str
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "conflicts": self.conflicts,
            "ok": self.ok,
        }


def spec_of(record: TaskRecord) -> TaskSpec:
    """Rebuild the validated spec from a stored record."""
    return TaskSpec.model_validate(dict(record.spec or {}))


def get_task(session: Session, task_id: str, version: str | None = None) -> TaskRecord | None:
    stmt = select(TaskRecord).where(TaskRecord.task_id == task_id)
    if version:
        stmt = stmt.where(TaskRecord.version == version)
    else:
        stmt = stmt.order_by(TaskRecord.version.desc())
    return session.execute(stmt.limit(1)).scalar_one_or_none()


def get_task_by_key(session: Session, key: str) -> TaskRecord | None:
    task_id, _, version = key.partition("@")
    return get_task(session, task_id, version or None)


def _spec_payload(spec: TaskSpec) -> dict[str, Any]:
    return spec.model_dump(mode="json")


def import_benchmark(
    session: Session, benchmark: LoadedBenchmark, *, allow_overwrite: bool = False
) -> ImportReport:
    report = ImportReport(benchmark.benchmark_id, benchmark.benchmark_version)

    for spec in benchmark.tasks:
        existing = get_task(session, spec.task_id, spec.version)
        payload = _spec_payload(spec)

        if existing is None:
            session.add(
                TaskRecord(
                    task_id=spec.task_id,
                    version=spec.version,
                    title=spec.title,
                    description=spec.description,
                    category=str(spec.category),
                    difficulty=str(spec.difficulty),
                    tags=list(spec.tags),
                    benchmark_id=benchmark.benchmark_id,
                    benchmark_version=benchmark.benchmark_version,
                    spec=payload,
                    validation_status=TaskValidationStatus.UNVALIDATED,
                )
            )
            report.created.append(spec.key)
            continue

        stored = dict(existing.spec or {})
        if stored == payload:
            report.unchanged.append(spec.key)
            continue

        run_count = (
            session.execute(
                select(func.count())
                .select_from(RunRecord)
                .where(RunRecord.task_id == spec.task_id, RunRecord.task_version == spec.version)
            ).scalar()
            or 0
        )

        if not allow_overwrite:
            report.conflicts.append(
                {
                    "task_key": spec.key,
                    "reason": (
                        f"a different definition of {spec.key} already exists"
                        + (f" and has {run_count} recorded run(s)" if run_count else "")
                        + "; bump the task version instead of changing it in place"
                    ),
                }
            )
            continue

        existing.title = spec.title
        existing.description = spec.description
        existing.category = str(spec.category)
        existing.difficulty = str(spec.difficulty)
        existing.tags = list(spec.tags)
        existing.benchmark_id = benchmark.benchmark_id
        existing.benchmark_version = benchmark.benchmark_version
        existing.spec = payload
        existing.validation_status = TaskValidationStatus.UNVALIDATED
        existing.validation_report = None
        existing.validated_at = None
        report.updated.append(spec.key)

    session.flush()
    log.info("registry.import", extra=report.to_dict())
    return report


def task_query(
    *,
    benchmark_id: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    validation_status: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    enabled_only: bool = False,
) -> Select[Any]:
    stmt = select(TaskRecord)
    if benchmark_id:
        stmt = stmt.where(TaskRecord.benchmark_id == benchmark_id)
    if category:
        stmt = stmt.where(TaskRecord.category == category)
    if difficulty:
        stmt = stmt.where(TaskRecord.difficulty == difficulty)
    if validation_status:
        stmt = stmt.where(TaskRecord.validation_status == validation_status)
    if enabled_only:
        stmt = stmt.where(TaskRecord.enabled.is_(True))
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(TaskRecord.title).like(pattern)
            | func.lower(TaskRecord.task_id).like(pattern)
        )
    if tag:
        # JSON containment is not portable across SQLite and PostgreSQL, so the
        # tag filter is applied in Python by the caller via `filter_by_tag`.
        pass
    return stmt.order_by(TaskRecord.benchmark_id.asc(), TaskRecord.task_id.asc())


def filter_by_tag(records: list[TaskRecord], tag: str | None) -> list[TaskRecord]:
    if not tag:
        return records
    return [r for r in records if tag in (r.tags or [])]


def list_tasks(
    session: Session, *, limit: int = 100, offset: int = 0, **filters: Any
) -> tuple[list[TaskRecord], int]:
    tag = filters.pop("tag", None)
    stmt = task_query(**filters)
    if tag:
        all_rows = filter_by_tag(list(session.execute(stmt).scalars()), tag)
        return all_rows[offset : offset + limit], len(all_rows)
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = list(session.execute(stmt.limit(limit).offset(offset)).scalars())
    return rows, int(total)


def benchmark_summaries(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            TaskRecord.benchmark_id,
            TaskRecord.benchmark_version,
            func.count(TaskRecord.id),
            func.sum(
                case((TaskRecord.validation_status == TaskValidationStatus.VALID, 1), else_=0)
            ),
        ).group_by(TaskRecord.benchmark_id, TaskRecord.benchmark_version)
    ).all()
    return [
        {
            "benchmark_id": benchmark_id,
            "benchmark_version": version,
            "task_count": int(count or 0),
            "validated_count": int(validated or 0),
        }
        for benchmark_id, version, count, validated in rows
    ]


def export_tasks(
    session: Session,
    *,
    benchmark_id: str | None = None,
    fmt: str = "json",
    task_keys: list[str] | None = None,
) -> str:
    if task_keys:
        records = [r for r in (get_task_by_key(session, key) for key in task_keys) if r is not None]
    else:
        stmt = select(TaskRecord).order_by(TaskRecord.task_id.asc())
        if benchmark_id:
            stmt = stmt.where(TaskRecord.benchmark_id == benchmark_id)
        records = list(session.execute(stmt).scalars())

    specs = [spec_of(record) for record in records]
    if fmt == "jsonl":
        return export_benchmark_jsonl(specs)
    first = records[0] if records else None
    return export_benchmark_json(
        specs,
        benchmark_id=benchmark_id or (first.benchmark_id if first else "custom"),
        benchmark_version=(first.benchmark_version if first else "1.0"),
        description=f"Exported from AgentBench Lab at {datetime.now(UTC).isoformat()}",
    )


def set_validation_result(
    session: Session, record: TaskRecord, status: TaskValidationStatus, report: dict[str, Any]
) -> None:
    record.validation_status = status
    record.validation_report = report
    record.validated_at = datetime.now(UTC)
    session.flush()
