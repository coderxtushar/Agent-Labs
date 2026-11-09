"""API surface: validation, authorization, pagination and error shape."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.orm import Session

from agentbench.orchestrator import experiment as experiments
from agentbench.registry.agents import register_agent
from agentbench.schemas.enums import RunOutcome, RunState
from tests.conftest import minirepo_task_dict

API = "/api/v1"


@pytest.fixture
def agent(db: Session) -> Any:
    record = register_agent(db, name="noop", adapter="mock", config={"behaviour": "noop"})
    db.commit()
    return record


@pytest.fixture
def completed_experiment(db: Session, seeded_task: Any, agent: Any) -> Any:
    """An experiment with one solved and one unsolved run, written directly.

    Written rather than executed so API tests stay fast and independent of the
    sandbox; run execution itself is covered in test_run_execution.py.
    """
    from sqlalchemy import select

    from agentbench.db.models import GradingResultRecord, RunRecord
    from agentbench.metrics.failure import Classification, store_classification
    from agentbench.schemas.enums import FailureCategory

    experiment = experiments.create_experiment(
        db,
        name="api-fixture",
        agent_config_id=agent.id,
        task_keys=[seeded_task.key],
        repetitions=2,
    )
    experiments.launch(db, experiment.id)
    rows = list(
        db.execute(
            select(RunRecord)
            .where(RunRecord.experiment_id == experiment.id)
            .order_by(RunRecord.attempt_index)
        ).scalars()
    )

    rows[0].state, rows[0].outcome = RunState.COMPLETED, RunOutcome.SOLVED
    rows[0].patch = "diff --git a/calc.py b/calc.py\n+x\n"
    rows[0].patch_stats = {"files_changed": 1, "insertions": 1, "deletions": 0, "paths": []}
    rows[0].timings = {"total_s": 4.2}
    db.add(
        GradingResultRecord(
            run_id=rows[0].id,
            passed=True,
            score=1.0,
            tests_passed=4,
            layers=[{"name": "patch", "status": "passed", "required": True, "summary": "ok"}],
            evidence={"blocking_layers": []},
        )
    )

    rows[1].state, rows[1].outcome = RunState.COMPLETED, RunOutcome.UNSOLVED
    rows[1].timings = {"total_s": 3.1}
    db.add(
        GradingResultRecord(
            run_id=rows[1].id,
            passed=False,
            score=0.5,
            tests_failed=2,
            layers=[{"name": "patch", "status": "failed", "required": True, "summary": "no diff"}],
            evidence={"blocking_layers": ["patch"]},
        )
    )
    store_classification(
        db,
        rows[1].id,
        Classification(FailureCategory.NO_PATCH_PRODUCED, rationale="agent changed nothing"),
    )
    experiments.refresh_state(db, experiment.id)
    db.commit()
    return experiment


# --------------------------------------------------------------------------- #
class TestSystemEndpoints:
    def test_health_reports_sandbox_status(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/health").json()
        assert body["status"] == "ok"
        assert "isolation" in body["sandbox"]

    def test_settings_never_expose_secret_values(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/system/settings").json()
        serialized = json.dumps(body)
        assert "api_key" not in serialized.lower().replace("credentials_present", "")
        assert set(body["credentials_present"]) == {"openai", "anthropic"}
        assert isinstance(body["credentials_present"]["openai"], bool)

    def test_capabilities_expose_outcome_semantics(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/system/capabilities").json()
        outcomes = {o["value"]: o for o in body["vocabularies"]["run_outcomes"]}
        assert outcomes["solved"]["counts_toward_success_rate"] is True
        assert outcomes["infra_error"]["counts_toward_success_rate"] is False
        assert outcomes["infra_error"]["attributable_to_agent"] is False

    def test_stats_signal_an_empty_installation(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/system/stats").json()
        assert body["is_empty"] is True
        assert body["needs_benchmark"] is True

    def test_stats_reflect_seeded_data(self, api_client: Any, seeded_task: Any) -> None:
        body = api_client.get(f"{API}/system/stats").json()
        assert body["tasks"] == 1
        assert body["is_empty"] is False


class TestTaskEndpoints:
    def test_listing_is_paginated(self, api_client: Any, seeded_task: Any) -> None:
        body = api_client.get(f"{API}/tasks", params={"limit": 1}).json()
        assert body["pagination"]["total"] == 1
        assert body["pagination"]["has_more"] is False
        assert len(body["items"]) == 1

    def test_listing_does_not_leak_hidden_acceptance_test_content(
        self, api_client: Any, seeded_task: Any
    ) -> None:
        body = api_client.get(f"{API}/tasks").json()
        serialized = json.dumps(body)
        assert "def test_add_two_positives" not in serialized
        assert body["items"][0]["summary"]["hidden_acceptance_files"]

    def test_benchmarks_endpoint_groups_tasks_and_counts_validated(
        self, api_client: Any, seeded_task: Any
    ) -> None:
        body = api_client.get(f"{API}/tasks/benchmarks").json()
        assert body["benchmarks"] == [
            {
                "benchmark_id": "minirepo",
                "benchmark_version": "1.0",
                "task_count": 1,
                "validated_count": 0,
            }
        ]

    def test_detail_reports_test_counts_and_limits(self, api_client: Any, seeded_task: Any) -> None:
        body = api_client.get(f"{API}/tasks/{seeded_task.key}").json()
        assert body["summary"]["fail_to_pass_count"] == 2
        assert body["summary"]["pass_to_pass_count"] == 2
        assert body["summary"]["limits"]["agent_timeout_s"] == 60

    def test_unknown_task_returns_a_structured_404(self, api_client: Any) -> None:
        response = api_client.get(f"{API}/tasks/nope@1.0")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "http_404"

    def test_import_accepts_a_benchmark_payload(self, api_client: Any) -> None:
        response = api_client.post(
            f"{API}/tasks/import",
            json={"content": {"benchmark_id": "api", "tasks": [minirepo_task_dict()]}},
        )
        assert response.status_code == 201
        assert response.json()["created"] == ["minirepo-add-bug@1.0"]

    def test_import_rejects_an_invalid_task_with_field_errors(self, api_client: Any) -> None:
        response = api_client.post(f"{API}/tasks/import", json={"content": {"task_id": "BAD ID"}})
        assert response.status_code == 400
        assert "task_id" in response.json()["error"]["message"]

    def test_reimporting_a_changed_task_version_conflicts(
        self, api_client: Any, seeded_task: Any
    ) -> None:
        changed = minirepo_task_dict(title="A different title entirely")
        response = api_client.post(f"{API}/tasks/import", json={"content": changed})
        assert response.status_code == 409
        assert "bump the task version" in response.json()["error"]["message"]

    def test_static_spec_validation_needs_no_sandbox(self, api_client: Any) -> None:
        body = api_client.post(f"{API}/tasks/validate-spec", json=minirepo_task_dict()).json()
        assert body["valid"] is True
        assert body["checks"]
        assert "static checks only" in body["note"]

    def test_static_spec_validation_reports_schema_errors(self, api_client: Any) -> None:
        body = api_client.post(
            f"{API}/tasks/validate-spec", json=minirepo_task_dict(task_id="X")
        ).json()
        assert body["valid"] is False
        assert body["errors"]

    def test_export_round_trips_through_import(self, api_client: Any, seeded_task: Any) -> None:
        exported = api_client.get(f"{API}/tasks/export", params={"format": "json"}).text
        payload = json.loads(exported)
        assert payload["tasks"][0]["task_id"] == "minirepo-add-bug"

    def test_jsonl_export_has_one_task_per_line(self, api_client: Any, seeded_task: Any) -> None:
        text = api_client.get(f"{API}/tasks/export", params={"format": "jsonl"}).text
        lines = [line for line in text.splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["task_id"] == "minirepo-add-bug"

    def test_enabling_and_disabling_a_task(self, api_client: Any, seeded_task: Any) -> None:
        body = api_client.patch(
            f"{API}/tasks/{seeded_task.key}/enabled", json={"enabled": False}
        ).json()
        assert body["enabled"] is False

    def test_deleting_a_task_with_runs_is_refused(
        self, api_client: Any, completed_experiment: Any, seeded_task: Any
    ) -> None:
        response = api_client.delete(f"{API}/tasks/{seeded_task.key}")
        assert response.status_code == 409
        assert "disable it instead" in response.json()["error"]["message"]


class TestAgentEndpoints:
    def test_adapters_expose_capabilities(self, api_client: Any) -> None:
        body = api_client.get(f"{API}/agents/adapters").json()
        keys = {a["key"] for a in body["adapters"]}
        assert {"mock", "scripted", "shell_command", "openai_tools"} <= keys

    def test_agent_listing_includes_live_readiness(self, api_client: Any, agent: Any) -> None:
        body = api_client.get(f"{API}/agents").json()
        assert body["items"][0]["readiness"]["ready"] is True
        assert body["items"][0]["is_simulated"] is True

    def test_creating_an_agent_validates_the_adapter_config(self, api_client: Any) -> None:
        response = api_client.post(
            f"{API}/agents",
            json={"name": "bad", "adapter": "mock", "config": {"behaviour": "teleport"}},
        )
        assert response.status_code == 400
        assert "behaviour" in response.json()["error"]["message"]

    def test_creating_an_agent_rejects_an_unknown_adapter(self, api_client: Any) -> None:
        response = api_client.post(f"{API}/agents", json={"name": "x", "adapter": "nope"})
        assert response.status_code == 400

    def test_duplicate_agent_version_conflicts(self, api_client: Any, agent: Any) -> None:
        response = api_client.post(
            f"{API}/agents",
            json={"name": "noop", "adapter": "mock", "config": {"behaviour": "noop"}},
        )
        assert response.status_code == 409

    def test_credential_shaped_config_values_are_redacted_in_responses(
        self, api_client: Any
    ) -> None:
        api_client.post(
            f"{API}/agents",
            json={
                "name": "cli-agent",
                "adapter": "shell_command",
                "config": {"command": "echo hi", "env": {"NOTE": "sk-" + "a" * 30}},
            },
        )
        body = api_client.get(f"{API}/agents").json()
        serialized = json.dumps(body)
        assert "sk-" + "a" * 30 not in serialized
        assert "REDACTED" in serialized

    def test_deleting_a_referenced_agent_is_refused(
        self, api_client: Any, completed_experiment: Any, agent: Any
    ) -> None:
        response = api_client.delete(f"{API}/agents/{agent.id}")
        assert response.status_code == 409
        assert "disable it instead" in response.json()["error"]["message"]


class TestExperimentEndpoints:
    def test_creating_an_experiment_returns_progress(
        self, api_client: Any, seeded_task: Any, agent: Any
    ) -> None:
        response = api_client.post(
            f"{API}/experiments",
            json={
                "name": "from-api",
                "agent_config_id": agent.id,
                "task_keys": [seeded_task.key],
                "repetitions": 2,
                "launch": True,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["progress"]["total"] == 2
        assert body["progress"]["pending"] == 2

    def test_creating_with_an_unknown_task_is_a_400(self, api_client: Any, agent: Any) -> None:
        response = api_client.post(
            f"{API}/experiments",
            json={"name": "x", "agent_config_id": agent.id, "task_keys": ["nope@1.0"]},
        )
        assert response.status_code == 400

    def test_creating_with_no_tasks_is_a_422(self, api_client: Any, agent: Any) -> None:
        response = api_client.post(
            f"{API}/experiments",
            json={"name": "x", "agent_config_id": agent.id, "task_keys": []},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_unknown_request_field_is_rejected(self, api_client: Any, agent: Any) -> None:
        response = api_client.post(
            f"{API}/experiments",
            json={
                "name": "x",
                "agent_config_id": agent.id,
                "task_keys": ["a@1.0"],
                "surprise": 1,
            },
        )
        assert response.status_code == 422

    def test_metrics_expose_denominators_and_warnings(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = api_client.get(f"{API}/experiments/{completed_experiment.id}/metrics").json()
        rate = body["success_rate_attributable"]
        assert rate["numerator"] == 1
        assert rate["denominator"] == 2
        assert rate["small_sample"] is True
        assert rate["interpretation_warning"]
        assert any("simulated" in warning.lower() for warning in body["warnings"])

    def test_metrics_separate_counted_from_excluded_outcomes(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = api_client.get(f"{API}/experiments/{completed_experiment.id}/metrics").json()
        assert body["outcomes"]["solved"] == 1
        assert body["outcomes"]["unsolved"] == 1
        assert body["reliability"]["excluded_from_rates"] == 0

    def test_experiment_runs_can_be_filtered_by_outcome(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = api_client.get(
            f"{API}/experiments/{completed_experiment.id}/runs",
            params={"outcome": "solved"},
        ).json()
        assert body["pagination"]["total"] == 1
        assert body["items"][0]["outcome"] == "solved"

    def test_markdown_report_states_its_configuration(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        text = api_client.get(
            f"{API}/experiments/{completed_experiment.id}/report",
            params={"format": "markdown"},
        ).text
        assert "# Experiment report" in text
        assert "Simulated agent" in text
        assert "## Reproduction" in text

    def test_csv_report_has_one_row_per_run(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        text = api_client.get(
            f"{API}/experiments/{completed_experiment.id}/report", params={"format": "csv"}
        ).text
        lines = [line for line in text.splitlines() if line.strip()]
        assert len(lines) == 3
        assert "counts_toward_success_rate" in lines[0]

    def test_json_report_includes_the_config_snapshot(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = json.loads(
            api_client.get(
                f"{API}/experiments/{completed_experiment.id}/report",
                params={"format": "json"},
            ).text
        )
        assert body["config_snapshot"]["benchmark"]["tasks"]
        assert len(body["runs"]) == 2

    def test_invalid_report_format_is_rejected(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        response = api_client.get(
            f"{API}/experiments/{completed_experiment.id}/report", params={"format": "pdf"}
        )
        assert response.status_code == 422

    def test_cancel_then_resume_is_refused(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        api_client.post(f"{API}/experiments/{completed_experiment.id}/cancel")
        response = api_client.post(f"{API}/experiments/{completed_experiment.id}/resume")
        assert response.status_code == 400


class TestRunEndpoints:
    def _run_id(self, api_client: Any, experiment_id: str, outcome: str) -> str:
        body = api_client.get(
            f"{API}/experiments/{experiment_id}/runs", params={"outcome": outcome}
        ).json()
        return body["items"][0]["id"]

    def test_run_detail_exposes_attribution_flags(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        run_id = self._run_id(api_client, completed_experiment.id, "solved")
        body = api_client.get(f"{API}/runs/{run_id}").json()
        assert body["counts_toward_success_rate"] is True
        assert body["attributable_to_agent"] is True
        assert body["grading"]["passed"] is True

    def test_run_detail_includes_failure_evidence(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        run_id = self._run_id(api_client, completed_experiment.id, "unsolved")
        body = api_client.get(f"{API}/runs/{run_id}").json()
        assert body["failure"]["category"] == "no_patch_produced"
        assert body["failure"]["model_generated"] is False
        assert body["grading"]["evidence"]["blocking_layers"] == ["patch"]

    def test_patch_endpoint_returns_a_diff(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        run_id = self._run_id(api_client, completed_experiment.id, "solved")
        response = api_client.get(f"{API}/runs/{run_id}/patch")
        assert response.status_code == 200
        assert "diff --git" in response.text

    def test_grading_endpoint_explains_why_a_run_was_not_graded(
        self, api_client: Any, seeded_task: Any, agent: Any
    ) -> None:
        from sqlalchemy import select

        from agentbench.db.models import RunRecord
        from agentbench.db.session import session_scope

        response = api_client.post(
            f"{API}/experiments",
            json={
                "name": "ungraded",
                "agent_config_id": agent.id,
                "task_keys": [seeded_task.key],
            },
        )
        experiment_id = response.json()["id"]
        with session_scope() as session:
            run = (
                session.execute(select(RunRecord).where(RunRecord.experiment_id == experiment_id))
                .scalars()
                .one()
            )
            run.state = RunState.COMPLETED
            run.outcome = RunOutcome.INFRA_ERROR
            run_id = run.id

        body = api_client.get(f"{API}/runs/{run_id}/grading").json()
        assert body["grading"] is None
        assert "infra_error" in body["reason"]

    def test_human_annotation_supersedes_the_rule_classification(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        run_id = self._run_id(api_client, completed_experiment.id, "unsolved")
        response = api_client.post(
            f"{API}/runs/{run_id}/annotations",
            json={
                "category": "context_or_instruction_issue",
                "rationale": "the problem statement was ambiguous",
                "author": "reviewer",
            },
        )
        assert response.status_code == 201
        assert response.json()["source"] == "human"

        detail = api_client.get(f"{API}/runs/{run_id}").json()
        assert detail["failure"]["category"] == "context_or_instruction_issue"
        assert len(detail["failure_history"]) == 2
        superseded = [a for a in detail["failure_history"] if a["superseded"]]
        assert superseded[0]["category"] == "no_patch_produced"

    def test_annotation_rejects_an_unknown_category(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        run_id = self._run_id(api_client, completed_experiment.id, "unsolved")
        response = api_client.post(f"{API}/runs/{run_id}/annotations", json={"category": "made_up"})
        assert response.status_code == 422

    def test_events_are_ordered_and_report_their_cursor(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        run_id = self._run_id(api_client, completed_experiment.id, "solved")
        body = api_client.get(f"{API}/runs/{run_id}/events").json()
        assert body["ordering_note"]
        assert [event["seq"] for event in body["events"]] == sorted(
            event["seq"] for event in body["events"]
        )

    def test_unknown_run_is_a_404(self, api_client: Any) -> None:
        assert api_client.get(f"{API}/runs/deadbeef").status_code == 404


class TestMetricsEndpoints:
    def test_overview_is_computed_from_stored_rows(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = api_client.get(f"{API}/overview").json()
        assert body["runs"]["total"] == 2
        assert body["runs"]["by_outcome"] == {"solved": 1, "unsolved": 1}
        assert body["runs"]["overall_success_rate_attributable"]["denominator"] == 2

    def test_comparison_refuses_identical_arms(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        response = api_client.get(
            f"{API}/compare",
            params={"baseline": completed_experiment.id, "candidate": completed_experiment.id},
        )
        assert response.status_code == 400

    def test_comparison_makes_no_significance_claim_on_small_samples(
        self, api_client: Any, completed_experiment: Any, seeded_task: Any, agent: Any
    ) -> None:
        other = api_client.post(
            f"{API}/experiments",
            json={
                "name": "candidate",
                "agent_config_id": agent.id,
                "task_keys": [seeded_task.key],
            },
        ).json()
        body = api_client.get(
            f"{API}/compare",
            params={"baseline": completed_experiment.id, "candidate": other["id"]},
        ).json()
        assert body["statistical_note"]["claims_significance"] is False
        assert "at least" in body["statistical_note"]["reason"]

    def test_failures_endpoint_returns_distribution_and_evidence(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = api_client.get(f"{API}/failures").json()
        assert body["distribution"] == {"no_patch_produced": 1}
        item = body["items"][0]
        assert item["annotation"]["rationale"]
        assert item["grading_summary"]["blocking_layers"] == ["patch"]

    def test_failures_can_be_filtered_by_category(
        self, api_client: Any, completed_experiment: Any
    ) -> None:
        body = api_client.get(f"{API}/failures", params={"category": "timeout"}).json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0


class TestAuthorization:
    def test_mutating_requests_require_a_token_when_configured(
        self, api_client: Any, monkeypatch: pytest.MonkeyPatch, agent: Any, seeded_task: Any
    ) -> None:
        from agentbench.config import get_settings, reset_settings_cache

        monkeypatch.setenv("AGENTBENCH_API_TOKEN", "secret-token")
        reset_settings_cache()
        get_settings()

        payload = {
            "name": "x",
            "agent_config_id": agent.id,
            "task_keys": [seeded_task.key],
        }
        assert api_client.post(f"{API}/experiments", json=payload).status_code == 401
        assert api_client.get(f"{API}/overview").status_code == 200

        authorized = api_client.post(
            f"{API}/experiments",
            json=payload,
            headers={"Authorization": "Bearer secret-token"},
        )
        assert authorized.status_code == 201

    def test_a_wrong_token_is_rejected(
        self, api_client: Any, monkeypatch: pytest.MonkeyPatch, agent: Any, seeded_task: Any
    ) -> None:
        from agentbench.config import get_settings, reset_settings_cache

        monkeypatch.setenv("AGENTBENCH_API_TOKEN", "secret-token")
        reset_settings_cache()
        get_settings()

        response = api_client.post(
            f"{API}/experiments",
            json={"name": "x", "agent_config_id": agent.id, "task_keys": [seeded_task.key]},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401
