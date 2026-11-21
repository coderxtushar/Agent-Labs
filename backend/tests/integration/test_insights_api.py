"""Leaderboard, profiles, task history, multi-arm comparison, analytics, monitor.

Runs are written directly (not executed) so these tests stay fast and pin the
aggregation rules: infrastructure failures never enter a denominator, results
are split by provenance, unclassified failures stay ``unknown``, and nothing
claims significance.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import ExperimentRecord, RunEventRecord, RunRecord
from agentbench.metrics.failure import Classification, store_classification
from agentbench.monitor import host, ollama
from agentbench.orchestrator import experiment as experiments
from agentbench.registry.agents import register_agent
from agentbench.registry.loader import LoadedBenchmark, parse_task_dict
from agentbench.registry.service import get_task_by_key, import_benchmark
from agentbench.schemas.enums import FailureCategory, RunOutcome, RunState
from tests.conftest import minirepo_task_dict

API = "/api/v1"


@pytest.fixture
def second_task(db: Session, seeded_task: Any) -> Any:
    spec = parse_task_dict(
        minirepo_task_dict(task_id="minirepo-second", title="a second fixture task")
    )
    import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[spec]))
    record = get_task_by_key(db, spec.key)
    db.commit()
    return record


def _experiment(
    db: Session,
    *,
    agent_name: str,
    task_keys: list[str],
    results: list[tuple[RunOutcome, float]],
    provenance: str = "live_model",
    model: str | None = "fixture-model",
    repetitions: int = 1,
    adapter: str = "mock",
) -> ExperimentRecord:
    """Create an experiment whose runs finish with ``results`` (task, attempt order)."""
    agent = register_agent(db, name=agent_name, adapter=adapter, config={"behaviour": "noop"})
    db.flush()
    record = experiments.create_experiment(
        db,
        name=f"exp-{agent_name}",
        agent_config_id=agent.id,
        task_keys=task_keys,
        repetitions=repetitions,
    )
    # Provenance is read off the stored snapshot; set it the way a live
    # experiment would record it.
    record.agent_snapshot = {
        **(record.agent_snapshot or {}),
        "provenance": provenance,
        "model": model,
        "is_simulated": provenance == "simulated",
    }
    experiments.launch(db, record.id)
    runs = list(
        db.execute(
            select(RunRecord)
            .where(RunRecord.experiment_id == record.id)
            .order_by(RunRecord.task_id, RunRecord.attempt_index)
        ).scalars()
    )
    assert len(runs) == len(results)
    for run, (outcome, seconds) in zip(runs, results, strict=True):
        run.state = RunState.COMPLETED
        run.outcome = outcome
        run.timings = {"total_s": seconds}
    experiments.refresh_state(db, record.id)
    db.commit()
    return record


def _runs(db: Session, experiment_id: str) -> list[RunRecord]:
    return list(
        db.execute(
            select(RunRecord)
            .where(RunRecord.experiment_id == experiment_id)
            .order_by(RunRecord.task_id, RunRecord.attempt_index)
        ).scalars()
    )


# --------------------------------------------------------------------------- #
class TestLeaderboard:
    def test_infra_errors_never_enter_the_denominator(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        _experiment(
            db,
            agent_name="live-a",
            task_keys=[seeded_task.key],
            repetitions=4,
            results=[
                (RunOutcome.SOLVED, 10.0),
                (RunOutcome.TIMEOUT, 30.0),
                (RunOutcome.INFRA_ERROR, 0.1),
                (RunOutcome.AGENT_ERROR, 5.0),
            ],
        )
        body = api_client.get(f"{API}/leaderboard").json()
        (row,) = body["rows"]
        assert row["success_rate"]["numerator"] == 1
        assert row["success_rate"]["denominator"] == 3
        assert row["excluded_runs"] == 1
        assert row["timeout_rate"]["numerator"] == 1
        assert row["error_rate"]["numerator"] == 1
        assert row["tasks_attempted"] == 1 and row["tasks_passed"] == 1
        # Runtime covers attributable runs only; the 0.1 s infra run is excluded.
        assert row["runtime"]["n"] == 3
        assert row["runtime"]["median_s"] == 10.0
        assert body["claims_significance"] is False
        assert any("fewer than 30" in w for w in body["warnings"])

    def test_simulated_results_are_hidden_by_default_and_labelled_when_shown(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        _experiment(
            db,
            agent_name="pipeline-check",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.SOLVED, 1.0)],
            provenance="simulated",
            model=None,
        )
        default = api_client.get(f"{API}/leaderboard").json()
        assert default["rows"] == []
        assert default["hidden_non_model_experiments"] == 1

        shown = api_client.get(f"{API}/leaderboard", params={"include_non_model": True}).json()
        (row,) = shown["rows"]
        assert row["provenance"]["value"] == "simulated"
        assert row["provenance"]["is_model_result"] is False

    def test_overlapping_intervals_are_flagged_not_ranked_as_different(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        _experiment(
            db,
            agent_name="leader",
            task_keys=[seeded_task.key],
            repetitions=2,
            results=[(RunOutcome.SOLVED, 1.0), (RunOutcome.SOLVED, 1.0)],
        )
        _experiment(
            db,
            agent_name="runner-up",
            task_keys=[seeded_task.key],
            repetitions=2,
            results=[(RunOutcome.SOLVED, 1.0), (RunOutcome.UNSOLVED, 1.0)],
        )
        rows = api_client.get(f"{API}/leaderboard").json()["rows"]
        assert [r["label"].split("@")[0] for r in rows] == ["leader", "runner-up"]
        assert rows[0]["ci_overlaps_leader"] is None
        assert rows[1]["ci_overlaps_leader"] is True

    def test_filters_restrict_to_selected_experiments(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        first = _experiment(
            db, agent_name="one", task_keys=[seeded_task.key], results=[(RunOutcome.SOLVED, 1.0)]
        )
        _experiment(
            db, agent_name="two", task_keys=[seeded_task.key], results=[(RunOutcome.UNSOLVED, 1.0)]
        )
        body = api_client.get(f"{API}/leaderboard", params={"experiment_ids": first.id}).json()
        assert [r["label"].split("@")[0] for r in body["rows"]] == ["one"]
        assert len(body["filters"]["experiments"]) == 2
        none = api_client.get(f"{API}/leaderboard", params={"benchmark_id": "nope"}).json()
        assert none["rows"] == [] and "no finished runs match these filters" in none["warnings"]


# --------------------------------------------------------------------------- #
class TestAgentProfile:
    def test_strengths_and_weaknesses_cite_their_evidence(
        self, api_client: Any, db: Session, seeded_task: Any, second_task: Any
    ) -> None:
        record = _experiment(
            db,
            agent_name="profiled",
            task_keys=[seeded_task.key, second_task.key],
            results=[(RunOutcome.SOLVED, 4.0), (RunOutcome.UNSOLVED, 6.0)],
        )
        body = api_client.get(f"{API}/agents/{record.agent_config_id}/profile").json()
        assert body["agent"]["id"] == record.agent_config_id
        assert [s["evidence"] for s in body["strengths"]] == [
            "solved 1 of 1 attributable attempt(s)"
        ]
        assert len(body["weaknesses"]) == 1
        # The unsolved run was never classified: it is reported as unknown, not guessed.
        assert body["failure_categories"] == {"unknown": 1}
        assert "Too few runs" in body["evidence_note"]
        assert len(body["history"]) == 1 and len(body["durations"]) == 2
        assert body["claims_significance"] is False

    def test_unknown_agent_is_404(self, api_client: Any) -> None:
        assert api_client.get(f"{API}/agents/does-not-exist/profile").status_code == 404


# --------------------------------------------------------------------------- #
class TestTaskHistory:
    def test_history_groups_by_agent_and_reports_failure_categories(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        record = _experiment(
            db,
            agent_name="historian",
            task_keys=[seeded_task.key],
            repetitions=2,
            results=[(RunOutcome.SOLVED, 2.0), (RunOutcome.UNSOLVED, 3.0)],
        )
        runs = _runs(db, record.id)
        runs[0].patch_stats = {
            "files_changed": 1,
            "insertions": 3,
            "deletions": 1,
            "paths": [{"path": "calc.py", "change": "modified", "+": 3, "-": 1}],
        }
        store_classification(
            db, runs[1].id, Classification(FailureCategory.NO_PATCH_PRODUCED, rationale="none")
        )
        db.commit()

        body = api_client.get(f"{API}/tasks/{seeded_task.key}/history").json()
        (agent,) = body["by_agent"]
        assert agent["success_rate"]["numerator"] == 1
        assert agent["success_rate"]["denominator"] == 2
        assert body["failure_categories"] == {"no_patch_produced": 1}
        assert body["patches"]["runs_with_patch"] == 1
        assert body["patches"]["paths_touched"] == [{"path": "calc.py", "runs": 1}]
        assert len(body["runs"]) == 2
        assert {r["provenance"] for r in body["runs"]} == {"live_model"}

    def test_unknown_task_is_404(self, api_client: Any) -> None:
        assert api_client.get(f"{API}/tasks/nope@1.0/history").status_code == 404


# --------------------------------------------------------------------------- #
class TestCompareMatrix:
    def test_requires_two_to_six_distinct_known_experiments(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        one = _experiment(
            db, agent_name="solo", task_keys=[seeded_task.key], results=[(RunOutcome.SOLVED, 1.0)]
        )
        url = f"{API}/compare/matrix"
        assert api_client.get(url, params={"experiment_ids": one.id}).status_code == 400
        assert (
            api_client.get(url, params={"experiment_ids": f"{one.id},{one.id}"}).status_code == 400
        )
        assert (
            api_client.get(url, params={"experiment_ids": f"{one.id},missing"}).status_code == 404
        )

    def test_mismatched_task_sets_and_provenance_are_blockers(
        self, api_client: Any, db: Session, seeded_task: Any, second_task: Any
    ) -> None:
        live = _experiment(
            db,
            agent_name="arm-live",
            task_keys=[seeded_task.key, second_task.key],
            results=[(RunOutcome.SOLVED, 1.0), (RunOutcome.UNSOLVED, 1.0)],
        )
        simulated = _experiment(
            db,
            agent_name="arm-sim",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.SOLVED, 1.0)],
            provenance="simulated",
            model=None,
        )
        body = api_client.get(
            f"{API}/compare/matrix", params={"experiment_ids": f"{live.id},{simulated.id}"}
        ).json()
        assert body["comparable"] is False
        assert any("same task set" in b for b in body["blockers"])
        assert any("provenance" in b for b in body["blockers"])
        assert body["shared_task_count"] == 1
        missing = next(row for row in body["tasks"] if not row["shared"])
        assert missing["cells"][1] is None
        assert body["statistical_note"]["claims_significance"] is False

    def test_three_arms_never_run_a_significance_test(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        ids = [
            _experiment(
                db, agent_name=f"arm-{i}", task_keys=[seeded_task.key], results=[(outcome, 1.0)]
            ).id
            for i, outcome in enumerate(
                [RunOutcome.SOLVED, RunOutcome.UNSOLVED, RunOutcome.TIMEOUT]
            )
        ]
        body = api_client.get(
            f"{API}/compare/matrix", params={"experiment_ids": ",".join(ids)}
        ).json()
        assert body["comparable"] is True
        assert len(body["arms"]) == 3
        assert "multiple-comparison" in body["statistical_note"]["note"]
        assert body["arms"][2]["timeout_rate"]["numerator"] == 1


# --------------------------------------------------------------------------- #
class TestAnalyticsAndOverview:
    def test_empty_analytics_keeps_the_same_shape(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/analytics").json()
        assert body["tools"]["calls_per_run"] == {"n": 0, "mean": None, "median": None, "max": None}
        assert body["model_run_count"] == 0 and body["daily"] == []

    def test_tool_usage_comes_from_recorded_events_of_model_runs_only(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        live = _experiment(
            db,
            agent_name="tooluser",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.SOLVED, 1.0)],
        )
        sim = _experiment(
            db,
            agent_name="simtool",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.SOLVED, 1.0)],
            provenance="simulated",
            model=None,
        )
        for experiment_id in (live.id, sim.id):
            run = _runs(db, experiment_id)[0]
            db.add_all(
                [
                    RunEventRecord(
                        run_id=run.id, seq=1, event_type="tool.call", payload={"tool": "read_file"}
                    ),
                    RunEventRecord(
                        run_id=run.id,
                        seq=2,
                        event_type="tool.result",
                        payload={"tool": "read_file", "ok": False},
                    ),
                ]
            )
        db.commit()

        body = api_client.get(f"{API}/analytics").json()
        assert body["model_run_count"] == 1 and body["finished_run_count"] == 2
        (tool,) = body["tools"]["by_tool"]
        assert tool == {
            **tool,
            "tool": "read_file",
            "calls": 1,
            "failed_results": 1,
        }
        # No provider reported tokens: unknown, not zero.
        assert all(entry["input_tokens"] is None for entry in body["tokens_by_agent"])

    def test_overview_splits_provenance_and_has_no_best_model_without_model_results(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        _experiment(
            db,
            agent_name="only-sim",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.UNSOLVED, 1.0)],
            provenance="simulated",
            model=None,
        )
        body = api_client.get(f"{API}/overview").json()
        assert body["best_observed"] is None
        assert body["model_results"]["finished_runs"] == 0
        assert body["model_failure_categories"] == {}
        assert [p["provenance"]["value"] for p in body["by_provenance"]] == ["simulated"]
        # The original overview fields are unchanged.
        assert "overall_success_rate_attributable" in body["runs"]


# --------------------------------------------------------------------------- #
class TestMonitor:
    def test_vm_stat_and_swapusage_parsing(self) -> None:
        pages = host.parse_vm_stat(
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free:                               10.\n"
            "Pages wired down:                         20.\n"
            '"Translation faults":                     99.\n'
        )
        assert pages == {
            "page_size": 16384,
            "pages free": 10,
            "pages wired down": 20,
            "translation faults": 99,
        }
        swap = host.parse_swapusage("vm.swapusage: total = 2048.00M  used = 512.50M  free = 1.5G")
        assert swap["total"] == 2048 * 1024**2
        assert swap["used"] == int(512.5 * 1024**2)

    def test_resource_snapshot_reports_or_explains_every_metric(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/system/resources").json()
        for section in ("memory", "swap"):
            assert body[section]["supported"] in (True, False)
            if not body[section]["supported"]:
                assert body[section]["reason"]
        assert body["cpu"]["cores"] >= 1
        assert "utilization_note" in body["cpu"]

    def test_docker_status_with_a_missing_binary_is_unavailable_not_an_error(self) -> None:
        status = host.docker_status("/nonexistent/docker-binary")
        assert status["available"] is False and status["sandboxes"] == []

    def test_ollama_unreachable_is_reported_plainly(self) -> None:
        status = ollama.ollama_status("http://127.0.0.1:9/v1")
        assert status["reachable"] is False
        assert status["base_url"] == "http://127.0.0.1:9"

    def test_local_models_only_reads_ollama_and_joins_agent_configs(
        self, api_client: Any, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.method)
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "9.9.9"})
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "local-model:latest",
                                "size": 123,
                                "details": {"parameter_size": "7B", "quantization_level": "Q4"},
                            }
                        ]
                    },
                )
            if request.url.path == "/api/ps":
                return httpx.Response(
                    200, json={"models": [{"name": "local-model:latest", "size_vram": 5}]}
                )
            return httpx.Response(404)

        real_client = httpx.Client
        monkeypatch.setattr(
            ollama.httpx,
            "Client",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        for name, model in (("uses-local", "local-model"), ("uses-missing", "not-installed")):
            register_agent(
                db,
                name=name,
                adapter="openai_tools",
                model=model,
                config={"model": model, "base_url": "http://127.0.0.1:11434/v1"},
            )
        db.commit()

        body = api_client.get(f"{API}/system/local-models").json()
        assert set(seen) == {"GET"}
        (model,) = body["models"]
        assert model["running"] is True and model["loaded"]["vram_bytes"] == 5
        assert [a["label"] for a in model["agents"]] == ["uses-local@1.0"]
        assert [a["model"] for a in body["agents_missing_model"]] == ["not-installed"]
        assert "only reads Ollama" in body["read_only_note"]


# --------------------------------------------------------------------------- #
class TestServerSideFilters:
    def test_experiment_search_matches_name_agent_and_model_and_sorts(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        _experiment(
            db,
            agent_name="zeta-agent",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.SOLVED, 1.0)],
        )
        _experiment(
            db,
            agent_name="alpha-agent",
            task_keys=[seeded_task.key],
            results=[(RunOutcome.SOLVED, 1.0)],
        )
        url = f"{API}/experiments"
        by_name = api_client.get(url, params={"search": "ZETA"}).json()
        assert [e["name"] for e in by_name["items"]] == ["exp-zeta-agent"]
        assert (
            by_name["pagination"]["total"] == 1
            if "pagination" in by_name
            else by_name["total"] == 1
        )
        sorted_names = [
            e["name"] for e in api_client.get(url, params={"sort": "name"}).json()["items"]
        ]
        assert sorted_names == ["exp-alpha-agent", "exp-zeta-agent"]
        assert api_client.get(url, params={"sort": "sideways"}).status_code == 422

    def test_excluding_simulated_experiments_keeps_the_total_correct(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.db.models import AgentConfigRecord

        sim = _experiment(
            db, agent_name="sim", task_keys=[seeded_task.key], results=[(RunOutcome.SOLVED, 1.0)]
        )
        real = _experiment(
            db, agent_name="real", task_keys=[seeded_task.key], results=[(RunOutcome.SOLVED, 1.0)]
        )
        # Both fixtures use the mock adapter; mark which config counts as simulated.
        for experiment, simulated in ((sim, True), (real, False)):
            config = db.get(AgentConfigRecord, experiment.agent_config_id)
            assert config is not None
            config.is_simulated = simulated
        db.commit()
        body = api_client.get(f"{API}/experiments", params={"include_simulated": False}).json()
        names = [e["name"] for e in body["items"]]
        assert names == ["exp-real"]
        total = body.get("total", body.get("pagination", {}).get("total"))
        assert total == 1

    def test_failures_accept_several_categories_and_buckets_keep_their_counts(
        self, api_client: Any, db: Session, seeded_task: Any
    ) -> None:
        record = _experiment(
            db,
            agent_name="failing",
            task_keys=[seeded_task.key],
            repetitions=3,
            results=[
                (RunOutcome.UNSOLVED, 1.0),
                (RunOutcome.UNSOLVED, 1.0),
                (RunOutcome.TIMEOUT, 1.0),
            ],
        )
        runs = _runs(db, record.id)
        for run, category in zip(
            runs,
            [
                FailureCategory.INCORRECT_IMPLEMENTATION,
                FailureCategory.TESTS_FAILING_AFTER_PATCH,
                FailureCategory.TIMEOUT,
            ],
            strict=True,
        ):
            store_classification(db, run.id, Classification(category, rationale="fixture"))
        db.commit()

        body = api_client.get(
            f"{API}/failures",
            params={"category": "incorrect_implementation,tests_failing_after_patch"},
        ).json()
        assert {row["annotation"]["category"] for row in body["items"]} == {
            "incorrect_implementation",
            "tests_failing_after_patch",
        }
        # The distribution ignores the category filter, so every bucket keeps its count.
        assert body["distribution"] == {
            "incorrect_implementation": 1,
            "tests_failing_after_patch": 1,
            "timeout": 1,
        }
        # ...but it does respect the other filters.
        scoped = api_client.get(f"{API}/failures", params={"task_id": "no-such-task"}).json()
        assert scoped["distribution"] == {} and scoped["items"] == []
