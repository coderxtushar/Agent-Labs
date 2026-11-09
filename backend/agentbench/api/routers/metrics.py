"""Cross-experiment metrics, comparisons and failure analysis."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from agentbench.api.deps import DbSession, Pagination, bad_request, not_found
from agentbench.api.schemas import serialize_annotation, serialize_run
from agentbench.db.models import ExperimentRecord, FailureAnnotationRecord, RunRecord
from agentbench.metrics.compare import compare_experiments
from agentbench.metrics.compute import platform_overview
from agentbench.metrics.export import comparison_markdown

router = APIRouter(tags=["metrics"])


@router.get("/overview", summary="Platform overview computed from stored results")
def overview_endpoint(session: DbSession) -> dict[str, Any]:
    return platform_overview(session)


@router.get("/compare", summary="Baseline vs candidate comparison")
def compare_endpoint(
    session: DbSession,
    baseline: Annotated[str, Query(description="baseline experiment id")],
    candidate: Annotated[str, Query(description="candidate experiment id")],
) -> dict[str, Any]:
    for identifier in (baseline, candidate):
        if session.get(ExperimentRecord, identifier) is None:
            raise not_found("experiment", identifier)
    if baseline == candidate:
        raise bad_request("baseline and candidate must be different experiments")
    return compare_experiments(session, baseline, candidate).to_dict()


@router.get(
    "/compare/report",
    response_class=PlainTextResponse,
    summary="Comparison report as Markdown",
)
def compare_report_endpoint(
    session: DbSession,
    baseline: Annotated[str, Query()],
    candidate: Annotated[str, Query()],
) -> PlainTextResponse:
    for identifier in (baseline, candidate):
        if session.get(ExperimentRecord, identifier) is None:
            raise not_found("experiment", identifier)
    return PlainTextResponse(
        comparison_markdown(session, baseline, candidate),
        media_type="text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="compare-{baseline[:8]}-{candidate[:8]}.md"'
            )
        },
    )


@router.get("/failures", summary="Failure categories with drill-down into evidence")
def failures_endpoint(
    session: DbSession,
    page: Pagination,
    experiment_id: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    task_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    stmt = (
        select(FailureAnnotationRecord, RunRecord)
        .join(RunRecord, RunRecord.id == FailureAnnotationRecord.run_id)
        .where(FailureAnnotationRecord.superseded.is_(False))
        .order_by(FailureAnnotationRecord.created_at.desc())
    )
    if experiment_id:
        stmt = stmt.where(RunRecord.experiment_id == experiment_id)
    if category:
        stmt = stmt.where(FailureAnnotationRecord.category == category)
    if task_id:
        stmt = stmt.where(RunRecord.task_id == task_id)
    if source:
        stmt = stmt.where(FailureAnnotationRecord.source == source)
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(FailureAnnotationRecord.rationale).like(pattern)
            | func.lower(RunRecord.task_id).like(pattern)
            | func.lower(func.coalesce(RunRecord.termination_reason, "")).like(pattern)
        )

    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = session.execute(stmt.limit(page.limit).offset(page.offset)).all()

    items = [
        {
            "annotation": serialize_annotation(annotation),
            "run": serialize_run(run),
            "grading_summary": _grading_summary(run),
        }
        for annotation, run in rows
    ]

    distribution_stmt = (
        select(FailureAnnotationRecord.category, func.count(FailureAnnotationRecord.id))
        .join(RunRecord, RunRecord.id == FailureAnnotationRecord.run_id)
        .where(FailureAnnotationRecord.superseded.is_(False))
        .group_by(FailureAnnotationRecord.category)
    )
    if experiment_id:
        distribution_stmt = distribution_stmt.where(RunRecord.experiment_id == experiment_id)

    envelope = page.envelope(items, total)
    envelope["distribution"] = {
        name: int(count or 0) for name, count in session.execute(distribution_stmt).all()
    }
    return envelope


def _grading_summary(run: RunRecord) -> dict[str, Any] | None:
    grading = run.grading
    if grading is None:
        return None
    return {
        "passed": grading.passed,
        "score": grading.score,
        "blocking_layers": (grading.evidence or {}).get("blocking_layers", []),
        "errored_layers": (grading.evidence or {}).get("errored_layers", []),
        "layers": [
            {
                "name": layer.get("name"),
                "status": layer.get("status"),
                "summary": layer.get("summary"),
            }
            for layer in (grading.layers or [])
            if isinstance(layer, dict)
        ],
        "tests": {
            "passed": grading.tests_passed,
            "failed": grading.tests_failed,
            "errored": grading.tests_errored,
        },
    }
