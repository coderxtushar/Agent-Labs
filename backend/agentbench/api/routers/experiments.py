"""Experiment endpoints: create, launch, cancel, resume, inspect, report."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from agentbench.api.deps import DbSession, Pagination, WriteAccess, bad_request, not_found
from agentbench.api.schemas import (
    ExperimentCreateRequest,
    ResumeRequest,
    serialize_experiment,
    serialize_run,
)
from agentbench.db.models import ExperimentRecord, RunRecord
from agentbench.metrics.compute import experiment_metrics
from agentbench.metrics.export import render_report
from agentbench.orchestrator import experiment as service
from agentbench.orchestrator.worker import get_worker

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.get("", summary="List experiments")
def list_endpoint(
    session: DbSession,
    page: Pagination,
    state: Annotated[str | None, Query()] = None,
    benchmark_id: Annotated[str | None, Query()] = None,
    agent_config_id: Annotated[str | None, Query()] = None,
    include_simulated: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    stmt = select(ExperimentRecord).order_by(ExperimentRecord.created_at.desc())
    if state:
        stmt = stmt.where(ExperimentRecord.state == state)
    if benchmark_id:
        stmt = stmt.where(ExperimentRecord.benchmark_id == benchmark_id)
    if agent_config_id:
        stmt = stmt.where(ExperimentRecord.agent_config_id == agent_config_id)

    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = list(session.execute(stmt.limit(page.limit).offset(page.offset)).scalars())
    if not include_simulated:
        rows = [r for r in rows if not (r.agent_snapshot or {}).get("is_simulated")]

    items = []
    for record in rows:
        payload = serialize_experiment(record)
        payload["progress"] = service.progress(session, record.id).to_dict()
        items.append(payload)
    return page.envelope(items, total)


@router.post("", summary="Create an experiment", status_code=201)
def create_endpoint(
    session: DbSession, body: ExperimentCreateRequest, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        experiment = service.create_experiment(
            session,
            name=body.name,
            agent_config_id=body.agent_config_id,
            task_keys=body.task_keys,
            description=body.description,
            repetitions=body.repetitions,
            concurrency=body.concurrency,
            seed=body.seed,
            include_hints=body.include_hints,
            limits_override=body.limits_override,
            baseline_experiment_id=body.baseline_experiment_id,
        )
        if body.launch:
            service.launch(session, experiment.id)
    except service.ExperimentError as exc:
        raise bad_request(str(exc)) from exc

    payload = serialize_experiment(experiment)
    payload["progress"] = service.progress(session, experiment.id).to_dict()
    return payload


@router.get("/{experiment_id}", summary="One experiment with progress")
def get_endpoint(session: DbSession, experiment_id: str) -> dict[str, Any]:
    record = session.get(ExperimentRecord, experiment_id)
    if record is None:
        raise not_found("experiment", experiment_id)
    payload = serialize_experiment(record)
    payload["progress"] = service.progress(session, experiment_id).to_dict()
    return payload


@router.post("/{experiment_id}/launch", summary="Queue the experiment's runs")
def launch_endpoint(
    session: DbSession, experiment_id: str, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        report = service.launch(session, experiment_id)
    except service.ExperimentError as exc:
        raise bad_request(str(exc)) from exc
    _nudge_worker()
    return report.to_dict()


@router.post("/{experiment_id}/cancel", summary="Request cancellation")
def cancel_endpoint(
    session: DbSession, experiment_id: str, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        report = service.cancel(session, experiment_id)
    except service.ExperimentError as exc:
        raise not_found("experiment", experiment_id) from exc
    return {
        **report.to_dict(),
        "note": (
            "pending runs were cancelled immediately; in-flight runs stop at their next "
            "cancellation checkpoint and tear down their own sandbox"
        ),
    }


@router.post("/{experiment_id}/resume", summary="Re-queue an interrupted experiment")
def resume_endpoint(
    session: DbSession,
    experiment_id: str,
    body: ResumeRequest | None = None,
    _: WriteAccess = None,
) -> dict[str, Any]:
    try:
        report = service.resume(session, experiment_id, force=bool(body and body.force))
    except service.ExperimentError as exc:
        raise bad_request(str(exc)) from exc
    _nudge_worker()
    payload = report.to_dict()
    if payload.get("skipped_live_lease"):
        payload["note"] = (
            f"{payload['skipped_live_lease']} run(s) were left alone because a worker lease is "
            "still live. Pass force=true only if you know that worker is gone."
        )
    return payload


@router.get("/{experiment_id}/metrics", summary="Metrics computed from stored results")
def metrics_endpoint(session: DbSession, experiment_id: str) -> dict[str, Any]:
    try:
        return experiment_metrics(session, experiment_id).to_dict()
    except LookupError as exc:
        raise not_found("experiment", experiment_id) from exc


@router.get("/{experiment_id}/runs", summary="Runs belonging to an experiment")
def runs_endpoint(
    session: DbSession,
    experiment_id: str,
    page: Pagination,
    outcome: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    task_key: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    if session.get(ExperimentRecord, experiment_id) is None:
        raise not_found("experiment", experiment_id)

    stmt = (
        select(RunRecord)
        .where(RunRecord.experiment_id == experiment_id)
        .order_by(RunRecord.task_id.asc(), RunRecord.attempt_index.asc())
    )
    if outcome:
        stmt = stmt.where(RunRecord.outcome == outcome)
    if state:
        stmt = stmt.where(RunRecord.state == state)
    if task_key:
        task_id, _, version = task_key.partition("@")
        stmt = stmt.where(RunRecord.task_id == task_id)
        if version:
            stmt = stmt.where(RunRecord.task_version == version)

    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = list(session.execute(stmt.limit(page.limit).offset(page.offset)).scalars())
    return page.envelope([serialize_run(row) for row in rows], total)


@router.get(
    "/{experiment_id}/report",
    response_class=PlainTextResponse,
    summary="Reproducible experiment report",
)
def report_endpoint(
    session: DbSession,
    experiment_id: str,
    fmt: Annotated[str, Query(alias="format", pattern="^(json|csv|markdown|md)$")] = "markdown",
) -> PlainTextResponse:
    if session.get(ExperimentRecord, experiment_id) is None:
        raise not_found("experiment", experiment_id)
    payload = render_report(session, experiment_id, fmt=fmt)
    media = {
        "json": "application/json",
        "csv": "text/csv",
        "markdown": "text/markdown",
        "md": "text/markdown",
    }[fmt]
    suffix = {"json": "json", "csv": "csv", "markdown": "md", "md": "md"}[fmt]
    return PlainTextResponse(
        payload,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="experiment-{experiment_id}.{suffix}"'
        },
    )


def _nudge_worker() -> None:
    """Give the in-process worker a chance to pick work up immediately."""
    from agentbench.config import get_settings

    if not get_settings().worker_in_api_process:
        return
    worker = get_worker()
    if worker.running:
        try:
            worker.poll_once()
        except Exception:  # noqa: BLE001 - the polling loop will retry anyway
            return
