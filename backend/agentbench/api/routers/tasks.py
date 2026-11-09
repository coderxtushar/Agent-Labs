"""Benchmark task registry endpoints."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from agentbench.api.deps import DbSession, Pagination, WriteAccess, bad_request, conflict, not_found
from agentbench.api.schemas import (
    BenchmarkImportRequest,
    TaskEnableRequest,
    serialize_task,
)
from agentbench.registry.loader import (
    BenchmarkLoadError,
    load_benchmark_text,
    parse_benchmark_payload,
)
from agentbench.registry.service import (
    benchmark_summaries,
    export_tasks,
    get_task_by_key,
    import_benchmark,
    list_tasks,
    set_validation_result,
    spec_of,
)
from agentbench.registry.validator import static_checks, validate_task
from agentbench.sandbox.base import SandboxError

router = APIRouter(prefix="/tasks", tags=["benchmarks"])


@router.get("", summary="List benchmark tasks")
def list_endpoint(
    session: DbSession,
    page: Pagination,
    benchmark_id: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    difficulty: Annotated[str | None, Query()] = None,
    validation_status: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    enabled_only: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    rows, total = list_tasks(
        session,
        limit=page.limit,
        offset=page.offset,
        benchmark_id=benchmark_id,
        category=category,
        difficulty=difficulty,
        validation_status=validation_status,
        tag=tag,
        search=search,
        enabled_only=enabled_only,
    )
    return page.envelope([serialize_task(row) for row in rows], total)


@router.get("/benchmarks", summary="Benchmark groupings and validation coverage")
def benchmarks_endpoint(session: DbSession) -> dict[str, Any]:
    return {"benchmarks": benchmark_summaries(session)}


@router.get("/export", response_class=PlainTextResponse, summary="Export tasks as JSON or JSONL")
def export_endpoint(
    session: DbSession,
    benchmark_id: Annotated[str | None, Query()] = None,
    fmt: Annotated[str, Query(alias="format", pattern="^(json|jsonl)$")] = "json",
) -> PlainTextResponse:
    payload = export_tasks(session, benchmark_id=benchmark_id, fmt=fmt)
    suffix = "jsonl" if fmt == "jsonl" else "json"
    name = f"{benchmark_id or 'benchmark'}.{suffix}"
    return PlainTextResponse(
        payload,
        media_type="application/json" if fmt == "json" else "application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/{task_key}", summary="Full task definition")
def get_endpoint(
    session: DbSession,
    task_key: str,
    include_spec: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    record = get_task_by_key(session, task_key)
    if record is None:
        raise not_found("task", task_key)
    return serialize_task(record, include_spec=include_spec)


@router.post("/import", summary="Import a benchmark file", status_code=201)
def import_endpoint(
    session: DbSession, body: BenchmarkImportRequest, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        if isinstance(body.content, str):
            benchmark = load_benchmark_text(body.content, filename=body.filename)
        else:
            benchmark = parse_benchmark_payload(body.content)
    except BenchmarkLoadError as exc:
        raise bad_request(f"{exc}") from exc

    report = import_benchmark(session, benchmark, allow_overwrite=body.allow_overwrite)
    if not report.ok:
        raise conflict(json.dumps(report.to_dict()))
    return report.to_dict()


@router.post("/validate-spec", summary="Static validation of a task spec without executing it")
def validate_spec_endpoint(body: dict[str, Any], _: WriteAccess = None) -> dict[str, Any]:
    """Schema plus consistency checks only.

    Deliberately does not touch Docker, so the UI can give instant feedback
    while a task is being authored.  Environment and falsifiability checks need
    :func:`validate_endpoint`.
    """
    try:
        benchmark = parse_benchmark_payload(body)
    except BenchmarkLoadError as exc:
        return {"valid": False, "errors": exc.errors or [{"message": str(exc)}], "checks": []}

    checks = [check.to_dict() for task in benchmark.tasks for check in static_checks(task)]
    return {
        "valid": all(check["ok"] for check in checks),
        "errors": [],
        "tasks": [task.key for task in benchmark.tasks],
        "checks": checks,
        "note": (
            "static checks only; run POST /tasks/{task_key}/validate to build the environment "
            "and verify the task is actually executable and falsifiable"
        ),
    }


@router.post("/{task_key}/validate", summary="Build the environment and verify the task")
def validate_endpoint(session: DbSession, task_key: str, _: WriteAccess = None) -> dict[str, Any]:
    record = get_task_by_key(session, task_key)
    if record is None:
        raise not_found("task", task_key)
    try:
        report = validate_task(spec_of(record))
    except SandboxError as exc:
        raise bad_request(f"sandbox unavailable for validation: {exc}") from exc
    set_validation_result(session, record, report.status, report.to_dict())
    return report.to_dict()


@router.patch("/{task_key}/enabled", summary="Enable or disable a task")
def enable_endpoint(
    session: DbSession, task_key: str, body: TaskEnableRequest, _: WriteAccess = None
) -> dict[str, Any]:
    record = get_task_by_key(session, task_key)
    if record is None:
        raise not_found("task", task_key)
    record.enabled = body.enabled
    session.flush()
    return serialize_task(record)


@router.delete("/{task_key}", summary="Delete a task that has no recorded runs", status_code=204)
def delete_endpoint(session: DbSession, task_key: str, _: WriteAccess = None) -> None:
    from sqlalchemy import func, select

    from agentbench.db.models import RunRecord

    record = get_task_by_key(session, task_key)
    if record is None:
        raise not_found("task", task_key)
    runs = int(
        session.execute(
            select(func.count())
            .select_from(RunRecord)
            .where(RunRecord.task_id == record.task_id, RunRecord.task_version == record.version)
        ).scalar()
        or 0
    )
    if runs:
        raise conflict(
            f"task '{task_key}' has {runs} recorded run(s); disable it instead so existing "
            "results stay interpretable"
        )
    session.delete(record)
    session.flush()
