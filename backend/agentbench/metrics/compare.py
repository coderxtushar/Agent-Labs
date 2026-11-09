"""Baseline vs. candidate comparison.

The question this answers is "did my change actually help?", and the honest
answer usually includes "not enough evidence to say".  Three guards make that
possible:

1. **Comparability is checked, not assumed.**  Differing task sets, task
   versions, grading versions or benchmark versions produce explicit blockers;
   the comparison still renders, but flagged as not directly comparable.
2. **Per-task movement is reported, not just the aggregate.**  A net-zero
   success rate can hide three fixes and three breakages, which is the single
   most useful thing to know about a change.
3. **No significance claims.**  A two-proportion z-test is reported *only* when
   both arms have enough samples, and its wording stays descriptive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from agentbench.metrics.compute import ExperimentMetrics, Rate, experiment_metrics, make_rate
from agentbench.schemas import provenance

MIN_SAMPLES_FOR_TEST = 10


@dataclass(slots=True)
class TaskDelta:
    task_key: str
    title: str
    category: str
    baseline_solved: int
    baseline_attempts: int
    candidate_solved: int
    candidate_attempts: int

    @property
    def baseline_rate(self) -> float | None:
        return self.baseline_solved / self.baseline_attempts if self.baseline_attempts else None

    @property
    def candidate_rate(self) -> float | None:
        return self.candidate_solved / self.candidate_attempts if self.candidate_attempts else None

    @property
    def movement(self) -> str:
        base, cand = self.baseline_rate, self.candidate_rate
        if base is None or cand is None:
            return "not_comparable"
        if cand > base:
            return "newly_solved" if base == 0 else "improved"
        if cand < base:
            return "newly_broken" if cand == 0 else "degraded"
        return "unchanged"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_key": self.task_key,
            "title": self.title,
            "category": self.category,
            "baseline": {
                "solved": self.baseline_solved,
                "attempts": self.baseline_attempts,
                "rate": None if self.baseline_rate is None else round(self.baseline_rate, 4),
            },
            "candidate": {
                "solved": self.candidate_solved,
                "attempts": self.candidate_attempts,
                "rate": None if self.candidate_rate is None else round(self.candidate_rate, 4),
            },
            "movement": self.movement,
            "delta": (
                None
                if self.baseline_rate is None or self.candidate_rate is None
                else round(self.candidate_rate - self.baseline_rate, 4)
            ),
        }


@dataclass(slots=True)
class Comparison:
    baseline: ExperimentMetrics
    candidate: ExperimentMetrics
    comparable: bool
    blockers: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    task_deltas: list[TaskDelta] = field(default_factory=list)

    def _bucket(self, movement: str) -> list[str]:
        return [delta.task_key for delta in self.task_deltas if delta.movement == movement]

    def to_dict(self) -> dict[str, Any]:
        base_rate = make_rate(self.baseline.solved, self.baseline.attributable)
        cand_rate = make_rate(self.candidate.solved, self.candidate.attributable)
        significance = _two_proportion_test(base_rate, cand_rate)

        return {
            "comparable": self.comparable,
            "blockers": self.blockers,
            "caveats": self.caveats,
            "baseline": {
                "experiment_id": self.baseline.experiment_id,
                "name": self.baseline.name,
                "agent": self.baseline.agent_label,
                "model": self.baseline.agent_model,
                "is_simulated": self.baseline.is_simulated,
                "provenance": provenance.describe(self.baseline.provenance),
                "success_rate_attributable": base_rate.to_dict(),
                "outcomes": self.baseline.to_dict()["outcomes"],
                "runtime": self.baseline.to_dict()["runtime"],
                "usage": self.baseline.to_dict()["usage"],
                "failure_categories": self.baseline.failure_categories,
            },
            "candidate": {
                "experiment_id": self.candidate.experiment_id,
                "name": self.candidate.name,
                "agent": self.candidate.agent_label,
                "model": self.candidate.agent_model,
                "is_simulated": self.candidate.is_simulated,
                "provenance": provenance.describe(self.candidate.provenance),
                "success_rate_attributable": cand_rate.to_dict(),
                "outcomes": self.candidate.to_dict()["outcomes"],
                "runtime": self.candidate.to_dict()["runtime"],
                "usage": self.candidate.to_dict()["usage"],
                "failure_categories": self.candidate.failure_categories,
            },
            "delta": {
                "success_rate": (
                    None
                    if base_rate.value is None or cand_rate.value is None
                    else round(cand_rate.value - base_rate.value, 4)
                ),
                "solved": self.candidate.solved - self.baseline.solved,
                "regressions": self.candidate.regression_count - self.baseline.regression_count,
                "timeouts": self.candidate.timeout - self.baseline.timeout,
                "agent_errors": self.candidate.agent_error - self.baseline.agent_error,
                "runtime_total_s": round(
                    self.candidate.duration_total_s - self.baseline.duration_total_s, 2
                ),
                "cost_usd": _delta_or_none(
                    self.baseline.cost_usd_total, self.candidate.cost_usd_total
                ),
            },
            "movement": {
                "newly_solved": self._bucket("newly_solved"),
                "newly_broken": self._bucket("newly_broken"),
                "improved": self._bucket("improved"),
                "degraded": self._bucket("degraded"),
                "unchanged": self._bucket("unchanged"),
                "not_comparable": self._bucket("not_comparable"),
            },
            "task_deltas": [delta.to_dict() for delta in self.task_deltas],
            "by_category": _category_delta(self.baseline, self.candidate),
            "statistical_note": significance,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        newly_solved = self._bucket("newly_solved")
        newly_broken = self._bucket("newly_broken")
        base = make_rate(self.baseline.solved, self.baseline.attributable).value
        cand = make_rate(self.candidate.solved, self.candidate.attributable).value

        parts: list[str] = []
        if base is None or cand is None:
            parts.append("Neither arm has enough scored runs to state a success rate.")
        else:
            parts.append(
                f"Success rate moved from {base:.0%} ({self.baseline.solved}/"
                f"{self.baseline.attributable}) to {cand:.0%} ({self.candidate.solved}/"
                f"{self.candidate.attributable})."
            )
        if newly_solved:
            parts.append(f"Newly solved: {', '.join(newly_solved)}.")
        if newly_broken:
            parts.append(f"Newly broken: {', '.join(newly_broken)}.")
        if not newly_solved and not newly_broken:
            parts.append("No task changed from solved to unsolved or back.")
        if not self.comparable:
            parts.append(
                "These arms are not directly comparable: " + "; ".join(self.blockers) + "."
            )
        if self.caveats:
            parts.append("Needs more testing: " + "; ".join(self.caveats) + ".")
        return " ".join(parts)


def _delta_or_none(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    return round(candidate - baseline, 6)


def _category_delta(
    baseline: ExperimentMetrics, candidate: ExperimentMetrics
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for category in sorted(set(baseline.by_category) | set(candidate.by_category)):
        base = baseline.by_category.get(category)
        cand = candidate.by_category.get(category)
        base_value = (base or {}).get("success_rate_attributable", {}).get("value")
        cand_value = (cand or {}).get("success_rate_attributable", {}).get("value")
        out[category] = {
            "baseline": base,
            "candidate": cand,
            "delta": _delta_or_none(base_value, cand_value),
        }
    return out


def _two_proportion_test(baseline: Rate, candidate: Rate) -> dict[str, Any]:
    """Descriptive two-proportion z-test, reported only when it is meaningful."""
    if baseline.denominator < MIN_SAMPLES_FOR_TEST or candidate.denominator < MIN_SAMPLES_FOR_TEST:
        return {
            "test": None,
            "reason": (
                f"not computed: a two-proportion test needs at least {MIN_SAMPLES_FOR_TEST} "
                f"scored runs per arm (baseline has {baseline.denominator}, candidate has "
                f"{candidate.denominator})"
            ),
            "claims_significance": False,
        }

    p1 = baseline.numerator / baseline.denominator
    p2 = candidate.numerator / candidate.denominator
    pooled = (baseline.numerator + candidate.numerator) / (
        baseline.denominator + candidate.denominator
    )
    standard_error = math.sqrt(
        pooled * (1 - pooled) * (1 / baseline.denominator + 1 / candidate.denominator)
    )
    if standard_error == 0:
        return {
            "test": "two-proportion z-test",
            "reason": "not computed: zero variance (both arms identical and saturated)",
            "claims_significance": False,
        }
    z = (p2 - p1) / standard_error
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    return {
        "test": "two-proportion z-test (two-sided, pooled)",
        "z": round(z, 4),
        "p_value": round(p_value, 4),
        "alpha_reference": 0.05,
        "below_alpha": p_value < 0.05,
        "claims_significance": False,
        "note": (
            "descriptive only. Benchmark runs are not independent identically distributed "
            "samples across tasks, so this p-value should guide how much more testing to do, "
            "not be reported as a significance result."
        ),
    }


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# --------------------------------------------------------------------------- #
def compare_experiments(session: Session, baseline_id: str, candidate_id: str) -> Comparison:
    baseline = experiment_metrics(session, baseline_id)
    candidate = experiment_metrics(session, candidate_id)

    blockers: list[str] = []
    caveats: list[str] = []

    base_keys = {task.task_key for task in baseline.tasks}
    cand_keys = {task.task_key for task in candidate.tasks}
    if base_keys != cand_keys:
        only_base = sorted(base_keys - cand_keys)
        only_cand = sorted(cand_keys - base_keys)
        blockers.append(
            "the two arms do not cover the same task set"
            + (f" (baseline only: {only_base})" if only_base else "")
            + (f" (candidate only: {only_cand})" if only_cand else "")
        )
    if baseline.grading_version != candidate.grading_version:
        blockers.append(
            f"grading versions differ ({baseline.grading_version} vs "
            f"{candidate.grading_version}); verdicts were produced by different rules"
        )
    if baseline.benchmark != candidate.benchmark:
        blockers.append(
            f"benchmark versions differ ({baseline.benchmark} vs {candidate.benchmark})"
        )
    if baseline.provenance != candidate.provenance:
        blockers.append(
            f"the arms have different result provenance ({baseline.provenance} vs "
            f"{candidate.provenance}); they measure different things"
        )

    base_dict = baseline.to_dict()
    cand_dict = candidate.to_dict()
    if not base_dict["completeness"]["is_complete"] or not cand_dict["completeness"]["is_complete"]:
        caveats.append("at least one arm is still incomplete")
    if baseline.repetitions == 1 or candidate.repetitions == 1:
        caveats.append("one repetition per task means a single flaky run can flip a task's verdict")
    if baseline.infra_error or candidate.infra_error:
        caveats.append(
            f"infrastructure failures occurred (baseline {baseline.infra_error}, candidate "
            f"{candidate.infra_error}) and are excluded from the rates"
        )

    base_by_task = {task.task_key: task for task in baseline.tasks}
    cand_by_task = {task.task_key: task for task in candidate.tasks}
    deltas: list[TaskDelta] = []
    for key in sorted(base_keys | cand_keys):
        base_task = base_by_task.get(key)
        cand_task = cand_by_task.get(key)
        reference = base_task or cand_task
        assert reference is not None
        deltas.append(
            TaskDelta(
                task_key=key,
                title=reference.title,
                category=reference.category,
                baseline_solved=base_task.solved if base_task else 0,
                baseline_attempts=base_task.attributable if base_task else 0,
                candidate_solved=cand_task.solved if cand_task else 0,
                candidate_attempts=cand_task.attributable if cand_task else 0,
            )
        )

    return Comparison(
        baseline=baseline,
        candidate=candidate,
        comparable=not blockers,
        blockers=blockers,
        caveats=caveats,
        task_deltas=deltas,
    )
