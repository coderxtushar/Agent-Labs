"""Report export.

Three formats, one source of truth (:mod:`agentbench.metrics.compute`):

* ``json`` - the full metrics document, suitable for diffing between runs.
* ``csv`` - one row per run, for spreadsheet and notebook analysis.
* ``markdown`` - a reproducible experiment report that states the exact
  configuration, the denominators behind every rate, and the caveats.

Every format carries the experiment's ``config_snapshot``, so a report is
enough to re-create the experiment that produced it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import (
    ExperimentRecord,
    FailureAnnotationRecord,
    GradingResultRecord,
    RunRecord,
)
from agentbench.metrics.compute import experiment_metrics
from agentbench.schemas import provenance

RUN_CSV_COLUMNS = [
    "run_id",
    "experiment_id",
    "experiment_name",
    "task_key",
    "task_category",
    "task_difficulty",
    "attempt_index",
    "state",
    "outcome",
    "counts_toward_success_rate",
    "graded_passed",
    "grading_score",
    "tests_passed",
    "tests_failed",
    "failure_category",
    "failure_source",
    "files_changed",
    "insertions",
    "deletions",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "cost_source",
    "infra_attempts",
    "total_s",
    "agent_s",
    "grading_s",
    "sandbox_provider",
    "provenance",
    "termination_reason",
]


def experiment_json(session: Session, experiment_id: str) -> str:
    experiment = session.get(ExperimentRecord, experiment_id)
    if experiment is None:
        raise LookupError(f"unknown experiment: {experiment_id}")
    metrics = experiment_metrics(session, experiment_id)
    return json.dumps(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "report_version": "1.0",
            "config_snapshot": experiment.config_snapshot,
            "metrics": metrics.to_dict(),
            "runs": [row for row in _run_rows(session, experiment_id)],
        },
        indent=2,
        default=str,
    )


def experiment_csv(session: Session, experiment_id: str) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=RUN_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in _run_rows(session, experiment_id):
        writer.writerow(row)
    return buffer.getvalue()


def _run_rows(session: Session, experiment_id: str) -> list[dict[str, Any]]:
    experiment = session.get(ExperimentRecord, experiment_id)
    if experiment is None:
        raise LookupError(f"unknown experiment: {experiment_id}")

    runs = list(
        session.execute(
            select(RunRecord)
            .where(RunRecord.experiment_id == experiment_id)
            .order_by(RunRecord.task_id.asc(), RunRecord.attempt_index.asc())
        ).scalars()
    )
    if not runs:
        return []

    run_ids = [run.id for run in runs]
    grading = {
        row.run_id: row
        for row in session.execute(
            select(GradingResultRecord).where(GradingResultRecord.run_id.in_(run_ids))
        ).scalars()
    }
    annotations = {
        row.run_id: row
        for row in session.execute(
            select(FailureAnnotationRecord).where(
                FailureAnnotationRecord.run_id.in_(run_ids),
                FailureAnnotationRecord.superseded.is_(False),
            )
        ).scalars()
    }

    rows: list[dict[str, Any]] = []
    for run in runs:
        snapshot = run.task_snapshot or {}
        stats = run.patch_stats or {}
        usage = run.usage or {}
        timings = run.timings or {}
        grade = grading.get(run.id)
        annotation = annotations.get(run.id)
        from agentbench.schemas.enums import RunOutcome

        counts = RunOutcome(run.outcome).counts_toward_success_rate if run.outcome else None
        rows.append(
            {
                "run_id": run.id,
                "experiment_id": experiment.id,
                "experiment_name": experiment.name,
                "task_key": run.task_key,
                "task_category": snapshot.get("category"),
                "task_difficulty": snapshot.get("difficulty"),
                "attempt_index": run.attempt_index,
                "state": run.state,
                "outcome": run.outcome,
                "counts_toward_success_rate": counts,
                "graded_passed": None if grade is None else grade.passed,
                "grading_score": None if grade is None else grade.score,
                "tests_passed": None if grade is None else grade.tests_passed,
                "tests_failed": None if grade is None else grade.tests_failed,
                "failure_category": None if annotation is None else annotation.category,
                "failure_source": None if annotation is None else annotation.source,
                "files_changed": stats.get("files_changed"),
                "insertions": stats.get("insertions"),
                "deletions": stats.get("deletions"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cost_usd": run.cost_usd,
                "cost_source": run.cost_source,
                "infra_attempts": run.infra_attempts,
                "total_s": timings.get("total_s"),
                "agent_s": timings.get("agent_s"),
                "grading_s": timings.get("grading_s"),
                "sandbox_provider": run.sandbox_provider,
                "provenance": str(provenance.from_agent_snapshot(run.agent_snapshot or {})),
                "termination_reason": (run.termination_reason or "")[:300],
            }
        )
    return rows


# --------------------------------------------------------------------------- #
def experiment_markdown(session: Session, experiment_id: str) -> str:
    experiment = session.get(ExperimentRecord, experiment_id)
    if experiment is None:
        raise LookupError(f"unknown experiment: {experiment_id}")
    metrics = experiment_metrics(session, experiment_id)
    data = metrics.to_dict()
    snapshot = experiment.config_snapshot or {}

    lines: list[str] = [
        f"# Experiment report: {metrics.name}",
        "",
        f"Generated {datetime.now(UTC).isoformat()} by AgentBench Lab.",
        "",
    ]

    kind = data["provenance"]
    if not kind["is_model_result"]:
        lines += [f"> **{kind['label']}.** {kind['caveat']}", ""]
    else:
        lines += [f"> **{kind['label']}.** {kind['caveat']}", ""]

    lines += [
        "## Configuration",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Experiment ID | `{metrics.experiment_id}` |",
        f"| Result provenance | **{kind['label']}** (`{kind['value']}`) |",
        f"| State | {metrics.state} |",
        f"| Benchmark | {metrics.benchmark} |",
        f"| Agent | {metrics.agent_label} (adapter `{metrics.agent_adapter}`) |",
        f"| Model | {metrics.agent_model or 'n/a'} |",
        f"| Prompt / tools version | {snapshot.get('agent', {}).get('prompt_version', '?')} / "
        f"{snapshot.get('agent', {}).get('tools_version', '?')} |",
        f"| Grading version | {metrics.grading_version} |",
        f"| Repetitions per task | {metrics.repetitions} |",
        f"| Seed | {snapshot.get('execution', {}).get('seed', 'none')} |",
        f"| Sandbox provider | {snapshot.get('execution', {}).get('sandbox_provider', '?')} |",
        f"| Hints given to agent | {snapshot.get('execution', {}).get('include_hints', False)} |",
        "",
        "## Completeness",
        "",
        f"- Runs: {data['completeness']['finished_runs']} finished of "
        f"{data['completeness']['total_runs']} "
        f"({data['completeness']['pending_runs']} pending, "
        f"{data['completeness']['active_runs']} in flight)",
        f"- Complete: **{data['completeness']['is_complete']}**",
        "",
        "## Headline results",
        "",
        "| Metric | Value | Numerator / denominator | 95% CI |",
        "| --- | --- | --- | --- |",
    ]

    for label, key in (
        ("Success rate (agent-attributable)", "success_rate_attributable"),
        ("Success rate (graded only)", "success_rate_graded"),
        ("pass@1", "pass_at_1"),
    ):
        rate = data[key]
        value = "n/a" if rate["percent"] is None else f"{rate['percent']}%"
        ci = (
            "n/a"
            if rate["ci_low"] is None
            else f"{round(100 * rate['ci_low'], 1)}% – {round(100 * rate['ci_high'], 1)}%"
        )
        lines.append(f"| {label} | {value} | {rate['numerator']} / {rate['denominator']} | {ci} |")

    lines += [
        "",
        f"Confidence intervals use the {data['success_rate_attributable']['ci_method'] or 'n/a'}. "
        "No significance claim is made.",
        "",
        "### Outcome breakdown",
        "",
        "| Outcome | Count | Counts toward success rate |",
        "| --- | --- | --- |",
    ]
    counting = {"solved", "unsolved", "timeout", "agent_error"}
    for outcome, count in data["outcomes"].items():
        lines.append(f"| {outcome} | {count} | {'yes' if outcome in counting else 'no'} |")

    lines += [
        "",
        "### Reliability",
        "",
        f"- Regressions (pass_to_pass broken): {data['reliability']['regression_count']}",
        f"- Timeout rate: {data['reliability']['timeout_rate']['percent']}% "
        f"({data['reliability']['timeout_rate']['numerator']}/"
        f"{data['reliability']['timeout_rate']['denominator']})",
        f"- Infrastructure failure rate: {data['reliability']['infra_failure_rate']['percent']}% "
        f"({data['reliability']['infra_failure_rate']['numerator']}/"
        f"{data['reliability']['infra_failure_rate']['denominator']})",
        f"- Infrastructure retries: {data['reliability']['infra_retry_total']}",
        f"- Runs excluded from success rates: {data['reliability']['excluded_from_rates']}",
        "",
        "### Runtime and cost",
        "",
        f"- Total wall clock: {data['runtime']['total_s']}s "
        f"(mean {data['runtime']['mean_s']}s, median {data['runtime']['median_s']}s, "
        f"p90 {data['runtime']['p90_s']}s)",
        f"- Tokens: in {data['usage']['input_tokens']}, out {data['usage']['output_tokens']}",
        f"- Cost: {data['usage']['cost_usd_total']} USD "
        f"(sources: {data['usage']['cost_sources'] or 'none reported'})",
        f"- Runs with no reported usage: {data['usage']['runs_without_reported_usage']}",
        "",
    ]

    if data["pass_at_k"]["values"]:
        lines += [
            "### pass@k",
            "",
            f"Estimator: {data['pass_at_k']['estimator']}",
            "",
            "| k | value |",
            "| --- | --- |",
            *[f"| {k} | {v} |" for k, v in data["pass_at_k"]["values"].items()],
            "",
        ]
    else:
        lines += ["### pass@k", "", data["pass_at_k"]["note"], ""]

    lines += [
        "## Per-task results",
        "",
        "| Task | Category | Attempts | Solved | Unsolved | Timeout | Other | Regressions | Mean s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in data["tasks"]:
        other = (
            task["agent_error"]
            + task["infra_error"]
            + task["invalid_task"]
            + task["grading_error"]
            + task["cancelled"]
        )
        lines.append(
            f"| `{task['task_key']}` | {task['category']} | {task['attempts']} | "
            f"{task['solved']} | {task['unsolved']} | {task['timeout']} | {other} | "
            f"{task['regressions']} | {task['mean_duration_s']} |"
        )

    if data["by_category"]:
        lines += [
            "",
            "## By category",
            "",
            "| Category | Tasks | Attempts | Solved | Success rate |",
            "| --- | --- | --- | --- | --- |",
        ]
        for category, stats in data["by_category"].items():
            rate = stats["success_rate_attributable"]
            value = "n/a" if rate["percent"] is None else f"{rate['percent']}%"
            lines.append(
                f"| {category} | {stats['tasks']} | {stats['attempts']} | {stats['solved']} | "
                f"{value} ({rate['numerator']}/{rate['denominator']}) |"
            )

    if data["failure_categories"]:
        lines += [
            "",
            "## Failure categories",
            "",
            "| Category | Count |",
            "| --- | --- |",
            *[
                f"| {name} | {count} |"
                for name, count in sorted(data["failure_categories"].items())
            ],
        ]

    if data["warnings"]:
        lines += ["", "## Caveats", ""]
        lines += [f"- {warning}" for warning in data["warnings"]]

    lines += [
        "",
        "## Reproduction",
        "",
        "```json",
        json.dumps(snapshot, indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(lines)


def render_report(session: Session, experiment_id: str, fmt: str = "markdown") -> str:
    if fmt == "json":
        return experiment_json(session, experiment_id)
    if fmt == "csv":
        return experiment_csv(session, experiment_id)
    if fmt in {"markdown", "md"}:
        return experiment_markdown(session, experiment_id)
    raise ValueError(f"unsupported report format: {fmt}")


def comparison_markdown(session: Session, baseline_id: str, candidate_id: str) -> str:
    from agentbench.metrics.compare import compare_experiments

    comparison = compare_experiments(session, baseline_id, candidate_id).to_dict()
    lines = [
        "# Comparison report",
        "",
        f"Generated {datetime.now(UTC).isoformat()}.",
        "",
        f"**{comparison['summary']}**",
        "",
        "| | Baseline | Candidate |",
        "| --- | --- | --- |",
        f"| Experiment | {comparison['baseline']['name']} | {comparison['candidate']['name']} |",
        f"| Agent | {comparison['baseline']['agent']} | {comparison['candidate']['agent']} |",
        f"| Model | {comparison['baseline']['model'] or 'n/a'} | "
        f"{comparison['candidate']['model'] or 'n/a'} |",
        f"| Success rate | {comparison['baseline']['success_rate_attributable']['percent']}% | "
        f"{comparison['candidate']['success_rate_attributable']['percent']}% |",
        f"| Solved | {comparison['baseline']['outcomes']['solved']} | "
        f"{comparison['candidate']['outcomes']['solved']} |",
        f"| Runtime (s) | {comparison['baseline']['runtime']['total_s']} | "
        f"{comparison['candidate']['runtime']['total_s']} |",
        f"| Cost (USD) | {comparison['baseline']['usage']['cost_usd_total']} | "
        f"{comparison['candidate']['usage']['cost_usd_total']} |",
        "",
        "## Movement",
        "",
    ]
    for bucket, keys in comparison["movement"].items():
        lines.append(f"- **{bucket}** ({len(keys)}): {', '.join(keys) if keys else 'none'}")

    lines += [
        "",
        "## Per-task",
        "",
        "| Task | Baseline | Candidate | Movement |",
        "| --- | --- | --- | --- |",
    ]
    for delta in comparison["task_deltas"]:
        lines.append(
            f"| `{delta['task_key']}` | {delta['baseline']['solved']}/"
            f"{delta['baseline']['attempts']} | {delta['candidate']['solved']}/"
            f"{delta['candidate']['attempts']} | {delta['movement']} |"
        )

    note = comparison["statistical_note"]
    lines += [
        "",
        "## Statistical note",
        "",
        note.get("reason") or note.get("note", ""),
        "",
    ]
    if comparison["blockers"]:
        lines += ["## Comparability blockers", "", *[f"- {b}" for b in comparison["blockers"]], ""]
    if comparison["caveats"]:
        lines += ["## Caveats", "", *[f"- {c}" for c in comparison["caveats"]], ""]
    return "\n".join(lines)
