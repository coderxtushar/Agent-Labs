"""Result provenance, and metric values checked against hand computation.

The metric assertions here deliberately use numbers worked out by hand rather
than by calling the same code under test, so an arithmetic regression cannot
hide behind a self-consistent implementation.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from sqlalchemy.orm import Session

from agentbench.metrics.compute import make_rate, pass_at_k, wilson_interval
from agentbench.schemas.provenance import (
    Provenance,
    classify,
    describe,
    from_agent_snapshot,
)


class TestProvenanceClassification:
    def test_a_non_model_adapter_is_simulated(self) -> None:
        assert (
            classify(is_simulated=True, requires_credentials=(), readiness_ready=False, model=None)
            is Provenance.SIMULATED
        )

    def test_a_model_adapter_without_credentials_is_an_offline_adapter_test(self) -> None:
        """This is the case that must never be read as a model result."""
        assert (
            classify(
                is_simulated=False,
                requires_credentials=("AGENTBENCH_OPENAI_API_KEY",),
                readiness_ready=False,
                model="gpt-4o-mini",
            )
            is Provenance.OFFLINE_ADAPTER_TEST
        )

    def test_a_model_adapter_with_credentials_is_a_live_model_run(self) -> None:
        assert (
            classify(
                is_simulated=False,
                requires_credentials=("AGENTBENCH_OPENAI_API_KEY",),
                readiness_ready=True,
                model="gpt-4o-mini",
            )
            is Provenance.LIVE_MODEL
        )

    def test_an_adapter_needing_no_credentials_and_no_model_is_an_external_agent(self) -> None:
        assert (
            classify(is_simulated=False, requires_credentials=(), readiness_ready=True, model=None)
            is Provenance.EXTERNAL_AGENT
        )

    def test_only_a_live_model_run_counts_as_a_model_result(self) -> None:
        model_results = [kind for kind in Provenance if kind.is_model_result]
        assert model_results == [Provenance.LIVE_MODEL]

    def test_every_provenance_carries_a_label_and_a_caveat(self) -> None:
        for kind in Provenance:
            payload = describe(kind)
            assert payload["label"]
            assert payload["caveat"]
            assert payload["value"] == str(kind)

    def test_no_caveat_claims_general_coding_capability(self) -> None:
        for kind in Provenance:
            caveat = kind.caveat.lower()
            assert "best" not in caveat
            assert "state of the art" not in caveat
        assert "does not measure general coding capability" in Provenance.LIVE_MODEL.caveat

    def test_provenance_is_derived_for_snapshots_that_predate_the_field(self) -> None:
        legacy = {
            "is_simulated": False,
            "model": "gpt-4o-mini",
            "capabilities": {"requires_credentials": ["AGENTBENCH_OPENAI_API_KEY"]},
            "readiness_at_launch": {"ready": False},
        }
        assert from_agent_snapshot(legacy) is Provenance.OFFLINE_ADAPTER_TEST

    def test_a_stored_value_is_honoured(self) -> None:
        assert from_agent_snapshot({"provenance": "live_model"}) is Provenance.LIVE_MODEL

    def test_an_unknown_stored_value_falls_back_to_derivation(self) -> None:
        snapshot = {"provenance": "wishful_thinking", "is_simulated": True}
        assert from_agent_snapshot(snapshot) is Provenance.SIMULATED


@pytest.mark.simulated
class TestProvenanceIsRecordedAndSurfaced:
    def _experiment(self, db: Session, task_key: str, agent: Any) -> Any:
        from agentbench.orchestrator import experiment as experiments

        record = experiments.create_experiment(
            db, name=f"prov-{agent.name}", agent_config_id=agent.id, task_keys=[task_key]
        )
        db.commit()
        return record

    def test_a_simulated_agent_is_recorded_as_simulated_at_launch(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.registry.agents import register_agent

        agent = register_agent(db, name="sim", adapter="mock", config={"behaviour": "noop"})
        experiment = self._experiment(db, seeded_task.key, agent)

        assert experiment.agent_snapshot["provenance"] == str(Provenance.SIMULATED)

    def test_a_model_adapter_without_a_key_is_recorded_as_an_offline_adapter_test(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.registry.agents import register_agent

        agent = register_agent(
            db,
            name="model-nokey",
            adapter="openai_tools",
            model="gpt-4o-mini",
            config={"model": "gpt-4o-mini"},
        )
        experiment = self._experiment(db, seeded_task.key, agent)

        assert experiment.agent_snapshot["provenance"] == str(Provenance.OFFLINE_ADAPTER_TEST)

    def test_a_model_adapter_with_a_key_is_recorded_as_a_live_model_run(
        self, db: Session, seeded_task: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agentbench.config import reset_settings_cache
        from agentbench.registry.agents import register_agent

        monkeypatch.setenv("AGENTBENCH_OPENAI_API_KEY", "sk-test-0000000000000000")
        reset_settings_cache()

        agent = register_agent(
            db,
            name="model-withkey",
            adapter="openai_tools",
            model="gpt-4o-mini",
            config={"model": "gpt-4o-mini"},
        )
        experiment = self._experiment(db, seeded_task.key, agent)

        assert experiment.agent_snapshot["provenance"] == str(Provenance.LIVE_MODEL)

    def test_an_external_cli_agent_is_recorded_as_an_external_agent(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.registry.agents import register_agent

        agent = register_agent(
            db, name="cli", adapter="shell_command", config={"command": "echo hi"}
        )
        experiment = self._experiment(db, seeded_task.key, agent)

        assert experiment.agent_snapshot["provenance"] == str(Provenance.EXTERNAL_AGENT)

    def test_metrics_expose_provenance_and_a_caveat_warning(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.metrics.compute import experiment_metrics
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent

        agent = register_agent(db, name="sim2", adapter="mock", config={"behaviour": "noop"})
        experiment = self._experiment(db, seeded_task.key, agent)
        experiments.launch(db, experiment.id)
        db.commit()

        payload = experiment_metrics(db, experiment.id).to_dict()

        assert payload["provenance"]["value"] == str(Provenance.SIMULATED)
        assert payload["provenance"]["is_model_result"] is False
        assert any("Simulated agent" in warning for warning in payload["warnings"])

    def test_the_markdown_report_states_provenance(self, db: Session, seeded_task: Any) -> None:
        from agentbench.metrics.export import render_report
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent

        agent = register_agent(db, name="sim3", adapter="mock", config={"behaviour": "noop"})
        experiment = self._experiment(db, seeded_task.key, agent)
        experiments.launch(db, experiment.id)
        db.commit()

        markdown = render_report(db, experiment.id, fmt="markdown")

        assert "Result provenance" in markdown
        assert "simulated" in markdown
        assert "not a model benchmark result" in markdown

    def test_the_csv_export_carries_a_provenance_column(
        self, db: Session, seeded_task: Any
    ) -> None:
        import csv
        import io

        from agentbench.metrics.export import render_report
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent

        agent = register_agent(db, name="sim4", adapter="mock", config={"behaviour": "noop"})
        experiment = self._experiment(db, seeded_task.key, agent)
        experiments.launch(db, experiment.id)
        db.commit()

        rows = list(csv.DictReader(io.StringIO(render_report(db, experiment.id, fmt="csv"))))

        assert rows
        assert rows[0]["provenance"] == str(Provenance.SIMULATED)

    def test_comparing_arms_of_different_provenance_is_blocked(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.metrics.compare import compare_experiments
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent

        simulated = register_agent(db, name="cmp-sim", adapter="mock", config={"behaviour": "noop"})
        external = register_agent(
            db, name="cmp-cli", adapter="shell_command", config={"command": "echo hi"}
        )
        first = self._experiment(db, seeded_task.key, simulated)
        second = self._experiment(db, seeded_task.key, external)
        experiments.launch(db, first.id)
        experiments.launch(db, second.id)
        db.commit()

        comparison = compare_experiments(db, first.id, second.id).to_dict()

        assert comparison["comparable"] is False
        assert any("provenance" in blocker for blocker in comparison["blockers"])


class TestRatesAgainstHandComputation:
    def test_wilson_interval_matches_a_hand_computed_value(self) -> None:
        """n=10, successes=7, z=1.959964.

        denominator = 1 + z^2/n          = 1.3841459
        centre      = p + z^2/(2n)       = 0.7 + 0.1920730 = 0.8920730
        spread      = z*sqrt(p(1-p)/n + z^2/(4n^2))
                    = 1.959964*sqrt(0.021 + 0.0096037) = 1.959964*0.1749391
        """
        z = 1.959963985
        n, successes = 10, 7
        p = successes / n
        denominator = 1 + z**2 / n
        centre = p + z**2 / (2 * n)
        spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
        expected = ((centre - spread) / denominator, (centre + spread) / denominator)

        low, high = wilson_interval(successes, n)

        assert low == pytest.approx(expected[0], abs=1e-9)
        assert high == pytest.approx(expected[1], abs=1e-9)
        assert low == pytest.approx(0.3967, abs=1e-4)
        assert high == pytest.approx(0.8922, abs=1e-4)

    def test_the_bundled_demo_result_reproduces_its_published_interval(self) -> None:
        """4 solved of 15 is the figure quoted in the README.

        The expected bounds below were computed independently (see the hand
        computation in the test above) and caught a wrong interval in an earlier
        README, which claimed 9.7-51.1%.
        """
        rate = make_rate(4, 15)
        assert rate.value == pytest.approx(4 / 15)
        assert rate.to_dict()["percent"] == pytest.approx(26.67, abs=0.01)
        assert rate.ci_low == pytest.approx(0.108975, abs=1e-5)
        assert rate.ci_high == pytest.approx(0.519504, abs=1e-5)

    def test_a_saturated_proportion_stays_inside_the_unit_interval(self) -> None:
        low, high = wilson_interval(3, 3)
        assert 0.0 < low < 1.0
        assert high == 1.0

    def test_pass_at_k_matches_the_binomial_identity(self) -> None:
        """1 - C(n-c, k)/C(n, k) for n=10, c=4, k=3."""
        n, c, k = 10, 4, 3
        expected = 1.0 - (math.comb(n - c, k) / math.comb(n, k))
        assert pass_at_k(n, c, k) == pytest.approx(expected)
        assert pass_at_k(n, c, k) == pytest.approx(1 - 20 / 120)

    @pytest.mark.parametrize(
        ("n", "c", "k", "expected"),
        [
            (5, 2, 2, 0.7),
            (4, 1, 1, 0.25),
            (10, 0, 5, 0.0),
            (10, 10, 5, 1.0),
            (6, 3, 2, 1 - math.comb(3, 2) / math.comb(6, 2)),
        ],
    )
    def test_pass_at_k_table(self, n: int, c: int, k: int, expected: float) -> None:
        assert pass_at_k(n, c, k) == pytest.approx(expected)

    def test_pass_at_k_is_undefined_below_k_samples(self) -> None:
        assert pass_at_k(2, 1, 3) is None


@pytest.mark.simulated
class TestDenominatorTreatment:
    def test_incomplete_runs_are_excluded_from_every_rate(
        self, db: Session, seeded_task: Any
    ) -> None:
        """A pending run must not silently count as a failure."""
        from sqlalchemy import select

        from agentbench.db.models import RunRecord
        from agentbench.metrics.compute import experiment_metrics
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent
        from agentbench.schemas.enums import RunOutcome, RunState

        agent = register_agent(db, name="denom", adapter="mock", config={"behaviour": "noop"})
        experiment = experiments.create_experiment(
            db,
            name="denom-exp",
            agent_config_id=agent.id,
            task_keys=[seeded_task.key],
            repetitions=4,
        )
        experiments.launch(db, experiment.id)
        runs = (
            db.execute(
                select(RunRecord)
                .where(RunRecord.experiment_id == experiment.id)
                .order_by(RunRecord.attempt_index)
            )
            .scalars()
            .all()
        )

        runs[0].state, runs[0].outcome = RunState.COMPLETED, RunOutcome.SOLVED
        runs[1].state, runs[1].outcome = RunState.COMPLETED, RunOutcome.UNSOLVED
        runs[2].state = RunState.AGENT_RUNNING
        db.commit()

        payload = experiment_metrics(db, experiment.id).to_dict()

        assert payload["success_rate_attributable"]["denominator"] == 2
        assert payload["success_rate_attributable"]["numerator"] == 1
        assert payload["completeness"]["finished_runs"] == 2
        assert payload["completeness"]["pending_runs"] == 1
        assert payload["completeness"]["active_runs"] == 1
        assert payload["completeness"]["is_complete"] is False
        assert any("incomplete experiment" in warning for warning in payload["warnings"])

    def test_non_attributable_outcomes_leave_the_denominator(
        self, db: Session, seeded_task: Any
    ) -> None:
        from sqlalchemy import select

        from agentbench.db.models import RunRecord
        from agentbench.metrics.compute import experiment_metrics
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent
        from agentbench.schemas.enums import RunOutcome, RunState

        agent = register_agent(db, name="denom2", adapter="mock", config={"behaviour": "noop"})
        experiment = experiments.create_experiment(
            db,
            name="denom-exp-2",
            agent_config_id=agent.id,
            task_keys=[seeded_task.key],
            repetitions=5,
        )
        experiments.launch(db, experiment.id)
        runs = (
            db.execute(
                select(RunRecord)
                .where(RunRecord.experiment_id == experiment.id)
                .order_by(RunRecord.attempt_index)
            )
            .scalars()
            .all()
        )

        for run, outcome in zip(
            runs,
            [
                RunOutcome.SOLVED,
                RunOutcome.TIMEOUT,
                RunOutcome.INFRA_ERROR,
                RunOutcome.INVALID_TASK,
                RunOutcome.GRADING_ERROR,
            ],
            strict=True,
        ):
            run.state, run.outcome = RunState.COMPLETED, outcome
        db.commit()

        payload = experiment_metrics(db, experiment.id).to_dict()

        # solved + timeout are attributable; the other three are not.
        assert payload["success_rate_attributable"]["denominator"] == 2
        assert payload["success_rate_graded"]["denominator"] == 1
        assert payload["reliability"]["excluded_from_rates"] == 3

    def test_a_zero_denominator_is_null_not_zero(self, db: Session, seeded_task: Any) -> None:
        from agentbench.metrics.compute import experiment_metrics
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent

        agent = register_agent(db, name="denom3", adapter="mock", config={"behaviour": "noop"})
        experiment = experiments.create_experiment(
            db, name="denom-exp-3", agent_config_id=agent.id, task_keys=[seeded_task.key]
        )
        experiments.launch(db, experiment.id)
        db.commit()

        payload = experiment_metrics(db, experiment.id).to_dict()

        assert payload["success_rate_attributable"]["value"] is None
        assert payload["success_rate_attributable"]["percent"] is None
        assert payload["success_rate_attributable"]["denominator"] == 0


class TestReproducibilityRecord:
    @pytest.mark.simulated
    def test_the_snapshot_pins_every_identity_needed_to_reproduce(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent

        agent = register_agent(
            db,
            name="repro",
            adapter="mock",
            model="n/a",
            config={"behaviour": "noop"},
            prompt_version="2.1",
            tools_version="3.4",
        )
        experiment = experiments.create_experiment(
            db,
            name="repro-exp",
            agent_config_id=agent.id,
            task_keys=[seeded_task.key],
            repetitions=2,
            seed=7,
        )
        snapshot = experiment.config_snapshot

        task_entry = snapshot["benchmark"]["tasks"][0]
        assert task_entry["key"] == seeded_task.key
        assert task_entry["grading_version"]
        assert task_entry["validation_status"]
        assert task_entry["image"]

        assert snapshot["agent"]["adapter"] == "mock"
        assert snapshot["agent"]["prompt_version"] == "2.1"
        assert snapshot["agent"]["tools_version"] == "3.4"
        assert snapshot["agent"]["provenance"] == str(Provenance.SIMULATED)

        assert snapshot["execution"]["seed"] == 7
        assert snapshot["execution"]["repetitions"] == 2
        assert snapshot["execution"]["sandbox_provider"]
        assert snapshot["execution"]["hard_ceilings"]["max_memory_mb"]
        assert snapshot["platform"]["version"]
