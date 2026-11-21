"""Cross-experiment views: leaderboard, agent profiles, task history, analytics.

Everything here is read-only and computed from stored runs. The same honesty
rules as :mod:`agentbench.metrics.compute` apply:

* A success rate is ``solved / attributable`` where *attributable* is solved,
  unsolved, timeout and agent_error. Infrastructure failures, invalid tasks,
  grading errors and cancellations are counted separately, never as failures.
* Every rate carries its numerator, denominator and Wilson interval.
* Results are grouped by provenance, so a simulated pipeline check is never
  merged into a live model's numbers.
* Ordering a leaderboard is not a significance claim; rows whose intervals
  overlap the leader's are flagged so the UI can say so.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from agentbench.db.models import (
    ExperimentRecord,
    FailureAnnotationRecord,
    RunEventRecord,
    RunRecord,
)
from agentbench.metrics.compare import compare_experiments
from agentbench.metrics.compute import _percentile, experiment_metrics, make_rate
from agentbench.schemas import provenance as provenance_module
from agentbench.schemas.enums import FailureCategory, RunOutcome, RunState

#: Rates below this many attributable runs are flagged as small-sample.
SIGNIFICANCE_MIN_RUNS = 30
MAX_COMPARE_ARMS = 6

ORDERING_NOTE = (
    "Rows are ordered by observed success rate. Ordering is not a significance claim: "
    "rows whose 95% interval overlaps the leader's are not distinguishable from it."
)
RUNTIME_NOTE = (
    "Runtime covers agent-attributable runs (solved, unsolved, timeout, agent error) "
    "with a recorded total duration."
)


def _provenance_of(experiment: ExperimentRecord) -> str:
    return str(provenance_module.from_agent_snapshot(experiment.agent_snapshot or {}))


def _is_model_result(provenance: str) -> bool:
    return bool(provenance_module.describe(provenance)["is_model_result"])


def _agent_label(experiment: ExperimentRecord) -> str:
    snapshot = experiment.agent_snapshot or {}
    return f"{snapshot.get('name', '?')}@{snapshot.get('version', '?')}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _terminal(run: RunRecord) -> bool:
    return RunState(run.state).is_terminal


def _outcome(run: RunRecord) -> RunOutcome:
    # A terminal run without an outcome is treated as infrastructure, exactly as
    # `experiment_metrics` does, so it can never inflate or deflate a rate.
    return RunOutcome(run.outcome) if run.outcome else RunOutcome.INFRA_ERROR


def _duration(run: RunRecord) -> float | None:
    value = (run.timings or {}).get("total_s")
    try:
        seconds = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return seconds if seconds and seconds > 0 else None


# --------------------------------------------------------------------------- #
@dataclass
class Tally:
    """Outcome counts and runtimes for any set of finished runs."""

    outcomes: Counter[str] = field(default_factory=Counter)
    durations: list[float] = field(default_factory=list)
    tasks_attempted: set[str] = field(default_factory=set)
    tasks_passed: set[str] = field(default_factory=set)
    last_finished_at: datetime | None = None

    def add(self, run: RunRecord) -> None:
        outcome = _outcome(run)
        self.outcomes[outcome.value] += 1
        if outcome.is_attributable_to_agent:
            self.tasks_attempted.add(run.task_key)
            seconds = _duration(run)
            if seconds is not None:
                self.durations.append(seconds)
        if outcome is RunOutcome.SOLVED:
            self.tasks_passed.add(run.task_key)
        finished = run.finished_at
        if finished is not None:
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=UTC)
            if self.last_finished_at is None or finished > self.last_finished_at:
                self.last_finished_at = finished

    @property
    def attributable(self) -> int:
        return sum(self.outcomes[o.value] for o in RunOutcome if o.is_attributable_to_agent)

    @property
    def finished(self) -> int:
        return sum(self.outcomes.values())

    def to_dict(self) -> dict[str, Any]:
        attributable = self.attributable
        solved = self.outcomes[RunOutcome.SOLVED.value]
        excluded = self.finished - attributable
        return {
            "finished_runs": self.finished,
            "attributable_runs": attributable,
            "excluded_runs": excluded,
            "outcomes": {o.value: self.outcomes[o.value] for o in RunOutcome},
            "tasks_attempted": len(self.tasks_attempted),
            "tasks_passed": len(self.tasks_passed),
            "success_rate": make_rate(solved, attributable).to_dict(),
            "timeout_rate": make_rate(
                self.outcomes[RunOutcome.TIMEOUT.value], attributable
            ).to_dict(),
            "error_rate": make_rate(
                self.outcomes[RunOutcome.AGENT_ERROR.value], attributable
            ).to_dict(),
            "runtime": runtime_stats(self.durations),
            "last_finished_at": _iso(self.last_finished_at),
        }


def runtime_stats(durations: list[float]) -> dict[str, Any]:
    return {
        "n": len(durations),
        "mean_s": round(mean(durations), 3) if durations else None,
        "median_s": round(median(durations), 3) if durations else None,
        "p90_s": _percentile(durations, 0.9),
        "min_s": round(min(durations), 3) if durations else None,
        "max_s": round(max(durations), 3) if durations else None,
    }


def _finished_runs(session: Session, experiment_ids: list[str]) -> list[RunRecord]:
    if not experiment_ids:
        return []
    rows = session.execute(
        select(RunRecord)
        .options(defer(RunRecord.patch))
        .where(RunRecord.experiment_id.in_(experiment_ids))
        .order_by(RunRecord.created_at.asc())
    ).scalars()
    return [run for run in rows if _terminal(run)]


def _effective_categories(session: Session, run_ids: list[str]) -> dict[str, str]:
    if not run_ids:
        return {}
    rows = session.execute(
        select(FailureAnnotationRecord.run_id, FailureAnnotationRecord.category).where(
            FailureAnnotationRecord.run_id.in_(run_ids),
            FailureAnnotationRecord.superseded.is_(False),
        )
    ).all()
    return {run_id: str(category) for run_id, category in rows}


def _failure_distribution(runs: list[RunRecord], categories: dict[str, str]) -> dict[str, int]:
    """Categories of non-solved runs; a run nobody classified is ``unknown``, not guessed."""
    counts: Counter[str] = Counter()
    for run in runs:
        if _outcome(run) is RunOutcome.SOLVED:
            continue
        counts[categories.get(run.id, str(FailureCategory.UNKNOWN))] += 1
    return dict(counts.most_common())


def _flag_overlaps(rows: list[dict[str, Any]]) -> None:
    leader = next((row for row in rows if row["success_rate"]["denominator"] > 0), None)
    for row in rows:
        rate = row["success_rate"]
        if leader is None or row is leader or rate["denominator"] == 0:
            row["ci_overlaps_leader"] = None
            continue
        lead = leader["success_rate"]
        row["ci_overlaps_leader"] = bool(
            rate["ci_high"] is not None
            and lead["ci_low"] is not None
            and rate["ci_high"] >= lead["ci_low"]
        )


def _sort_key(row: dict[str, Any]) -> tuple[int, float, int]:
    rate = row["success_rate"]
    value = rate["value"]
    return (0 if value is not None else 1, -(value or 0.0), -rate["denominator"])


# --------------------------------------------------------------------------- #
def leaderboard(
    session: Session,
    *,
    benchmark_id: str | None = None,
    experiment_ids: list[str] | None = None,
    include_non_model: bool = False,
) -> dict[str, Any]:
    """One row per (agent configuration, provenance) across matching experiments."""
    all_experiments = list(
        session.execute(select(ExperimentRecord).order_by(ExperimentRecord.created_at.desc()))
        .unique()
        .scalars()
    )
    selected = [
        e
        for e in all_experiments
        if (benchmark_id is None or e.benchmark_id == benchmark_id)
        and (not experiment_ids or e.id in experiment_ids)
    ]
    by_id = {e.id: e for e in selected}
    runs = _finished_runs(session, list(by_id))

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        experiment = by_id[run.experiment_id]
        provenance = _provenance_of(experiment)
        if not include_non_model and not _is_model_result(provenance):
            continue
        key = (experiment.agent_config_id, provenance)
        group = groups.setdefault(
            key,
            {
                "experiment": experiment,
                "tally": Tally(),
                "experiments": set(),
                "benchmarks": set(),
            },
        )
        group["tally"].add(run)
        group["experiments"].add(experiment.id)
        group["benchmarks"].add(f"{experiment.benchmark_id}@{experiment.benchmark_version}")

    rows: list[dict[str, Any]] = []
    for (agent_config_id, provenance), group in groups.items():
        experiment = group["experiment"]
        snapshot = experiment.agent_snapshot or {}
        row = {
            "agent_config_id": agent_config_id,
            "label": _agent_label(experiment),
            "name": snapshot.get("name"),
            "version": snapshot.get("version"),
            "model": snapshot.get("model"),
            "adapter": snapshot.get("adapter"),
            "provenance": provenance_module.describe(provenance),
            "benchmarks": sorted(group["benchmarks"]),
            "experiment_count": len(group["experiments"]),
            **group["tally"].to_dict(),
        }
        rows.append(row)

    rows.sort(key=_sort_key)
    _flag_overlaps(rows)
    for position, row in enumerate(rows, start=1):
        row["position"] = position

    enough = any(row["attributable_runs"] >= SIGNIFICANCE_MIN_RUNS for row in rows)
    warnings: list[str] = []
    if not rows:
        warnings.append("no finished runs match these filters")
    if rows and not enough:
        warnings.append(
            f"every row has fewer than {SIGNIFICANCE_MIN_RUNS} attributable runs; "
            "differences between rows are indicative only"
        )
    benchmark_mix = {b for row in rows for b in row["benchmarks"]}
    if len(benchmark_mix) > 1:
        warnings.append(
            "rows span more than one benchmark; filter to one suite for a like-for-like ranking"
        )
    hidden = 0
    if not include_non_model:
        hidden = sum(1 for e in selected if not _is_model_result(_provenance_of(e)))

    return {
        "rows": rows,
        "ordering_note": ORDERING_NOTE,
        "runtime_note": RUNTIME_NOTE,
        "warnings": warnings,
        "claims_significance": False,
        "include_non_model": include_non_model,
        "hidden_non_model_experiments": hidden,
        "filters": {
            "benchmarks": sorted({e.benchmark_id for e in all_experiments}),
            "experiments": [
                {
                    "id": e.id,
                    "name": e.name,
                    "agent": _agent_label(e),
                    "benchmark": f"{e.benchmark_id}@{e.benchmark_version}",
                    "provenance": _provenance_of(e),
                    "created_at": _iso(e.created_at),
                }
                for e in all_experiments
            ],
        },
        "computed_at": datetime.now(UTC).isoformat(),
    }


# --------------------------------------------------------------------------- #
def agent_profile(session: Session, agent_config_id: str) -> dict[str, Any]:
    experiments = list(
        session.execute(
            select(ExperimentRecord)
            .where(ExperimentRecord.agent_config_id == agent_config_id)
            .order_by(ExperimentRecord.created_at.asc())
        )
        .unique()
        .scalars()
    )
    by_id = {e.id: e for e in experiments}
    runs = _finished_runs(session, list(by_id))
    categories = _effective_categories(session, [run.id for run in runs])

    per_provenance: dict[str, Tally] = {}
    per_experiment: dict[str, Tally] = {}
    per_task: dict[str, dict[str, Any]] = {}
    per_category: dict[str, Tally] = {}
    durations: list[dict[str, Any]] = []

    for run in runs:
        provenance = _provenance_of(by_id[run.experiment_id])
        per_provenance.setdefault(provenance, Tally()).add(run)
        per_experiment.setdefault(run.experiment_id, Tally()).add(run)
        task = per_task.setdefault(
            f"{provenance}|{run.task_key}",
            {
                "task_key": run.task_key,
                "task_id": run.task_id,
                "provenance": provenance,
                "tally": Tally(),
            },
        )
        task["tally"].add(run)
        category = _task_category(run)
        per_category.setdefault(f"{provenance}|{category}", Tally()).add(run)
        outcome = _outcome(run)
        seconds = _duration(run)
        if outcome.is_attributable_to_agent and seconds is not None:
            durations.append(
                {
                    "run_id": run.id,
                    "task_key": run.task_key,
                    "outcome": outcome.value,
                    "total_s": round(seconds, 3),
                    "provenance": provenance,
                }
            )

    model_provenances = [p for p in per_provenance if _is_model_result(p)]
    evidence_scope = model_provenances or list(per_provenance)

    tasks_out: list[dict[str, Any]] = []
    for task in per_task.values():
        tally: Tally = task["tally"]
        tasks_out.append(
            {
                "task_key": task["task_key"],
                "task_id": task["task_id"],
                "category": _category_for_key(runs, task["task_key"]),
                "provenance": task["provenance"],
                **tally.to_dict(),
            }
        )
    tasks_out.sort(key=lambda t: (t["provenance"], t["task_key"]))

    strengths: list[dict[str, Any]] = []
    weaknesses: list[dict[str, Any]] = []
    for task in tasks_out:
        if task["provenance"] not in evidence_scope or task["attributable_runs"] == 0:
            continue
        solved = task["outcomes"]["solved"]
        entry = {
            "task_key": task["task_key"],
            "solved": solved,
            "attempts": task["attributable_runs"],
            "evidence": f"solved {solved} of {task['attributable_runs']} attributable attempt(s)",
        }
        (strengths if solved > 0 else weaknesses).append(entry)

    total_evidence = sum(per_provenance[p].attributable for p in evidence_scope)
    evidence_note = f"Based on {total_evidence} attributable run(s). " + (
        "Too few runs to separate skill from chance; repeat the experiment to firm this up."
        if total_evidence < SIGNIFICANCE_MIN_RUNS
        else "Treat per-task verdicts with fewer than 5 attempts as indicative."
    )
    if not model_provenances and per_provenance:
        evidence_note += " These runs are not model results (simulated or offline)."

    history = []
    for experiment in experiments:
        tally = per_experiment.get(experiment.id, Tally())
        history.append(
            {
                "experiment_id": experiment.id,
                "name": experiment.name,
                "state": experiment.state,
                "benchmark": f"{experiment.benchmark_id}@{experiment.benchmark_version}",
                "provenance": _provenance_of(experiment),
                "repetitions": experiment.repetitions,
                "created_at": _iso(experiment.created_at),
                "finished_at": _iso(experiment.finished_at),
                **tally.to_dict(),
            }
        )

    return {
        "agent_config_id": agent_config_id,
        "summary": [
            {"provenance": provenance_module.describe(p), **t.to_dict()}
            for p, t in per_provenance.items()
        ],
        "history": history,
        "tasks": tasks_out,
        "by_category": [
            {"provenance": key.split("|", 1)[0], "category": key.split("|", 1)[1], **t.to_dict()}
            for key, t in sorted(per_category.items())
        ],
        "durations": durations,
        "failure_categories": _failure_distribution(
            [r for r in runs if _provenance_of(by_id[r.experiment_id]) in evidence_scope],
            categories,
        ),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "evidence_note": evidence_note,
        "runtime_note": RUNTIME_NOTE,
        "claims_significance": False,
        "computed_at": datetime.now(UTC).isoformat(),
    }


def _task_category(run: RunRecord) -> str:
    return str(_snapshot_field(run, "category") or "uncategorised")


def _snapshot_field(run: RunRecord, key: str) -> Any:
    return (run.task_snapshot or {}).get(key)


def _category_for_key(runs: list[RunRecord], task_key: str) -> str:
    for run in runs:
        if run.task_key == task_key:
            return _task_category(run)
    return "uncategorised"


# --------------------------------------------------------------------------- #
def task_history(session: Session, task_id: str, task_version: str) -> dict[str, Any]:
    runs_all = list(
        session.execute(
            select(RunRecord)
            .options(defer(RunRecord.patch))
            .where(RunRecord.task_id == task_id, RunRecord.task_version == task_version)
            .order_by(RunRecord.created_at.desc())
        ).scalars()
    )
    experiment_ids = sorted({run.experiment_id for run in runs_all})
    experiments = {
        e.id: e
        for e in session.execute(
            select(ExperimentRecord).where(ExperimentRecord.id.in_(experiment_ids))
        )
        .unique()
        .scalars()
    }
    finished = [run for run in runs_all if _terminal(run)]
    categories = _effective_categories(session, [run.id for run in finished])

    by_agent: dict[tuple[str, str], dict[str, Any]] = {}
    for run in finished:
        experiment = experiments[run.experiment_id]
        provenance = _provenance_of(experiment)
        entry = by_agent.setdefault(
            (experiment.agent_config_id, provenance),
            {
                "agent_config_id": experiment.agent_config_id,
                "label": _agent_label(experiment),
                "model": (experiment.agent_snapshot or {}).get("model"),
                "provenance": provenance_module.describe(provenance),
                "tally": Tally(),
            },
        )
        entry["tally"].add(run)

    agents_out = []
    for entry in by_agent.values():
        tally = entry.pop("tally")
        agents_out.append({**entry, **tally.to_dict()})
    agents_out.sort(key=_sort_key)

    patched = [run for run in finished if (run.patch_stats or {}).get("files_changed")]
    paths: Counter[str] = Counter()
    for run in patched:
        for item in (run.patch_stats or {}).get("paths") or []:
            path = item.get("path") if isinstance(item, dict) else item
            if isinstance(path, str):
                paths[path] += 1

    def _avg(key: str) -> float | None:
        values = [float((run.patch_stats or {}).get(key) or 0) for run in patched]
        return round(mean(values), 2) if values else None

    return {
        "task_key": f"{task_id}@{task_version}",
        "total_runs": len(runs_all),
        "finished_runs": len(finished),
        "by_agent": agents_out,
        "failure_categories": _failure_distribution(finished, categories),
        "patches": {
            "runs_with_patch": len(patched),
            "runs_without_patch": len(finished) - len(patched),
            "mean_files_changed": _avg("files_changed"),
            "mean_insertions": _avg("insertions"),
            "mean_deletions": _avg("deletions"),
            "paths_touched": [{"path": p, "runs": n} for p, n in paths.most_common(20)],
        },
        "run_ids": [run.id for run in runs_all[:100]],
        "run_context": {
            run.id: {
                "provenance": _provenance_of(experiments[run.experiment_id]),
                "failure_category": categories.get(run.id),
            }
            for run in runs_all[:100]
        },
        "claims_significance": False,
    }


# --------------------------------------------------------------------------- #
def compare_many(session: Session, experiment_ids: list[str]) -> dict[str, Any]:
    """Side-by-side view of 2..6 experiments over their shared task set."""
    if not 2 <= len(experiment_ids) <= MAX_COMPARE_ARMS:
        raise ValueError(f"compare between 2 and {MAX_COMPARE_ARMS} experiments")
    if len(set(experiment_ids)) != len(experiment_ids):
        raise ValueError("experiment ids must be distinct")

    metrics = [experiment_metrics(session, eid) for eid in experiment_ids]
    dicts = [m.to_dict() for m in metrics]

    blockers: list[str] = []
    caveats: list[str] = []
    task_sets = [{t.task_key for t in m.tasks} for m in metrics]
    union = set().union(*task_sets)
    shared = set.intersection(*task_sets) if task_sets else set()
    if any(s != task_sets[0] for s in task_sets):
        blockers.append(
            f"the arms do not cover the same task set ({len(shared)} shared of {len(union)}); "
            "rates are computed over different tasks"
        )
    for label, values in (
        ("grading versions", {m.grading_version for m in metrics}),
        ("benchmark versions", {m.benchmark for m in metrics}),
        ("result provenance", {m.provenance for m in metrics}),
    ):
        if len(values) > 1:
            blockers.append(f"{label} differ ({', '.join(sorted(values))})")
    if any(not d["completeness"]["is_complete"] for d in dicts):
        caveats.append("at least one arm is still incomplete")
    if any(m.repetitions == 1 for m in metrics):
        caveats.append("one repetition per task means a single flaky run can flip a task's verdict")
    small = [
        d["name"]
        for d in dicts
        if d["success_rate_attributable"]["denominator"] < SIGNIFICANCE_MIN_RUNS
    ]
    if small:
        caveats.append(
            f"{len(small)} arm(s) have fewer than {SIGNIFICANCE_MIN_RUNS} attributable runs; "
            "differences are indicative only"
        )
    if any(m.infra_error for m in metrics):
        caveats.append("infrastructure failures occurred and are excluded from the rates")

    arms = []
    for m, d in zip(metrics, dicts, strict=True):
        attributable = d["success_rate_attributable"]["denominator"]
        arms.append(
            {
                "experiment_id": m.experiment_id,
                "name": m.name,
                "agent": m.agent_label,
                "model": m.agent_model,
                "benchmark": m.benchmark,
                "grading_version": m.grading_version,
                "repetitions": m.repetitions,
                "provenance": provenance_module.describe(m.provenance),
                "completeness": d["completeness"],
                "outcomes": d["outcomes"],
                "success_rate_attributable": d["success_rate_attributable"],
                "timeout_rate": d["reliability"]["timeout_rate"],
                "error_rate": make_rate(m.agent_error, attributable).to_dict(),
                "runtime": d["runtime"],
                "usage": d["usage"],
                "failure_categories": d["failure_categories"],
            }
        )

    by_task = [{t.task_key: t for t in m.tasks} for m in metrics]
    matrix = []
    for key in sorted(union):
        cells: list[dict[str, Any] | None] = []
        title = category = ""
        for tasks in by_task:
            task = tasks.get(key)
            if task is None:
                cells.append(None)
                continue
            title, category = task.title or title, task.category or category
            attributable = task.attributable
            cells.append(
                {
                    "attempts": task.attempts,
                    "attributable": attributable,
                    "solved": task.solved,
                    "outcomes": {
                        o.value: getattr(task, o.value, 0)
                        for o in RunOutcome
                        if getattr(task, o.value, 0)
                    },
                    "rate": round(task.solved / attributable, 4) if attributable else None,
                    "mean_duration_s": task.mean_duration_s,
                }
            )
        matrix.append(
            {
                "task_key": key,
                "title": title,
                "category": category,
                "shared": key in shared,
                "cells": cells,
            }
        )

    statistical_note: dict[str, Any] = {
        "claims_significance": False,
        "note": (
            "No significance test is run across more than two arms (it would need a "
            "multiple-comparison correction). Compare two arms for a two-proportion test."
        ),
    }
    if len(experiment_ids) == 2:
        pair = compare_experiments(session, experiment_ids[0], experiment_ids[1]).to_dict()
        statistical_note = pair["statistical_note"]

    return {
        "comparable": not blockers,
        "blockers": blockers,
        "caveats": caveats,
        "arms": arms,
        "tasks": matrix,
        "shared_task_count": len(shared),
        "statistical_note": statistical_note,
        "computed_at": datetime.now(UTC).isoformat(),
    }


# --------------------------------------------------------------------------- #
def analytics(session: Session) -> dict[str, Any]:
    """Platform-wide activity and behaviour, split by provenance class."""
    experiments = {e.id: e for e in session.execute(select(ExperimentRecord)).unique().scalars()}
    runs = _finished_runs(session, list(experiments))
    provenance_by_run = {run.id: _provenance_of(experiments[run.experiment_id]) for run in runs}
    model_runs = [run for run in runs if _is_model_result(provenance_by_run[run.id])]

    daily: dict[str, dict[str, Any]] = {}
    for run in runs:
        stamp = run.finished_at or run.created_at
        day = stamp.date().isoformat() if stamp else "unknown"
        entry = daily.setdefault(
            day, {"date": day, "model_runs": 0, "non_model_runs": 0, "outcomes": Counter()}
        )
        entry[
            "model_runs" if _is_model_result(provenance_by_run[run.id]) else "non_model_runs"
        ] += 1
        entry["outcomes"][_outcome(run).value] += 1

    by_provenance: dict[str, Tally] = {}
    for run in runs:
        by_provenance.setdefault(provenance_by_run[run.id], Tally()).add(run)

    by_category: dict[str, Tally] = {}
    by_difficulty: dict[str, Tally] = {}
    durations_by_outcome: dict[str, list[float]] = {}
    for run in model_runs:
        by_category.setdefault(_task_category(run), Tally()).add(run)
        by_difficulty.setdefault(str(_snapshot_field(run, "difficulty") or "unrated"), Tally()).add(
            run
        )
        seconds = _duration(run)
        if seconds is not None:
            durations_by_outcome.setdefault(_outcome(run).value, []).append(seconds)

    tokens: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        experiment = experiments[run.experiment_id]
        key = (_agent_label(experiment), provenance_by_run[run.id])
        entry = tokens.setdefault(
            key,
            {
                "agent": key[0],
                "provenance": key[1],
                "runs_reported": 0,
                "runs_unreported": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        )
        usage = run.usage or {}
        if usage.get("reported_by_provider"):
            entry["runs_reported"] += 1
            entry["input_tokens"] += int(usage.get("input_tokens") or 0)
            entry["output_tokens"] += int(usage.get("output_tokens") or 0)
        else:
            entry["runs_unreported"] += 1
    for entry in tokens.values():
        if entry["runs_reported"] == 0:
            # Nothing reported means unknown, not zero.
            entry["input_tokens"] = entry["output_tokens"] = None

    tools = _tool_usage(session, [run.id for run in model_runs])

    return {
        "daily": [{**v, "outcomes": dict(v["outcomes"])} for _, v in sorted(daily.items())],
        "by_provenance": [
            {"provenance": provenance_module.describe(p), **t.to_dict()}
            for p, t in sorted(by_provenance.items())
        ],
        "by_category": [{"category": k, **t.to_dict()} for k, t in sorted(by_category.items())],
        "by_difficulty": [
            {"difficulty": k, **t.to_dict()} for k, t in sorted(by_difficulty.items())
        ],
        "duration_by_outcome": [
            {"outcome": k, **runtime_stats(v)} for k, v in sorted(durations_by_outcome.items())
        ],
        "tokens_by_agent": sorted(tokens.values(), key=lambda e: (e["provenance"], e["agent"])),
        "tools": tools,
        "scope_note": (
            "Category, difficulty, duration and tool statistics cover model results only "
            "(live_model and external_agent provenance); simulated runs are excluded."
        ),
        "model_run_count": len(model_runs),
        "finished_run_count": len(runs),
        "claims_significance": False,
        "computed_at": datetime.now(UTC).isoformat(),
    }


def _tool_usage(session: Session, run_ids: list[str]) -> dict[str, Any]:
    if not run_ids:
        empty = {"n": 0, "mean": None, "median": None, "max": None}
        return {"by_tool": [], "runs_with_tool_events": 0, "calls_per_run": empty}
    rows = session.execute(
        select(RunEventRecord.run_id, RunEventRecord.event_type, RunEventRecord.payload).where(
            RunEventRecord.run_id.in_(run_ids),
            RunEventRecord.event_type.in_(["tool.call", "tool.result"]),
        )
    ).all()
    calls: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    per_run: Counter[str] = Counter()
    for run_id, event_type, payload in rows:
        tool = str((payload or {}).get("tool") or "unknown")
        if event_type == "tool.call":
            calls[tool] += 1
            per_run[run_id] += 1
        elif (payload or {}).get("ok") is False:
            failures[tool] += 1
    stats = runtime_stats([float(n) for n in per_run.values()])
    return {
        "by_tool": [
            {
                "tool": tool,
                "calls": count,
                "failed_results": failures[tool],
                "failure_rate": make_rate(failures[tool], count).to_dict(),
            }
            for tool, count in calls.most_common()
        ],
        "runs_with_tool_events": len(per_run),
        "calls_per_run": {
            "n": stats["n"],
            "mean": stats["mean_s"],
            "median": stats["median_s"],
            "max": stats["max_s"],
        },
    }


# --------------------------------------------------------------------------- #
def overview_extras(session: Session) -> dict[str, Any]:
    """Additions to the platform overview, split so simulated work never reads as a score."""
    experiments = {e.id: e for e in session.execute(select(ExperimentRecord)).unique().scalars()}
    runs = _finished_runs(session, list(experiments))
    by_provenance: dict[str, Tally] = {}
    model_tally = Tally()
    model_runs: list[RunRecord] = []
    for run in runs:
        provenance = _provenance_of(experiments[run.experiment_id])
        by_provenance.setdefault(provenance, Tally()).add(run)
        if _is_model_result(provenance):
            model_tally.add(run)
            model_runs.append(run)
    board = leaderboard(session)
    leader = next((row for row in board["rows"] if row["success_rate"]["denominator"] > 0), None)
    return {
        "by_provenance": [
            {"provenance": provenance_module.describe(p), **t.to_dict()}
            for p, t in sorted(by_provenance.items())
        ],
        "model_results": model_tally.to_dict(),
        "model_failure_categories": _failure_distribution(
            model_runs, _effective_categories(session, [run.id for run in model_runs])
        ),
        "best_observed": (
            {
                "agent_config_id": leader["agent_config_id"],
                "label": leader["label"],
                "model": leader["model"],
                "success_rate": leader["success_rate"],
                "distinguishable_from_runner_up": (
                    None
                    if len(board["rows"]) < 2
                    else board["rows"][1].get("ci_overlaps_leader") is False
                ),
            }
            if leader
            else None
        ),
        "best_observed_note": (
            "Highest observed success rate among model results. "
            "Not a significance claim; see the leaderboard for intervals."
        ),
    }
