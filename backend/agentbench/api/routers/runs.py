"""Run inspection: detail, event timeline, patch, grading, failure annotations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, select

from agentbench.api.deps import DbSession, Pagination, WriteAccess, not_found
from agentbench.api.schemas import (
    AnnotationRequest,
    serialize_annotation,
    serialize_event,
    serialize_grading,
    serialize_run,
)
from agentbench.db.models import RunAttemptHistoryRecord, RunRecord
from agentbench.db.session import session_scope
from agentbench.metrics.failure import (
    Classification,
    annotation_history,
    effective_annotation,
    store_classification,
)
from agentbench.schemas.enums import ClassificationSource, RunState
from agentbench.trajectory.recorder import count_events, read_events

router = APIRouter(prefix="/runs", tags=["runs"])

STREAM_POLL_INTERVAL_S = 1.0
STREAM_MAX_SECONDS = 1800


@router.get("", summary="List runs across experiments")
def list_endpoint(
    session: DbSession,
    page: Pagination,
    experiment_id: Annotated[str | None, Query()] = None,
    outcome: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    task_id: Annotated[str | None, Query()] = None,
    active_only: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    stmt = select(RunRecord).order_by(RunRecord.created_at.desc())
    if experiment_id:
        stmt = stmt.where(RunRecord.experiment_id == experiment_id)
    if outcome:
        stmt = stmt.where(RunRecord.outcome == outcome)
    if state:
        stmt = stmt.where(RunRecord.state == state)
    if task_id:
        stmt = stmt.where(RunRecord.task_id == task_id)
    if active_only:
        stmt = stmt.where(
            RunRecord.state.notin_([RunState.COMPLETED, RunState.CANCELLED, RunState.PENDING])
        )

    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = list(session.execute(stmt.limit(page.limit).offset(page.offset)).scalars())
    return page.envelope([serialize_run(row) for row in rows], total)


@router.get("/{run_id}", summary="Run detail with grading, failure evidence and attempt history")
def get_endpoint(session: DbSession, run_id: str) -> dict[str, Any]:
    record = session.get(RunRecord, run_id)
    if record is None:
        raise not_found("run", run_id)

    annotation = effective_annotation(session, run_id)
    history = [
        {
            "attempt_no": row.attempt_no,
            "outcome": row.outcome,
            "reason": row.reason,
            "detail": row.detail,
            "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        }
        for row in session.execute(
            select(RunAttemptHistoryRecord)
            .where(RunAttemptHistoryRecord.run_id == run_id)
            .order_by(RunAttemptHistoryRecord.attempt_no.asc())
        ).scalars()
    ]

    return {
        **serialize_run(record),
        "grading": serialize_grading(record.grading),
        "failure": serialize_annotation(annotation) if annotation else None,
        "failure_history": [
            serialize_annotation(row) for row in annotation_history(session, run_id)
        ],
        "attempt_history": history,
        "event_count": count_events(session, run_id),
        "task_snapshot": record.task_snapshot,
        "agent_snapshot": _redacted_agent(record),
    }


def _redacted_agent(record: RunRecord) -> dict[str, Any]:
    from agentbench.trajectory.redaction import redact_value

    return redact_value(dict(record.agent_snapshot or {}))


@router.get("/{run_id}/events", summary="Ordered event timeline")
def events_endpoint(
    session: DbSession,
    run_id: str,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    event_type: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    record = session.get(RunRecord, run_id)
    if record is None:
        raise not_found("run", run_id)
    rows = read_events(session, run_id, after_seq=after_seq, limit=limit, event_types=event_type)
    total = count_events(session, run_id)
    return {
        "run_id": run_id,
        "events": [serialize_event(row) for row in rows],
        "total": total,
        "last_seq": rows[-1].seq if rows else after_seq,
        "has_more": bool(rows) and rows[-1].seq < total,
        "run_state": record.state,
        "is_terminal": RunState(record.state).is_terminal,
        "ordering_note": "events are ordered by `seq`; timestamps can collide",
    }


@router.get("/{run_id}/events/stream", summary="Server-sent event stream of new events")
async def stream_endpoint(
    run_id: str, after_seq: Annotated[int, Query(ge=0)] = 0
) -> StreamingResponse:
    """Server-sent events, polled from the database.

    Polling rather than a listen/notify channel keeps this working identically
    on SQLite and PostgreSQL.  The stream closes as soon as the run reaches a
    terminal state, and in any case after ``STREAM_MAX_SECONDS``.
    """

    async def generator() -> AsyncIterator[bytes]:
        cursor = after_seq
        elapsed = 0.0
        while elapsed < STREAM_MAX_SECONDS:
            with session_scope() as session:
                record = session.get(RunRecord, run_id)
                if record is None:
                    yield _sse("error", {"message": f"run not found: {run_id}"})
                    return
                rows = read_events(session, run_id, after_seq=cursor, limit=200)
                terminal = RunState(record.state).is_terminal
                state = record.state
                payloads = [serialize_event(row) for row in rows]

            for payload in payloads:
                cursor = payload["seq"]
                yield _sse("event", payload)
            if terminal:
                yield _sse("end", {"run_id": run_id, "state": state, "last_seq": cursor})
                return
            yield b": keep-alive\n\n"
            await asyncio.sleep(STREAM_POLL_INTERVAL_S)
            elapsed += STREAM_POLL_INTERVAL_S

        yield _sse("timeout", {"run_id": run_id, "last_seq": cursor})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.get("/{run_id}/patch", response_class=PlainTextResponse, summary="Unified diff")
def patch_endpoint(session: DbSession, run_id: str) -> PlainTextResponse:
    record = session.get(RunRecord, run_id)
    if record is None:
        raise not_found("run", run_id)
    body = record.patch or ""
    return PlainTextResponse(
        body,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.diff"'},
    )


@router.get("/{run_id}/grading", summary="Grading result with per-layer evidence")
def grading_endpoint(session: DbSession, run_id: str) -> dict[str, Any]:
    record = session.get(RunRecord, run_id)
    if record is None:
        raise not_found("run", run_id)
    payload = serialize_grading(record.grading)
    if payload is None:
        return {
            "run_id": run_id,
            "grading": None,
            "reason": (
                f"this run was not graded: it ended as '{record.outcome}'. Grading only runs "
                "for an agent that finished normally."
            ),
        }
    return {"run_id": run_id, "grading": payload}


@router.post(
    "/{run_id}/annotations", summary="Record a human failure classification", status_code=201
)
def annotate_endpoint(
    session: DbSession, run_id: str, body: AnnotationRequest, _: WriteAccess = None
) -> dict[str, Any]:
    """Append a human classification.

    The previous classification is marked superseded rather than deleted, and no
    raw evidence is touched, so an incorrect correction can itself be corrected.
    """
    record = session.get(RunRecord, run_id)
    if record is None:
        raise not_found("run", run_id)

    previous = effective_annotation(session, run_id)
    annotation = store_classification(
        session,
        run_id,
        Classification(
            category=body.category,
            source=ClassificationSource.HUMAN,
            confidence=None,
            rationale=body.rationale,
            evidence_refs={
                "supersedes_category": previous.category if previous else None,
                "supersedes_source": previous.source if previous else None,
            },
        ),
        author=body.author,
    )
    return serialize_annotation(annotation)
