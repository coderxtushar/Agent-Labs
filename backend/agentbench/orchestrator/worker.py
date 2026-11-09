"""Run worker.

A worker claims ``PENDING`` runs, executes them, and updates experiment state.
Durability comes from the database, not from worker memory:

* **Claiming** sets ``claimed_by`` and a ``claim_expires_at`` lease inside a
  transaction, and only succeeds for a row still in ``PENDING``.  Two workers
  racing for the same run cannot both win.
* **Crash recovery** happens at startup and periodically: runs whose lease has
  expired return to ``PENDING``, and orphaned containers carrying the platform's
  label are removed.
* **Bounded concurrency** is enforced by a fixed-size thread pool sized by
  ``AGENTBENCH_MAX_CONCURRENT_RUNS``.

Redis and an external queue are deliberately absent.  The lease table already
provides at-most-once execution and crash recovery for a single-host
deployment; swapping in a broker only becomes worthwhile for multi-host
scheduling, and :func:`claim_next_run` is the seam for it.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentbench.config import get_settings
from agentbench.db.models import ExperimentRecord, RunRecord
from agentbench.db.session import session_scope
from agentbench.logging_setup import get_logger
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.runner import RunExecutor, RunReport
from agentbench.schemas.enums import EventType, ExperimentState, RunOutcome, RunState
from agentbench.trajectory.recorder import EventRecorder

log = get_logger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------- #
def reclaim_expired_runs(session: Session) -> int:
    """Return lease-expired runs to ``PENDING``.

    Called at worker startup so a hard crash does not leave runs stuck in an
    active state forever.  The abandoned attempt is recorded in the event
    stream rather than deleted.
    """
    now = datetime.now(UTC)
    rows = session.execute(
        select(RunRecord).where(
            RunRecord.state.notin_([RunState.PENDING, RunState.COMPLETED, RunState.CANCELLED])
        )
    ).scalars()

    reclaimed = 0
    for run in rows:
        expires = run.claim_expires_at
        if (
            expires is not None
            and (expires if expires.tzinfo else expires.replace(tzinfo=UTC)) > now
        ):
            continue
        EventRecorder(session, run.id).record(
            EventType.RETRY,
            {
                "scope": "lease_expiry",
                "previous_state": run.state,
                "previous_worker": run.claimed_by,
                "reason": "worker lease expired; the run is reclaimable",
            },
        )
        run.state = RunState.PENDING
        run.claimed_by = None
        run.claimed_at = None
        run.claim_expires_at = None
        run.sandbox_id = None
        reclaimed += 1

    session.flush()
    if reclaimed:
        log.warning("worker.reclaimed_expired_runs", extra={"count": reclaimed})
    return reclaimed


def claim_next_run(session: Session, worker_id: str) -> str | None:
    """Atomically claim one pending run belonging to a runnable experiment.

    The conditional ``UPDATE ... WHERE state = 'pending'`` is what makes this
    safe: the row is only claimed if no other worker claimed it first.
    """
    settings = get_settings()
    candidates = session.execute(
        select(RunRecord.id)
        .join(ExperimentRecord, ExperimentRecord.id == RunRecord.experiment_id)
        .where(
            RunRecord.state == RunState.PENDING,
            ExperimentRecord.cancel_requested.is_(False),
            ExperimentRecord.state.in_([ExperimentState.QUEUED, ExperimentState.RUNNING]),
        )
        .order_by(RunRecord.created_at.asc(), RunRecord.attempt_index.asc())
        .limit(20)
    ).scalars()

    now = datetime.now(UTC)
    expires = now + timedelta(seconds=settings.run_lease_seconds)

    for run_id in candidates:
        claimed = session.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id, RunRecord.state == RunState.PENDING)
            .values(
                state=RunState.CLAIMED,
                claimed_by=worker_id,
                claimed_at=now,
                claim_expires_at=expires,
                heartbeat_at=now,
            )
        )
        # A zero row count means another worker won the race for this row.
        if getattr(claimed, "rowcount", 0):
            session.commit()
            return run_id
        session.rollback()
    return None


def release_claim(session: Session, run_id: str, reason: str) -> None:
    run = session.get(RunRecord, run_id)
    if run is None or RunState(run.state).is_terminal:
        return
    EventRecorder(session, run.id).record(
        EventType.RETRY, {"scope": "claim_released", "reason": reason}
    )
    run.state = RunState.PENDING
    run.claimed_by = None
    run.claimed_at = None
    run.claim_expires_at = None
    session.flush()


# --------------------------------------------------------------------------- #
class Worker:
    """Polls for pending runs and executes them with bounded concurrency."""

    def __init__(self, worker_id: str | None = None, concurrency: int | None = None) -> None:
        settings = get_settings()
        self.worker_id = worker_id or default_worker_id()
        self.concurrency = max(
            1, min(concurrency or settings.max_concurrent_runs, settings.max_concurrent_runs)
        )
        self._settings = settings
        self._stop = threading.Event()
        self._pool: ThreadPoolExecutor | None = None
        self._inflight: set[Future[RunReport]] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    def startup_recovery(self) -> dict[str, int]:
        """Clean up after a previous crash before taking new work."""
        with session_scope() as session:
            reclaimed = reclaim_expired_runs(session)

        orphans = 0
        if self._settings.sandbox_provider == "docker":
            from agentbench.sandbox.docker_sandbox import cleanup_orphans

            orphans = len(cleanup_orphans())
        log.info(
            "worker.startup_recovery",
            extra={"worker_id": self.worker_id, "reclaimed": reclaimed, "orphans": orphans},
        )
        return {"reclaimed_runs": reclaimed, "removed_orphan_sandboxes": orphans}

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.startup_recovery()
        self._pool = ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="agentbench-run"
        )
        self._thread = threading.Thread(target=self._loop, name="agentbench-worker", daemon=True)
        self._thread.start()
        log.info(
            "worker.started", extra={"worker_id": self.worker_id, "concurrency": self.concurrency}
        )

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
        self._started = False
        log.info("worker.stopped", extra={"worker_id": self.worker_id})

    @property
    def running(self) -> bool:
        return self._started and not self._stop.is_set()

    @property
    def active_run_count(self) -> int:
        with self._lock:
            return len(self._inflight)

    # -- loop --------------------------------------------------------------
    def _loop(self) -> None:
        interval = self._settings.worker_poll_interval_s
        while not self._stop.is_set():
            try:
                dispatched = self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                log.exception("worker.poll_failed", extra={"error": str(exc)})
                dispatched = 0
            self._stop.wait(interval if dispatched == 0 else 0.05)

    def poll_once(self) -> int:
        """Claim and dispatch as many runs as free capacity allows."""
        assert self._pool is not None
        dispatched = 0
        while True:
            with self._lock:
                self._inflight = {f for f in self._inflight if not f.done()}
                free = self.concurrency - len(self._inflight)
            if free <= 0:
                break
            with session_scope() as session:
                run_id = claim_next_run(session, self.worker_id)
            if run_id is None:
                break
            future = self._pool.submit(self._execute, run_id)
            with self._lock:
                self._inflight.add(future)
            dispatched += 1
        self._refresh_experiments()
        return dispatched

    def _execute(self, run_id: str) -> RunReport:
        try:
            report = RunExecutor(run_id, self.worker_id).execute()
        except Exception as exc:  # noqa: BLE001
            log.exception("worker.run_crashed", extra={"run_id": run_id, "error": str(exc)})
            with session_scope() as session:
                run = session.get(RunRecord, run_id)
                if run is not None and not RunState(run.state).is_terminal:
                    EventRecorder(session, run_id).error(
                        "worker thread crashed while executing the run", detail=str(exc)
                    )
                    run.state = RunState.COMPLETED
                    run.outcome = RunOutcome.INFRA_ERROR
                    run.termination_reason = f"worker crashed: {exc}"[:4000]
                    run.finished_at = datetime.now(UTC)
                    run.claimed_by = None
                    run.claim_expires_at = None
            return RunReport(run_id, RunOutcome.INFRA_ERROR, RunState.COMPLETED, str(exc))
        finally:
            self._refresh_experiments()
        return report

    def _refresh_experiments(self) -> None:
        try:
            with session_scope() as session:
                for experiment_id in experiments.runnable_experiment_ids(session):
                    experiments.refresh_state(session, experiment_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.refresh_failed", extra={"error": str(exc)})

    # -- synchronous helper for tests and the CLI --------------------------
    def drain(self, max_seconds: float = 600.0, poll_interval: float = 0.2) -> list[RunReport]:
        """Execute every available run to completion, in this thread.

        Used by ``agentbench run`` and by tests: no background thread, so
        failures surface directly and ordering is deterministic.
        """
        reports: list[RunReport] = []
        deadline = time.monotonic() + max_seconds
        self.startup_recovery()
        while time.monotonic() < deadline:
            with session_scope() as session:
                run_id = claim_next_run(session, self.worker_id)
            if run_id is None:
                break
            reports.append(RunExecutor(run_id, self.worker_id).execute())
            self._refresh_experiments()
            time.sleep(poll_interval if poll_interval < 0.05 else 0.0)
        return reports


_worker: Worker | None = None


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker


def shutdown_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
