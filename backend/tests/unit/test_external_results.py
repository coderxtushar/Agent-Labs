"""Published third-party results: provenance is mandatory, intervals are derived honestly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentbench.metrics.compute import wilson_interval
from agentbench.metrics.external import (
    ExternalDatasetError,
    dataset_payload,
    load_dataset,
    published_results,
)

REPO_EXTERNAL = Path(__file__).resolve().parents[3] / "benchmarks" / "external"


def _dataset(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "source": {
            "publisher": "Test publisher",
            "url": "https://example.org/leaderboard",
            "benchmark": "Test bench",
            "published": "2026-01-01",
            "retrieved": "2026-01-02",
            "tasks": 100,
            "default_harness": "harness-a",
        },
        "rows": [
            {
                "rank": 1,
                "model": "Alpha",
                "lab": "Lab A",
                "harness": "harness-a",
                "score_percent": 90.0,
            },
            {
                "rank": 2,
                "model": "Beta",
                "lab": "Lab B",
                "harness": "harness-a",
                "score_percent": 88.0,
            },
            {
                "rank": 3,
                "model": "Gamma",
                "lab": "Lab C",
                "harness": "harness-a",
                "score_percent": 50.0,
            },
            {
                "rank": 4,
                "model": "Alpha",
                "lab": "Lab A",
                "harness": "harness-b",
                "score_percent": 60.0,
            },
        ],
    }
    data.update(overrides)
    return data


def _write(tmp_path: Path, name: str, data: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_dataset_without_a_source_url_is_rejected(tmp_path: Path) -> None:
    data = _dataset()
    del data["source"]["url"]
    with pytest.raises(ExternalDatasetError):
        load_dataset(_write(tmp_path, "bad.json", data))


def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    data = _dataset()
    data["rows"][0]["verified_by_agent_labs"] = True
    with pytest.raises(ExternalDatasetError):
        load_dataset(_write(tmp_path, "bad.json", data))


def test_intervals_are_wilson_from_score_and_task_count(tmp_path: Path) -> None:
    payload = dataset_payload(load_dataset(_write(tmp_path, "d.json", _dataset())), "d")
    alpha = payload["rows"][0]
    low, high = wilson_interval(90, 100)
    assert alpha["solved_estimate"] == 90
    assert alpha["interval"]["ci_low"] == round(low, 4)
    assert alpha["interval"]["ci_high"] == round(high, 4)
    assert alpha["interval"]["derived"] is True
    assert payload["is_agent_labs_result"] is False
    assert payload["comparable_with_agent_labs_runs"] is False


def test_ties_are_flagged_only_within_the_same_harness(tmp_path: Path) -> None:
    rows = {
        (r["model"], r["harness"]): r
        for r in dataset_payload(load_dataset(_write(tmp_path, "d.json", _dataset())), "d")["rows"]
    }
    assert rows[("Alpha", "harness-a")]["harness_leader"] is True
    assert rows[("Alpha", "harness-a")]["tied_with_harness_leader"] is None
    assert rows[("Beta", "harness-a")]["tied_with_harness_leader"] is True  # 88 vs 90 of 100
    assert rows[("Gamma", "harness-a")]["tied_with_harness_leader"] is False
    # The only row on harness-b leads its own harness; it is never compared with harness-a.
    assert rows[("Alpha", "harness-b")]["harness_leader"] is True
    assert rows[("Alpha", "harness-b")]["on_default_harness"] is False


def test_invalid_files_are_reported_and_valid_ones_still_load(tmp_path: Path) -> None:
    _write(tmp_path, "good.json", _dataset())
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    body = published_results(tmp_path)
    assert [d["id"] for d in body["datasets"]] == ["good"]
    assert len(body["errors"]) == 1 and "broken.json" in body["errors"][0]
    assert "not run in Agent Labs" in body["notice"]


def test_missing_directory_is_empty_not_an_error(tmp_path: Path) -> None:
    assert published_results(tmp_path / "absent") == {
        **published_results(tmp_path / "absent"),
        "datasets": [],
        "errors": [],
    }


def test_the_bundled_dataset_is_valid_and_sourced() -> None:
    body = published_results(REPO_EXTERNAL)
    assert body["errors"] == []
    (dataset,) = body["datasets"]
    assert dataset["source"]["url"].startswith("https://www.vals.ai/")
    assert dataset["source"]["tasks"] == 500
    # Every published score is a whole number of the 500 tasks, so the count is exact.
    for row in dataset["rows"]:
        assert row["solved_estimate"] / 5 == pytest.approx(row["score_percent"])


def test_endpoint_serves_the_fixture_directory(api_client: Any) -> None:
    body = api_client.get("/api/v1/published-results").json()
    # Tests point benchmarks_dir at tests/fixtures, which has no external/ folder.
    assert body["datasets"] == [] and body["errors"] == []
