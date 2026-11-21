"""Cross-experiment metrics, comparisons and failure analysis."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from agentbench.api.deps import AppSettings, DbSession, Pagination, bad_request, not_found
from agentbench.api.schemas import serialize_annotation, serialize_run
from agentbench.db.models import ExperimentRecord, FailureAnnotationRecord, RunRecord
from agentbench.metrics.compare import compare_experiments
from agentbench.metrics.compute import platform_overview
from agentbench.metrics.export import comparison_markdown
from agentbench.metrics.external import published_results
from agentbench.metrics.insights import (
    MAX_COMPARE_ARMS,
    analytics,
    compare_many,
    leaderboard,
    overview_extras,
)

router = APIRouter(tags=["metrics"])


@router.get("/overview", summary="Platform overview computed from stored results")
def overview_endpoint(session: DbSession) -> dict[str, Any]:
    return {**platform_overview(session), **overview_extras(session)}


def _id_list(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


@router.get("/leaderboard", summary="Agent leaderboard across experiments, grouped by provenance")
def leaderboard_endpoint(
    session: DbSession,
    benchmark_id: Annotated[str | None, Query(max_length=80)] = None,
    experiment_ids: Annotated[
        str | None, Query(description="comma-separated experiment ids", max_length=2000)
    ] = None,
    include_non_model: Annotated[
        bool, Query(description="include simulated and offline-adapter results")
    ] = False,
) -> dict[str, Any]:
    return leaderboard(
        session,
        benchmark_id=benchmark_id or None,
        experiment_ids=_id_list(experiment_ids) or None,
        include_non_model=include_non_model,
    )


@router.get(
    "/published-results",
    summary="Third-party published benchmark scores (not run in Agent Labs), with sources",
)
def published_results_endpoint(settings: AppSettings) -> dict[str, Any]:
    return published_results(settings.benchmarks_dir / "external")


@router.get("/analytics", summary="Platform-wide activity, tool usage and runtime analytics")
def analytics_endpoint(session: DbSession) -> dict[str, Any]:
    return analytics(session)


@router.get("/compare/matrix", summary="Side-by-side comparison of 2 to 6 experiments")
def compare_matrix_endpoint(
    session: DbSession,
    experiment_ids: Annotated[str, Query(description="comma-separated experiment ids")],
) -> dict[str, Any]:
    ids = _id_list(experiment_ids)
    if not 2 <= len(ids) <= MAX_COMPARE_ARMS:
        raise bad_request(f"select between 2 and {MAX_COMPARE_ARMS} experiments")
    if len(set(ids)) != len(ids):
        raise bad_request("experiment ids must be distinct")
    for identifier in ids:
        if session.get(ExperimentRecord, identifier) is None:
            raise not_found("experiment", identifier)
    return compare_many(session, ids)


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
    category: Annotated[
        str | None,
        Query(description="one category, or several separated by commas (any of them)"),
    ] = None,
    task_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    def scoped(stmt: Any) -> Any:
        """Every filter except category, shared by the list and the distribution."""
        stmt = stmt.where(FailureAnnotationRecord.superseded.is_(False))
        if experiment_id:
            stmt = stmt.where(RunRecord.experiment_id == experiment_id)
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
        return stmt

    categories = [part.strip() for part in (category or "").split(",") if part.strip()]
    stmt = scoped(
        select(FailureAnnotationRecord, RunRecord)
        .join(RunRecord, RunRecord.id == FailureAnnotationRecord.run_id)
        .order_by(FailureAnnotationRecord.created_at.desc())
    )
    if categories:
        stmt = stmt.where(FailureAnnotationRecord.category.in_(categories))

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

    # The distribution ignores the category filter on purpose: it is what the
    # bucket cards show, and each bucket must keep its count when one is selected.
    distribution_stmt = scoped(
        select(FailureAnnotationRecord.category, func.count(FailureAnnotationRecord.id))
        .join(RunRecord, RunRecord.id == FailureAnnotationRecord.run_id)
        .group_by(FailureAnnotationRecord.category)
    )

    envelope = page.envelope(items, total)
    envelope["distribution"] = {
        name: int(count or 0) for name, count in session.execute(distribution_stmt).all()
    }
    envelope["distribution_scope"] = (
        "all filters except category (experiment, task, source and search apply)"
    )
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
