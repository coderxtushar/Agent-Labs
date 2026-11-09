"""Metrics computed from stored run results.

Nothing here is cached or precomputed: every number is derived from
``runs`` and ``grading_results`` at read time, so a metric can always be traced
back to the rows that produced it.

Honesty rules baked into the shapes below
-----------------------------------------
* **Every rate carries its denominator.**  A rate with ``denominator == 0`` is
  reported as ``None``, never as ``0.0``.
* **Two success rates, both labelled.**  ``success_rate_attributable`` counts
  timeouts and agent errors as failures (comparable to how SWE-bench-style
  numbers are usually quoted).  ``success_rate_graded`` only counts runs that
  reached a verdict.  Infrastructure failures, invalid tasks and cancellations
  are excluded from both and reported separately.
* **pass@k only when the sampling supports it.**  ``pass@k`` is omitted unless
  the experiment ran at least ``k`` repetitions per task, and the estimator
  used is stated in the output.
* **Confidence intervals are flagged as unreliable for small samples**, and
  nothing in this module claims statistical significance.
* **Completeness is first-class.**  ``run_completeness`` and
  ``excluded_from_rates`` make partial experiments visibly partial.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import (
    ExperimentRecord,
    FailureAnnotationRecord,
    GradingResultRecord,
    RunRecord,
)
from agentbench.schemas import provenance as provenance_module
from agentbench.schemas.enums import ExperimentState, FailureCategory, RunOutcome, RunState

PASS_AT_K_ESTIMATOR = "unbiased (Chen et al. 2021): 1 - C(n-c, k) / C(n, k)"
CI_METHOD = "Wilson score interval, 95%"
SMALL_SAMPLE_THRESHOLD = 10


# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Rate:
    """A proportion that always shows what it was computed over."""

    numerator: int
    denominator: int
    #: 95% Wilson interval, or ``None`` when the denominator is zero.
    ci_low: float | None = None
    ci_high: float | None = None

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def small_sample(self) -> bool:
        return 0 < self.denominator < SMALL_SAMPLE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": None if self.value is None else round(self.value, 4),
            "percent": None if self.value is None else round(100 * self.value, 2),
            "numerator": self.numerator,
            "denominator": self.denominator,
            "ci_low": None if self.ci_low is None else round(self.ci_low, 4),
            "ci_high": None if self.ci_high is None else round(self.ci_high, 4),
            "ci_method": CI_METHOD if self.denominator else None,
            "small_sample": self.small_sample,
            "interpretation_warning": (
                f"only {self.denominator} run(s); treat this rate as indicative, not significant"
                if self.small_sample
                else None
            ),
        }


def make_rate(numerator: int, denominator: int) -> Rate:
    if denominator <= 0:
        return Rate(numerator, 0)
    low, high = wilson_interval(numerator, denominator)
    return Rate(numerator, denominator, low, high)


def wilson_interval(successes: int, trials: int, z: float = 1.959963985) -> tuple[float, float]:
    """Wilson score interval.

    Chosen over the normal approximation because benchmark samples are small and
    proportions sit near 0 or 1, where the normal interval produces bounds
    outside [0, 1].
    """
    if trials <= 0:
        return (0.0, 0.0)
    p = successes / trials
    denominator = 1 + z**2 / trials
    centre = p + z**2 / (2 * trials)
    spread = z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2))
    return (
        max(0.0, (centre - spread) / denominator),
        min(1.0, (centre + spread) / denominator),
    )


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """Unbiased pass@k for ``n`` samples of which ``c`` succeeded.

    Returns ``None`` when ``n < k``: estimating pass@k from fewer than k samples
    would be an extrapolation, not a measurement.
    """
    if k <= 0 or n < k:
        return None
    if c <= 0:
        return 0.0
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TaskMetrics:
    task_key: str
    task_id: str
    title: str = ""
    category: str = ""
    difficulty: str = ""
    attempts: int = 0
    solved: int = 0
    unsolved: int = 0
    timeout: int = 0
    agent_error: int = 0
    infra_error: int = 0
    invalid_task: int = 0
    grading_error: int = 0
    cancelled: int = 0
    regressions: int = 0
    mean_duration_s: float | None = None
    pass_at_1: float | None = None
    pass_at_k: dict[str, float] = field(default_factory=dict)

    @property
    def graded(self) -> int:
        return self.solved + self.unsolved

    @property
    def attributable(self) -> int:
        return self.solved + self.unsolved + self.timeout + self.agent_error

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["success_rate_attributable"] = make_rate(self.solved, self.attributable).to_dict()
        payload["success_rate_graded"] = make_rate(self.solved, self.graded).to_dict()
        return payload


@dataclass(slots=True)
class ExperimentMetrics:
    experiment_id: str
    name: str
    state: str
    agent_label: str
    agent_adapter: str
    agent_model: str | None
    is_simulated: bool
    provenance: str
    benchmark: str
    grading_version: str
    repetitions: int
    computed_at: str

    total_runs: int = 0
    finished_runs: int = 0
    pending_runs: int = 0
    active_runs: int = 0

    solved: int = 0
    unsolved: int = 0
    timeout: int = 0
    agent_error: int = 0
    infra_error: int = 0
    invalid_task: int = 0
    grading_error: int = 0
    cancelled: int = 0

    regression_count: int = 0
    infra_retry_total: int = 0

    duration_total_s: float = 0.0
    duration_mean_s: float | None = None
    duration_median_s: float | None = None
    duration_p90_s: float | None = None

    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd_total: float | None = None
    cost_sources: dict[str, int] = field(default_factory=dict)
    runs_without_usage: int = 0

    tasks: list[TaskMetrics] = field(default_factory=list)
    by_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_difficulty: dict[str, dict[str, Any]] = field(default_factory=dict)
    failure_categories: dict[str, int] = field(default_factory=dict)
    pass_at_k: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def graded(self) -> int:
        return self.solved + self.unsolved

    @property
    def attributable(self) -> int:
        return self.solved + self.unsolved + self.timeout + self.agent_error

    @property
    def excluded_from_rates(self) -> int:
        return self.infra_error + self.invalid_task + self.grading_error + self.cancelled

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "state": self.state,
            "agent": {
                "label": self.agent_label,
                "adapter": self.agent_adapter,
                "model": self.agent_model,
                "is_simulated": self.is_simulated,
            },
            "provenance": provenance_module.describe(self.provenance),
            "benchmark": self.benchmark,
            "grading_version": self.grading_version,
            "repetitions": self.repetitions,
            "computed_at": self.computed_at,
            "completeness": {
                "total_runs": self.total_runs,
                "finished_runs": self.finished_runs,
                "pending_runs": self.pending_runs,
                "active_runs": self.active_runs,
                "run_completeness": (
                    round(self.finished_runs / self.total_runs, 4) if self.total_runs else None
                ),
                "is_complete": self.total_runs > 0
                and self.pending_runs == 0
                and self.active_runs == 0,
            },
            "outcomes": {
                "solved": self.solved,
                "unsolved": self.unsolved,
                "timeout": self.timeout,
                "agent_error": self.agent_error,
                "infra_error": self.infra_error,
                "invalid_task": self.invalid_task,
                "grading_error": self.grading_error,
                "cancelled": self.cancelled,
            },
            "success_rate_attributable": make_rate(self.solved, self.attributable).to_dict(),
            "success_rate_graded": make_rate(self.solved, self.graded).to_dict(),
            "pass_at_1": make_rate(self.solved, self.attributable).to_dict(),
            "pass_at_k": {
                "values": self.pass_at_k,
                "estimator": PASS_AT_K_ESTIMATOR if self.pass_at_k else None,
                "note": (
                    "averaged over tasks; reported only for k <= repetitions"
                    if self.pass_at_k
                    else f"not reported: the experiment ran {self.repetitions} repetition(s) per task"
                ),
            },
            "reliability": {
                "regression_count": self.regression_count,
                "regression_rate": make_rate(self.regression_count, self.graded).to_dict(),
                "timeout_rate": make_rate(self.timeout, self.attributable).to_dict(),
                "infra_failure_rate": make_rate(self.infra_error, self.finished_runs).to_dict(),
                "infra_retry_total": self.infra_retry_total,
                "excluded_from_rates": self.excluded_from_rates,
            },
            "runtime": {
                "total_s": round(self.duration_total_s, 2),
                "mean_s": self.duration_mean_s,
                "median_s": self.duration_median_s,
                "p90_s": self.duration_p90_s,
            },
            "usage": {
                "input_tokens": self.tokens_input,
                "output_tokens": self.tokens_output,
                "cost_usd_total": self.cost_usd_total,
                "cost_sources": self.cost_sources,
                "runs_without_reported_usage": self.runs_without_usage,
                "note": (
                    "token and cost figures cover only runs where the adapter reported them; "
                    "cost_sources records how each figure was derived"
                ),
            },
            "by_category": self.by_category,
            "by_difficulty": self.by_difficulty,
            "failure_categories": self.failure_categories,
            "tasks": [task.to_dict() for task in self.tasks],
            "warnings": self.warnings,
        }
        return payload


# --------------------------------------------------------------------------- #
def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def _regression_detected(grading: GradingResultRecord | None) -> bool:
    if grading is None:
        return False
    for layer in grading.layers or []:
        if isinstance(layer, dict) and layer.get("name") == "pass_to_pass":
            return str(layer.get("status")) == "failed"
    return False


def experiment_metrics(session: Session, experiment_id: str) -> ExperimentMetrics:
    experiment = session.get(ExperimentRecord, experiment_id)
    if experiment is None:
        raise LookupError(f"unknown experiment: {experiment_id}")

    snapshot = experiment.agent_snapshot or {}
    metrics = ExperimentMetrics(
        experiment_id=experiment.id,
        name=experiment.name,
        state=experiment.state,
        agent_label=f"{snapshot.get('name', '?')}@{snapshot.get('version', '?')}",
        agent_adapter=str(snapshot.get("adapter", "?")),
        agent_model=snapshot.get("model"),
        is_simulated=bool(snapshot.get("is_simulated")),
        provenance=str(provenance_module.from_agent_snapshot(snapshot)),
        benchmark=f"{experiment.benchmark_id}@{experiment.benchmark_version}",
        grading_version=experiment.grading_version,
        repetitions=experiment.repetitions,
        computed_at=datetime.now(UTC).isoformat(),
    )

    runs = list(
        session.execute(
            select(RunRecord)
            .where(RunRecord.experiment_id == experiment_id)
            .order_by(RunRecord.task_id.asc(), RunRecord.attempt_index.asc())
        ).scalars()
    )
    metrics.total_runs = len(runs)
    if not runs:
        metrics.warnings.append("this experiment has no runs yet")
        return metrics

    grading_rows = {
        row.run_id: row
        for row in session.execute(
            select(GradingResultRecord).where(
                GradingResultRecord.run_id.in_([run.id for run in runs])
            )
        ).scalars()
    }
    annotations = {
        row.run_id: row
        for row in session.execute(
            select(FailureAnnotationRecord).where(
                FailureAnnotationRecord.run_id.in_([run.id for run in runs]),
                FailureAnnotationRecord.superseded.is_(False),
            )
        ).scalars()
    }

    per_task: dict[str, TaskMetrics] = {}
    durations: list[float] = []
    task_durations: dict[str, list[float]] = {}
    tokens_in = tokens_out = 0
    saw_usage = False
    cost_total = 0.0
    saw_cost = False

    for run in runs:
        state = RunState(run.state)
        if state is RunState.PENDING:
            metrics.pending_runs += 1
        elif state.is_terminal:
            metrics.finished_runs += 1
        else:
            metrics.active_runs += 1

        metrics.infra_retry_total += run.infra_attempts

        task = per_task.setdefault(
            run.task_key,
            TaskMetrics(
                task_key=run.task_key,
                task_id=run.task_id,
                title=(run.task_snapshot or {}).get("title", ""),
                category=(run.task_snapshot or {}).get("category", ""),
                difficulty=(run.task_snapshot or {}).get("difficulty", ""),
            ),
        )

        if not state.is_terminal:
            continue

        outcome = RunOutcome(run.outcome) if run.outcome else RunOutcome.INFRA_ERROR
        task.attempts += 1
        setattr(metrics, outcome.value, getattr(metrics, outcome.value, 0) + 1)
        setattr(task, outcome.value, getattr(task, outcome.value, 0) + 1)

        grading = grading_rows.get(run.id)
        if _regression_detected(grading):
            metrics.regression_count += 1
            task.regressions += 1

        total_s = float((run.timings or {}).get("total_s") or 0.0)
        if total_s > 0:
            durations.append(total_s)
            task_durations.setdefault(run.task_key, []).append(total_s)

        usage = run.usage or {}
        if usage.get("reported_by_provider"):
            saw_usage = True
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
        else:
            metrics.runs_without_usage += 1

        if run.cost_usd is not None:
            saw_cost = True
            cost_total += float(run.cost_usd)
            source = run.cost_source or "unspecified"
            metrics.cost_sources[source] = metrics.cost_sources.get(source, 0) + 1

        if outcome is not RunOutcome.SOLVED:
            annotation = annotations.get(run.id)
            category = str(annotation.category) if annotation else str(FailureCategory.UNKNOWN)
            metrics.failure_categories[category] = metrics.failure_categories.get(category, 0) + 1

    for key, task in per_task.items():
        task.mean_duration_s = (
            round(mean(task_durations[key]), 3) if task_durations.get(key) else None
        )
        task.pass_at_1 = pass_at_k(task.attributable, task.solved, 1)
        for k in (2, 3, 5, 10):
            if k <= metrics.repetitions and task.attributable >= k:
                value = pass_at_k(task.attributable, task.solved, k)
                if value is not None:
                    task.pass_at_k[f"pass@{k}"] = round(value, 4)

    metrics.tasks = sorted(per_task.values(), key=lambda t: t.task_key)

    for k in (2, 3, 5, 10):
        label = f"pass@{k}"
        values = [task.pass_at_k[label] for task in metrics.tasks if label in task.pass_at_k]
        if values and len(values) == len(metrics.tasks):
            metrics.pass_at_k[label] = round(mean(values), 4)

    metrics.by_category = _group(metrics.tasks, lambda t: t.category or "uncategorised")
    metrics.by_difficulty = _group(metrics.tasks, lambda t: t.difficulty or "unspecified")

    metrics.duration_total_s = round(sum(durations), 2)
    metrics.duration_mean_s = round(mean(durations), 3) if durations else None
    metrics.duration_median_s = round(median(durations), 3) if durations else None
    metrics.duration_p90_s = _percentile(durations, 0.9)

    metrics.tokens_input = tokens_in if saw_usage else None
    metrics.tokens_output = tokens_out if saw_usage else None
    metrics.cost_usd_total = round(cost_total, 6) if saw_cost else None

    metrics.warnings.extend(_warnings_for(metrics, experiment))
    return metrics


def _group(tasks: list[TaskMetrics], key: Any) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[TaskMetrics]] = {}
    for task in tasks:
        buckets.setdefault(key(task), []).append(task)
    out: dict[str, dict[str, Any]] = {}
    for name, group in sorted(buckets.items()):
        solved = sum(t.solved for t in group)
        attributable = sum(t.attributable for t in group)
        graded = sum(t.graded for t in group)
        out[name] = {
            "tasks": len(group),
            "attempts": sum(t.attempts for t in group),
            "solved": solved,
            "success_rate_attributable": make_rate(solved, attributable).to_dict(),
            "success_rate_graded": make_rate(solved, graded).to_dict(),
            "regressions": sum(t.regressions for t in group),
        }
    return out


def _warnings_for(metrics: ExperimentMetrics, experiment: ExperimentRecord) -> list[str]:
    warnings: list[str] = []
    if metrics.pending_runs or metrics.active_runs:
        warnings.append(
            f"incomplete experiment: {metrics.pending_runs} pending and "
            f"{metrics.active_runs} in-flight run(s); rates cover finished runs only"
        )
    kind = provenance_module.Provenance(metrics.provenance)
    if not kind.is_model_result:
        warnings.append(f"{kind.label}: {kind.caveat}")
    if metrics.infra_error:
        warnings.append(
            f"{metrics.infra_error} run(s) failed for infrastructure reasons and are excluded "
            "from success rates"
        )
    if metrics.invalid_task:
        warnings.append(
            f"{metrics.invalid_task} run(s) hit a broken task environment; fix the task before "
            "drawing conclusions"
        )
    if metrics.grading_error:
        warnings.append(
            f"{metrics.grading_error} run(s) could not be graded; those are grading "
            "infrastructure problems, not agent failures"
        )
    if metrics.attributable and metrics.attributable < SMALL_SAMPLE_THRESHOLD:
        warnings.append(
            f"only {metrics.attributable} scored run(s): differences of a few percent are noise"
        )
    if metrics.repetitions == 1:
        warnings.append(
            "a single repetition per task cannot show run-to-run variance; raise repetitions "
            "to measure reliability"
        )
    if ExperimentState(experiment.state) is ExperimentState.CANCELLED:
        warnings.append("the experiment was cancelled; its task coverage is partial by definition")
    return warnings


# --------------------------------------------------------------------------- #
def platform_overview(session: Session) -> dict[str, Any]:
    """Aggregate figures for the overview screen, computed from stored rows."""
    from sqlalchemy import func

    experiments = list(
        session.execute(
            select(ExperimentRecord).order_by(ExperimentRecord.created_at.desc()).limit(200)
        ).scalars()
    )
    run_rows = session.execute(
        select(RunRecord.state, RunRecord.outcome, func.count(RunRecord.id)).group_by(
            RunRecord.state, RunRecord.outcome
        )
    ).all()

    by_state: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    total = 0
    for state, outcome, count in run_rows:
        count = int(count or 0)
        total += count
        by_state[state] = by_state.get(state, 0) + count
        if outcome:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + count

    solved = by_outcome.get(RunOutcome.SOLVED, 0)
    attributable = sum(
        by_outcome.get(o, 0)
        for o in (
            RunOutcome.SOLVED,
            RunOutcome.UNSOLVED,
            RunOutcome.TIMEOUT,
            RunOutcome.AGENT_ERROR,
        )
    )
    annotation_rows = session.execute(
        select(FailureAnnotationRecord.category, func.count(FailureAnnotationRecord.id))
        .where(FailureAnnotationRecord.superseded.is_(False))
        .group_by(FailureAnnotationRecord.category)
    ).all()

    return {
        "experiments": {
            "total": len(experiments),
            "by_state": _count_by(experiments, lambda e: e.state),
            "recent": [
                {
                    "id": e.id,
                    "name": e.name,
                    "state": e.state,
                    "benchmark": f"{e.benchmark_id}@{e.benchmark_version}",
                    "agent": f"{(e.agent_snapshot or {}).get('name')}@"
                    f"{(e.agent_snapshot or {}).get('version')}",
                    "is_simulated": bool((e.agent_snapshot or {}).get("is_simulated")),
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in experiments[:10]
            ],
        },
        "runs": {
            "total": total,
            "by_state": by_state,
            "by_outcome": by_outcome,
            "active": sum(count for state, count in by_state.items() if RunState(state).is_active),
            "overall_success_rate_attributable": make_rate(solved, attributable).to_dict(),
        },
        "failure_categories": {category: int(count or 0) for category, count in annotation_rows},
        "computed_at": datetime.now(UTC).isoformat(),
    }


def _count_by(items: list[Any], key: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = str(key(item))
        out[value] = out.get(value, 0) + 1
    return out
