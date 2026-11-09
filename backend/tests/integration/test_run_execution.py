"""End-to-end run execution, outcome attribution and grading.

These run through the ``local_unsafe`` provider against the fixture repository
so they need no Docker daemon.  They are marked ``simulated`` because the agents
are deterministic test doubles: they prove the harness attributes outcomes
correctly, not that any model is capable.

The central property under test is attribution.  A wrong patch, a crashed
adapter, an exhausted budget, a broken task environment and a dead sandbox must
produce five *different* outcomes, because collapsing them would make every
metric in the platform meaningless.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import RunRecord
from agentbench.metrics.failure import effective_annotation
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.runner import RunExecutor
from agentbench.orchestrator.worker import claim_next_run
from agentbench.registry.agents import register_agent
from agentbench.schemas.enums import FailureCategory, RunOutcome, RunState

pytestmark = [pytest.mark.simulated, pytest.mark.usefixtures("local_sandbox_enabled")]


def run_single(db: Session, task_key: str, agent_id: str, **experiment_kwargs: Any) -> RunRecord:
    """Create a one-task experiment and execute it through the real claim path.

    A run must be claimed before it can execute; going straight to the executor
    would bypass the lease and the PENDING -> CLAIMED transition.
    """
    experiment = experiments.create_experiment(
        db,
        name=f"t-{agent_id[:8]}",
        agent_config_id=agent_id,
        task_keys=[task_key],
        **experiment_kwargs,
    )
    experiments.launch(db, experiment.id)
    db.commit()

    run_id = claim_next_run(db, "test-worker")
    assert run_id is not None, "the worker could not claim the queued run"

    RunExecutor(run_id, "test-worker").execute()

    db.expire_all()
    return db.execute(select(RunRecord).where(RunRecord.id == run_id)).scalars().one()


@pytest.fixture
def solving_agent(db: Session, fixed_calc_source: str) -> Any:
    return register_agent(
        db,
        name="fixes-add",
        adapter="scripted",
        config={"tasks": {"minirepo-add-bug": {"files": {"calc.py": fixed_calc_source}}}},
    )


@pytest.fixture
def noop_agent(db: Session) -> Any:
    return register_agent(db, name="noop", adapter="mock", config={"behaviour": "noop"})


# --------------------------------------------------------------------------- #
class TestSolvedPath:
    def test_a_correct_patch_is_graded_solved(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, solving_agent.id)

        assert run.outcome == RunOutcome.SOLVED
        assert run.state == RunState.COMPLETED
        assert run.grading is not None
        assert run.grading.passed is True
        assert run.grading.score == 1.0

    def test_solved_run_records_every_grading_layer(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, solving_agent.id)
        layers = {layer["name"]: layer["status"] for layer in run.grading.layers}
        assert layers == {
            "environment": "passed",
            "patch": "passed",
            "fail_to_pass": "passed",
            "pass_to_pass": "passed",
        }

    def test_solved_run_captures_the_patch_and_its_stats(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, solving_agent.id)
        assert run.patch and "diff --git a/calc.py b/calc.py" in run.patch
        assert run.patch_stats["files_changed"] == 1
        assert run.patch_stats["paths"][0]["path"] == "calc.py"

    def test_hidden_acceptance_tests_never_appear_in_the_patch(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        """The grading tests are injected after the diff is captured, so they
        must not show up as agent changes."""
        run = run_single(db, seeded_task.key, solving_agent.id)
        assert "test_acceptance_add" not in (run.patch or "")
        assert all("acceptance" not in entry["path"] for entry in run.patch_stats["paths"])

    def test_solved_run_has_no_failure_annotation(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, solving_agent.id)
        assert effective_annotation(db, run.id) is None

    def test_timings_are_recorded_per_phase(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, solving_agent.id)
        assert {"prepare_s", "setup_s", "agent_s", "collect_s", "grading_s", "total_s"} <= set(
            run.timings
        )


class TestUnsolvedPaths:
    def test_no_changes_is_unsolved_with_no_patch_produced(
        self, db: Session, seeded_task: Any, noop_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, noop_agent.id)

        assert run.outcome == RunOutcome.UNSOLVED
        assert run.grading.passed is False
        assert effective_annotation(db, run.id).category == FailureCategory.NO_PATCH_PRODUCED

    def test_an_empty_patch_short_circuits_before_the_tests_run(
        self, db: Session, seeded_task: Any, noop_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, noop_agent.id)
        layer_names = [layer["name"] for layer in run.grading.layers]
        assert "fail_to_pass" not in layer_names
        assert run.grading.evidence["stopped_at"] == "patch"

    def test_an_irrelevant_patch_is_unsolved_as_incorrect_implementation(
        self, db: Session, seeded_task: Any
    ) -> None:
        agent = register_agent(
            db, name="wrong", adapter="mock", config={"behaviour": "wrong_patch"}
        )
        run = run_single(db, seeded_task.key, agent.id)

        assert run.outcome == RunOutcome.UNSOLVED
        assert effective_annotation(db, run.id).category == (
            FailureCategory.INCORRECT_IMPLEMENTATION
        )

    def test_overwriting_the_graded_tests_is_rejected_not_rewarded(
        self, db: Session, seeded_task: Any
    ) -> None:
        """An agent that replaces the hidden test file with a trivially passing
        one must fail, or every task is gameable."""
        agent = register_agent(
            db, name="tamper", adapter="mock", config={"behaviour": "touch_forbidden"}
        )
        run = run_single(db, seeded_task.key, agent.id)

        assert run.outcome == RunOutcome.UNSOLVED
        assert run.grading.passed is False
        annotation = effective_annotation(db, run.id)
        assert annotation.category == FailureCategory.FORBIDDEN_FILES_MODIFIED
        patch_layer = next(layer for layer in run.grading.layers if layer["name"] == "patch")
        assert patch_layer["evidence"]["violations"]

    def test_breaking_a_pre_existing_test_is_classified_as_a_regression(
        self, db: Session, seeded_task: Any
    ) -> None:
        """Passes the hidden acceptance tests but breaks `mul`, which the
        pre-existing suite guards."""
        regressing_source = (
            "def add(left, right):\n    return left + right\n\n\ndef mul(a, b):\n    return a + b\n"
        )
        agent = register_agent(
            db,
            name="regressor",
            adapter="scripted",
            config={"tasks": {"minirepo-add-bug": {"files": {"calc.py": regressing_source}}}},
        )
        run = run_single(db, seeded_task.key, agent.id)

        assert run.outcome == RunOutcome.UNSOLVED
        layers = {layer["name"]: layer["status"] for layer in run.grading.layers}
        assert layers["fail_to_pass"] == "passed"
        assert layers["pass_to_pass"] == "failed"
        assert effective_annotation(db, run.id).category == FailureCategory.EXISTING_REGRESSION


class TestFailureAttribution:
    def test_a_crashed_adapter_is_agent_error_not_unsolved(
        self, db: Session, seeded_task: Any
    ) -> None:
        agent = register_agent(db, name="crasher", adapter="mock", config={"behaviour": "crash"})
        run = run_single(db, seeded_task.key, agent.id)

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert run.grading is None
        assert RunOutcome(run.outcome).counts_toward_success_rate is False
        assert RunOutcome(run.outcome).is_attributable_to_agent is True

    def test_a_sandbox_fault_is_retried_before_being_recorded(
        self, db: Session, seeded_task: Any, settings: Any
    ) -> None:
        """The first sandbox fault schedules a retry and keeps the evidence; it
        does not immediately burn the run."""
        from agentbench.db.models import RunAttemptHistoryRecord

        agent = register_agent(
            db, name="sandbox-fault", adapter="mock", config={"behaviour": "sandbox_fault"}
        )
        run = run_single(db, seeded_task.key, agent.id)

        assert run.state == RunState.PENDING
        assert run.outcome is None
        assert run.infra_attempts == 1
        history = (
            db.execute(
                select(RunAttemptHistoryRecord).where(RunAttemptHistoryRecord.run_id == run.id)
            )
            .scalars()
            .all()
        )
        assert [row.outcome for row in history] == [str(RunOutcome.INFRA_ERROR)]

    def test_an_exhausted_infra_retry_budget_records_infra_error(
        self, db: Session, seeded_task: Any, settings: Any
    ) -> None:
        agent = register_agent(
            db, name="sandbox-fault-2", adapter="mock", config={"behaviour": "sandbox_fault"}
        )
        run = run_single(db, seeded_task.key, agent.id)

        for _ in range(settings.infra_retry_limit + 2):
            db.commit()
            run_id = claim_next_run(db, "test-worker")
            if run_id is None:
                break
            RunExecutor(run_id, "test-worker").execute()
        db.expire_all()
        run = db.execute(select(RunRecord).where(RunRecord.id == run.id)).scalars().one()

        assert run.outcome == RunOutcome.INFRA_ERROR
        assert run.state == RunState.COMPLETED
        assert run.infra_attempts == settings.infra_retry_limit + 1
        assert RunOutcome(run.outcome).is_attributable_to_agent is False
        assert effective_annotation(db, run.id).category == (FailureCategory.INFRASTRUCTURE_FAILURE)

    def test_an_exhausted_budget_is_timeout_not_unsolved(
        self, db: Session, seeded_task: Any
    ) -> None:
        agent = register_agent(
            db,
            name="hanger",
            adapter="mock",
            config={"behaviour": "hang", "hang_seconds": 3600},
        )
        run = run_single(db, seeded_task.key, agent.id, limits_override={"agent_timeout_s": 2})

        assert run.outcome == RunOutcome.TIMEOUT
        assert run.grading is None
        assert effective_annotation(db, run.id).category == FailureCategory.TIMEOUT

    def test_a_broken_task_environment_is_invalid_task(
        self, db: Session, minirepo_spec: Any, noop_agent: Any
    ) -> None:
        """A failing setup command is the task author's problem, so it must not
        be recorded as an agent failure."""
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        broken = minirepo_spec.model_copy(deep=True)
        broken.task_id = "minirepo-broken-env"
        broken.environment.setup_commands = ["exit 17"]
        import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[broken]))
        record = get_task_by_key(db, broken.key)

        run = run_single(db, record.key, noop_agent.id)

        assert run.outcome == RunOutcome.INVALID_TASK
        assert run.grading is None
        assert effective_annotation(db, run.id).category == (
            FailureCategory.ENVIRONMENT_SETUP_FAILURE
        )

    def test_a_missing_repository_is_invalid_task(
        self, db: Session, minirepo_spec: Any, noop_agent: Any
    ) -> None:
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        broken = minirepo_spec.model_copy(deep=True)
        broken.task_id = "minirepo-missing-repo"
        broken.repo.path = "does-not-exist"
        import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[broken]))
        record = get_task_by_key(db, broken.key)

        run = run_single(db, record.key, noop_agent.id)
        assert run.outcome == RunOutcome.INVALID_TASK

    def test_a_mis_specified_test_node_is_a_grading_error_not_an_agent_failure(
        self, db: Session, minirepo_spec: Any, fixed_calc_source: str
    ) -> None:
        """A fail_to_pass node that no runner collects means the task is wrong,
        so the verdict must be withheld rather than reported as UNSOLVED."""
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        broken = minirepo_spec.model_copy(deep=True)
        broken.task_id = "minirepo-bad-node-id"
        broken.tests.fail_to_pass = ["tests/test_acceptance_add.py::test_does_not_exist"]
        import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[broken]))
        record = get_task_by_key(db, broken.key)

        # The agent must still produce a real patch, otherwise the patch layer
        # would fail first and mask the grading problem under test.
        agent = register_agent(
            db,
            name="fixes-bad-node-task",
            adapter="scripted",
            config={"tasks": {broken.task_id: {"files": {"calc.py": fixed_calc_source}}}},
        )
        run = run_single(db, record.key, agent.id)

        assert run.outcome == RunOutcome.GRADING_ERROR
        assert run.grading.passed is None
        assert effective_annotation(db, run.id).category == FailureCategory.GRADING_FAILURE


class TestEvidenceTrail:
    def test_every_run_records_an_ordered_event_stream(
        self, db: Session, seeded_task: Any, solving_agent: Any
    ) -> None:
        from agentbench.trajectory.recorder import read_events

        run = run_single(db, seeded_task.key, solving_agent.id)
        events = read_events(db, run.id, limit=500)

        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        types = [event.event_type for event in events]
        for expected in (
            "run.claimed",
            "sandbox.create",
            "sandbox.ready",
            "env.setup_start",
            "agent.start",
            "file.write",
            "patch.generated",
            "eval.start",
            "eval.end",
            "run.finalized",
            "sandbox.destroy",
        ):
            assert expected in types, f"missing event: {expected}"

    def test_the_sandbox_is_destroyed_even_when_the_agent_crashes(
        self, db: Session, seeded_task: Any
    ) -> None:
        from agentbench.trajectory.recorder import read_events

        agent = register_agent(db, name="crasher2", adapter="mock", config={"behaviour": "crash"})
        run = run_single(db, seeded_task.key, agent.id)
        types = [event.event_type for event in read_events(db, run.id, limit=500)]
        assert "sandbox.destroy" in types

    def test_failure_classification_cites_its_evidence(
        self, db: Session, seeded_task: Any, noop_agent: Any
    ) -> None:
        run = run_single(db, seeded_task.key, noop_agent.id)
        annotation = effective_annotation(db, run.id)
        assert annotation.source == "rule"
        assert annotation.rationale
        assert annotation.evidence_refs["run_id"] == run.id
        assert annotation.evidence_refs["layer_statuses"]
