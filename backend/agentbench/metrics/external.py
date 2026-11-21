"""Published third-party benchmark results, shown beside, never inside, Agent Labs results.

These are numbers someone else measured (for example a public SWE-bench
leaderboard). Agent Labs did not run them, so:

* every dataset carries its publisher, URL, publication and retrieval dates
  and harness, and every response says ``is_agent_labs_result: false``;
* scores are stored verbatim; the only thing derived is a 95% Wilson interval
  from the score and the task count, labelled as derived;
* rows are only compared within one harness, because a different agent
  scaffold measures a different system.

Datasets live as JSON files in ``<benchmarks_dir>/external/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from agentbench.metrics.compute import make_rate

DERIVED_CI_NOTE = (
    "95% Wilson interval derived by Agent Labs from the published score and task count, "
    "assuming one attempt per task. The publisher does not report intervals."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExternalSource(_Strict):
    publisher: str = Field(min_length=1)
    url: HttpUrl
    benchmark: str = Field(min_length=1)
    published: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    retrieved: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    tasks: int = Field(gt=0)
    attempts_per_task: int = Field(default=1, ge=1)
    default_harness: str = Field(min_length=1)
    status: str = ""
    notes: list[str] = Field(default_factory=list)


class ExternalRow(_Strict):
    rank: int = Field(ge=1)
    model: str = Field(min_length=1)
    lab: str = Field(min_length=1)
    harness: str = Field(min_length=1)
    score_percent: float = Field(ge=0, le=100)
    cost_per_task_usd: float | None = Field(default=None, ge=0)
    tokens_per_task: str | None = None
    duration_per_task: str | None = None


class ExternalDataset(_Strict):
    schema_version: str = "1.0"
    source: ExternalSource
    rows: list[ExternalRow] = Field(min_length=1)


class ExternalDatasetError(ValueError):
    """A dataset file is missing provenance or is malformed; it is never shown."""


def load_dataset(path: Path) -> ExternalDataset:
    try:
        return ExternalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ExternalDatasetError(f"{path.name}: {exc}") from exc


def dataset_payload(dataset: ExternalDataset, dataset_id: str) -> dict[str, Any]:
    tasks = dataset.source.tasks * dataset.source.attempts_per_task
    rows: list[dict[str, Any]] = []
    for row in dataset.rows:
        solved = round(row.score_percent * tasks / 100)
        rate = make_rate(solved, tasks).to_dict()
        rows.append(
            {
                **row.model_dump(),
                "on_default_harness": row.harness == dataset.source.default_harness,
                "solved_estimate": solved,
                "interval": {
                    "ci_low": rate["ci_low"],
                    "ci_high": rate["ci_high"],
                    "method": rate["ci_method"],
                    "derived": True,
                },
            }
        )

    # Ties are only meaningful within one harness: flag rows whose interval
    # overlaps the best row on the same harness.
    leaders: dict[str, dict[str, Any]] = {}
    for item in sorted(rows, key=lambda r: -r["score_percent"]):
        leaders.setdefault(item["harness"], item)
    for item in rows:
        leader = leaders[item["harness"]]
        item["harness_leader"] = item is leader
        item["tied_with_harness_leader"] = (
            None
            if item is leader
            else bool(item["interval"]["ci_high"] >= leader["interval"]["ci_low"])
        )

    source = dataset.source.model_dump(mode="json")
    return {
        "id": dataset_id,
        "source": source,
        "is_agent_labs_result": False,
        "comparable_with_agent_labs_runs": False,
        "interval_note": DERIVED_CI_NOTE,
        "harnesses": sorted({row["harness"] for row in rows}),
        "rows": sorted(rows, key=lambda r: r["rank"]),
    }


def published_results(directory: Path) -> dict[str, Any]:
    """Every valid dataset under ``directory``; invalid files are reported, not shown."""
    datasets: list[dict[str, Any]] = []
    errors: list[str] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                datasets.append(dataset_payload(load_dataset(path), path.stem))
            except ExternalDatasetError as exc:
                errors.append(str(exc)[:500])
    return {
        "datasets": datasets,
        "errors": errors,
        "notice": (
            "Published by third parties and not run in Agent Labs. These scores come from a "
            "different benchmark and harness than your own experiments and cannot be ranked "
            "against them."
        ),
    }
