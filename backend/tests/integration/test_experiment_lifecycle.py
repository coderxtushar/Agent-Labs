"""Experiment lifecycle: idempotency, claiming, cancellation, resumption.

The properties under test are the ones that make an interrupted experiment
recoverable:

* re-launching never duplicates a completed run;
* two workers cannot claim the same run;
* a crashed worker's runs become reclaimable once the lease expires;
* resuming does not discard completed work, and does not steal a run from a
  worker that may still be alive unless forced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import RunRecord
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.worker import (
    Worker,
    claim_next_run,
    reclaim_expired_runs,
    release_claim,
)
from agentbench.registry.agents import register_agent
from agentbench.schemas.enums import ExperimentState, RunOutcome, RunState

pytestmark = pytest.mark.simulated


@pytest.fixture
def agent(db: Session) -> Any:
    return register_agent(db, name="noop", adapter="mock", config={"behaviour": "noop"})


def make_experiment(db: Session, task_key: str, agent_id: str, **kwargs: Any) -> Any:
    return experiments.create_experiment(
        db, name="exp", agent_config_id=agent_id, task_keys=[task_key], **kwargs
    )


def runs_of(db: Session, experiment_id: str) -> list[RunRecord]:
    return list(
        db.execute(
            select(RunRecord)
            .where(RunRecord.experiment_id == experiment_id)
            .order_by(RunRecord.attempt_index)
        ).scalars()
    )


# --------------------------------------------------------------------------- #
class TestCreation:
    def test_agent_configuration_is_snapshotted_at_creation(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        snapshot = experiment.agent_snapshot

        agent.config = {"behaviour": "crash"}
        agent.model = "changed-later"
        db.flush()

        assert snapshot["config"] == {"behaviour": "noop"}
        assert experiment.agent_snapshot["config"] == {"behaviour": "noop"}

    def test_config_snapshot_captures_everything_needed_to_reproduce(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=3, seed=42)
        snapshot = experiment.config_snapshot

        assert snapshot["benchmark"]["tasks"][0]["key"] == seeded_task.key
        assert snapshot["execution"]["repetitions"] == 3
        assert snapshot["execution"]["seed"] == 42
        assert snapshot["execution"]["sandbox_provider"]
        assert snapshot["agent"]["adapter"] == "mock"
        assert snapshot["platform"]["version"]

    def test_unknown_task_is_rejected(self, db: Session, agent: Any) -> None:
        with pytest.raises(experiments.ExperimentError) as exc:
            make_experiment(db, "nope@1.0", agent.id)
        assert "unknown task" in str(exc.value)

    def test_unknown_agent_is_rejected(self, db: Session, seeded_task: Any) -> None:
        with pytest.raises(experiments.ExperimentError) as exc:
            make_experiment(db, seeded_task.key, "no-such-agent")
        assert "unknown agent" in str(exc.value)

    def test_disabled_agent_is_rejected(self, db: Session, seeded_task: Any, agent: Any) -> None:
        agent.enabled = False
        db.flush()
        with pytest.raises(experiments.ExperimentError) as exc:
            make_experiment(db, seeded_task.key, agent.id)
        assert "disabled" in str(exc.value)

    def test_empty_task_list_is_rejected(self, db: Session, agent: Any) -> None:
        with pytest.raises(experiments.ExperimentError):
            experiments.create_experiment(db, name="x", agent_config_id=agent.id, task_keys=[])

    def test_absurd_repetition_count_is_rejected(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        with pytest.raises(experiments.ExperimentError):
            make_experiment(db, seeded_task.key, agent.id, repetitions=1000)

    def test_concurrency_is_clamped_to_the_global_ceiling(
        self, db: Session, seeded_task: Any, agent: Any, settings: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, concurrency=999)
        assert experiment.concurrency == settings.max_concurrent_runs


class TestIdempotency:
    def test_launch_creates_one_run_per_task_and_repetition(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=3)
        experiments.launch(db, experiment.id)
        rows = runs_of(db, experiment.id)
        assert [row.attempt_index for row in rows] == [0, 1, 2]

    def test_relaunch_does_not_duplicate_runs(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        before = {row.id for row in runs_of(db, experiment.id)}

        experiments.launch(db, experiment.id)
        experiments.materialize_runs(db, experiment)

        assert {row.id for row in runs_of(db, experiment.id)} == before

    def test_relaunch_leaves_completed_runs_untouched(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        rows = runs_of(db, experiment.id)
        rows[0].state = RunState.COMPLETED
        rows[0].outcome = RunOutcome.SOLVED
        db.flush()

        experiments.launch(db, experiment.id)

        refreshed = runs_of(db, experiment.id)
        assert refreshed[0].state == RunState.COMPLETED
        assert refreshed[0].outcome == RunOutcome.SOLVED

    def test_seeds_are_derived_deterministically_per_repetition(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=3, seed=100)
        experiments.launch(db, experiment.id)
        assert [row.seed for row in runs_of(db, experiment.id)] == [100, 101, 102]

    def test_no_seed_means_no_per_run_seed(self, db: Session, seeded_task: Any, agent: Any) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        assert all(row.seed is None for row in runs_of(db, experiment.id))

    def test_task_validation_status_is_recorded_on_the_run(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        assert runs_of(db, experiment.id)[0].task_validation_status == "unvalidated"


class TestClaiming:
    def test_a_run_can_only_be_claimed_once(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=1)
        experiments.launch(db, experiment.id)
        db.commit()

        first = claim_next_run(db, "worker-a")
        second = claim_next_run(db, "worker-b")

        assert first is not None
        assert second is None

    def test_claiming_sets_the_lease_and_owner(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()

        run_id = claim_next_run(db, "worker-a")
        run = db.get(RunRecord, run_id)

        assert run.state == RunState.CLAIMED
        assert run.claimed_by == "worker-a"
        assert run.claim_expires_at is not None

    def test_runs_of_a_cancelled_experiment_are_not_claimed(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        experiments.cancel(db, experiment.id)
        db.commit()

        assert claim_next_run(db, "worker-a") is None

    def test_runs_of_a_draft_experiment_are_not_claimed(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.materialize_runs(db, experiment)
        db.commit()

        assert claim_next_run(db, "worker-a") is None

    def test_releasing_a_claim_returns_the_run_to_the_queue(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "worker-a")

        release_claim(db, run_id, "shutting down")
        db.commit()

        assert db.get(RunRecord, run_id).state == RunState.PENDING
        assert claim_next_run(db, "worker-b") == run_id


class TestCrashRecovery:
    def test_expired_lease_makes_a_run_reclaimable(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "dead-worker")
        run = db.get(RunRecord, run_id)
        run.state = RunState.AGENT_RUNNING
        run.claim_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        db.commit()

        assert reclaim_expired_runs(db) == 1
        assert db.get(RunRecord, run_id).state == RunState.PENDING

    def test_a_live_lease_is_not_reclaimed(self, db: Session, seeded_task: Any, agent: Any) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "live-worker")
        db.get(RunRecord, run_id).state = RunState.AGENT_RUNNING
        db.commit()

        assert reclaim_expired_runs(db) == 0
        assert db.get(RunRecord, run_id).state == RunState.AGENT_RUNNING

    def test_completed_runs_are_never_reclaimed(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        run = runs_of(db, experiment.id)[0]
        run.state = RunState.COMPLETED
        run.outcome = RunOutcome.SOLVED
        run.claim_expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()

        assert reclaim_expired_runs(db) == 0
        assert db.get(RunRecord, run.id).state == RunState.COMPLETED

    def test_reclaiming_records_the_abandoned_attempt(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        from agentbench.trajectory.recorder import read_events

        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "dead-worker")
        run = db.get(RunRecord, run_id)
        run.state = RunState.AGENT_RUNNING
        run.claim_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        db.commit()

        reclaim_expired_runs(db)

        events = read_events(db, run_id)
        retry = [event for event in events if event.event_type == "retry"]
        assert retry and retry[-1].payload["scope"] == "lease_expiry"


class TestCancellation:
    def test_cancel_marks_pending_runs_cancelled(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=3)
        experiments.launch(db, experiment.id)

        experiments.cancel(db, experiment.id)

        rows = runs_of(db, experiment.id)
        assert all(row.state == RunState.CANCELLED for row in rows)
        assert all(row.outcome == RunOutcome.CANCELLED for row in rows)
        assert db.get(type(experiment), experiment.id).state == ExperimentState.CANCELLED

    def test_cancel_does_not_touch_already_completed_runs(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        rows = runs_of(db, experiment.id)
        rows[0].state = RunState.COMPLETED
        rows[0].outcome = RunOutcome.SOLVED
        db.flush()

        experiments.cancel(db, experiment.id)

        refreshed = runs_of(db, experiment.id)
        assert refreshed[0].outcome == RunOutcome.SOLVED
        assert refreshed[1].outcome == RunOutcome.CANCELLED

    def test_cancel_sets_the_flag_in_flight_runs_poll(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        experiments.cancel(db, experiment.id)
        assert db.get(type(experiment), experiment.id).cancel_requested is True

    def test_a_cancelled_experiment_cannot_be_relaunched(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        experiments.cancel(db, experiment.id)
        with pytest.raises(experiments.ExperimentError) as exc:
            experiments.launch(db, experiment.id)
        assert "cancelled" in str(exc.value)


class TestResume:
    def test_resume_requeues_an_abandoned_run(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "dead-worker")
        run = db.get(RunRecord, run_id)
        run.state = RunState.GRADING
        run.claim_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        db.commit()

        report = experiments.resume(db, experiment.id)

        assert db.get(RunRecord, run_id).state == RunState.PENDING
        assert report.skipped_live_lease == 0

    def test_resume_preserves_completed_results(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        rows = runs_of(db, experiment.id)
        rows[0].state = RunState.COMPLETED
        rows[0].outcome = RunOutcome.SOLVED
        db.commit()

        experiments.resume(db, experiment.id)

        assert db.get(RunRecord, rows[0].id).outcome == RunOutcome.SOLVED

    def test_resume_does_not_steal_a_run_with_a_live_lease(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "live-worker")
        db.get(RunRecord, run_id).state = RunState.AGENT_RUNNING
        db.commit()

        report = experiments.resume(db, experiment.id)

        assert db.get(RunRecord, run_id).state == RunState.AGENT_RUNNING
        assert report.skipped_live_lease == 1

    def test_forced_resume_reclaims_a_live_lease(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id)
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "live-worker")
        db.get(RunRecord, run_id).state = RunState.AGENT_RUNNING
        db.commit()

        report = experiments.resume(db, experiment.id, force=True)

        assert db.get(RunRecord, run_id).state == RunState.PENDING
        assert report.skipped_live_lease == 0

    def test_resume_materializes_runs_missing_after_a_repetition_increase(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=1)
        experiments.launch(db, experiment.id)
        experiment.repetitions = 3
        db.flush()

        experiments.resume(db, experiment.id)

        assert [row.attempt_index for row in runs_of(db, experiment.id)] == [0, 1, 2]


class TestProgressAndState:
    def test_progress_counts_each_bucket(self, db: Session, seeded_task: Any, agent: Any) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=4)
        experiments.launch(db, experiment.id)
        rows = runs_of(db, experiment.id)
        rows[0].state, rows[0].outcome = RunState.COMPLETED, RunOutcome.SOLVED
        rows[1].state, rows[1].outcome = RunState.COMPLETED, RunOutcome.UNSOLVED
        rows[2].state = RunState.AGENT_RUNNING
        db.flush()

        report = experiments.progress(db, experiment.id)

        assert report.total == 4
        assert report.completed == 2
        assert report.active == 1
        assert report.pending == 1
        assert report.by_outcome == {"solved": 1, "unsolved": 1}
        assert report.is_complete is False

    def test_state_becomes_completed_when_every_run_finishes(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        for row in runs_of(db, experiment.id):
            row.state, row.outcome = RunState.COMPLETED, RunOutcome.UNSOLVED
        db.flush()

        assert experiments.refresh_state(db, experiment.id) == ExperimentState.COMPLETED
        assert db.get(type(experiment), experiment.id).finished_at is not None

    def test_state_becomes_running_while_work_is_in_flight(
        self, db: Session, seeded_task: Any, agent: Any
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        runs_of(db, experiment.id)[0].state = RunState.AGENT_RUNNING
        db.flush()

        assert experiments.refresh_state(db, experiment.id) == ExperimentState.RUNNING


class TestWorkerDrain:
    def test_drain_executes_every_queued_run(
        self, db: Session, seeded_task: Any, agent: Any, local_sandbox_enabled: None
    ) -> None:
        experiment = make_experiment(db, seeded_task.key, agent.id, repetitions=2)
        experiments.launch(db, experiment.id)
        db.commit()

        reports = Worker(worker_id="drainer", concurrency=1).drain(max_seconds=120)

        assert len(reports) == 2
        assert all(report.outcome == RunOutcome.UNSOLVED for report in reports)
        db.expire_all()
        assert experiments.progress(db, experiment.id).is_complete

    def test_drain_is_a_no_op_when_the_queue_is_empty(self, db: Session) -> None:
        assert Worker(worker_id="drainer", concurrency=1).drain(max_seconds=5) == []
