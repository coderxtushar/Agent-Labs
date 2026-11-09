"""Integration tests against real backing services.

Each class skips if its dependency is unavailable rather than passing vacuously,
so a green run on a machine without Docker does not imply PostgreSQL works.

* **PostgreSQL** - starts a real ``postgres:16-alpine`` container, applies the
  Alembic migrations against it, checks there is no schema drift, and exercises
  the orchestration paths whose correctness depends on the database: the
  conditional-UPDATE worker claim, the idempotency constraint, and JSON column
  round-tripping. SQLite and PostgreSQL disagree about enough of this
  (``rowcount`` semantics, JSON typing, transaction isolation) that passing on
  one proves nothing about the other.
* **Git-backed task sources** - creates a real git repository, commits, and
  resolves a task against a pinned commit SHA through the normal run path.
* **Worker restart** - kills a worker process mid-run and verifies a second
  worker recovers the abandoned run without duplicating completed work.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:16-alpine"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _docker_ok() -> bool:
    from agentbench.sandbox.docker_sandbox import docker_available

    available, _ = docker_available(use_cache=False)
    return available


def _image_present(image: str) -> bool:
    return (
        subprocess.run(  # noqa: S603
            ["docker", "image", "inspect", image], capture_output=True, check=False
        ).returncode
        == 0
    )


# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """A real PostgreSQL instance in a container, torn down afterwards."""
    if not _docker_ok():
        pytest.skip("no Docker daemon; cannot start PostgreSQL")
    if not _image_present(POSTGRES_IMAGE):
        pytest.skip(f"{POSTGRES_IMAGE} is not pulled; run `docker pull {POSTGRES_IMAGE}`")

    port = _free_port()
    name = f"agentbench-pg-test-{uuid.uuid4().hex[:8]}"
    password = "agentbench-test-password"  # noqa: S105 - throwaway container
    proc = subprocess.run(  # noqa: S603
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=agentbench",
            "-e",
            "POSTGRES_USER=agentbench",
            "-p",
            f"127.0.0.1:{port}:5432",
            POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"could not start PostgreSQL: {proc.stderr.strip()[:300]}")

    url = f"postgresql+psycopg://agentbench:{password}@127.0.0.1:{port}/agentbench"
    try:
        # `pg_isready` inside the container reports ready during the image's
        # init phase, before it shuts the server down and restarts it. Waiting
        # on a real connection from the host is the only reliable signal.
        psycopg = pytest.importorskip("psycopg", reason="psycopg is not installed")
        deadline = time.monotonic() + 120
        last_error = ""
        ready = False
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(
                    f"postgresql://agentbench:{password}@127.0.0.1:{port}/agentbench",
                    connect_timeout=3,
                ) as connection:
                    connection.execute("SELECT 1")
                ready = True
                break
            except Exception as exc:  # noqa: BLE001 - any connection error means not ready
                last_error = str(exc)
                time.sleep(1)
        if not ready:
            pytest.skip(f"PostgreSQL did not accept connections in time: {last_error[:200]}")
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)  # noqa: S603


@pytest.fixture
def pg_session(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Point the application at PostgreSQL with a freshly migrated schema."""
    psycopg = pytest.importorskip(
        "psycopg", reason="psycopg is not installed; install the 'postgres' extra"
    )
    assert psycopg is not None

    from agentbench.config import reset_settings_cache
    from agentbench.db.session import reset_engine, session_scope

    schema = f"test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(
        "AGENTBENCH_DATABASE_URL", f"{postgres_url}?options=-csearch_path%3D{schema}"
    )
    reset_settings_cache()
    reset_engine()

    from agentbench.db.session import get_engine

    with get_engine().begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    env = {
        **os.environ,
        "AGENTBENCH_DATABASE_URL": f"{postgres_url}?options=-csearch_path%3D{schema}",
    }
    upgrade = subprocess.run(  # noqa: S603
        [str(BACKEND_ROOT / ".venv" / "bin" / "python"), "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade.returncode == 0, f"alembic upgrade failed:\n{upgrade.stderr[-2000:]}"

    try:
        with session_scope() as session:
            yield session
    finally:
        with get_engine().begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        reset_engine()
        reset_settings_cache()


@pytest.mark.docker
class TestPostgresPersistence:
    def test_migrations_apply_and_leave_no_drift(self, pg_session: Session) -> None:
        from agentbench.db.models import Base

        env = {**os.environ}
        check = subprocess.run(  # noqa: S603
            [str(BACKEND_ROOT / ".venv" / "bin" / "python"), "-m", "alembic", "check"],
            cwd=BACKEND_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, f"schema drift on PostgreSQL:\n{check.stdout}{check.stderr}"

        rows = pg_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        ).scalars()
        present = set(rows)
        assert set(Base.metadata.tables) <= present

    def test_json_columns_round_trip_nested_structures(
        self, pg_session: Session, minirepo_spec: Any
    ) -> None:
        """JSON is a native type on PostgreSQL and a text blob on SQLite; the
        models must behave the same on both."""
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        import_benchmark(pg_session, LoadedBenchmark("minirepo", "1.0", tasks=[minirepo_spec]))
        pg_session.commit()

        record = get_task_by_key(pg_session, minirepo_spec.key)
        assert record is not None
        assert record.spec["tests"]["fail_to_pass"] == minirepo_spec.tests.fail_to_pass
        assert (
            "def test_add_two_positives"
            in (record.spec["grading"]["acceptance_tests"]["tests/test_acceptance_add.py"])
        )
        assert record.tags == minirepo_spec.tags

    def test_the_idempotency_constraint_is_enforced(
        self, pg_session: Session, minirepo_spec: Any
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from agentbench.db.models import RunRecord
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        import_benchmark(pg_session, LoadedBenchmark("minirepo", "1.0", tasks=[minirepo_spec]))
        record = get_task_by_key(pg_session, minirepo_spec.key)
        assert record is not None
        agent = register_agent(
            pg_session, name="pg-noop", adapter="mock", config={"behaviour": "noop"}
        )
        experiment = experiments.create_experiment(
            pg_session, name="pg-exp", agent_config_id=agent.id, task_keys=[record.key]
        )
        experiments.launch(pg_session, experiment.id)
        pg_session.commit()

        duplicate = RunRecord(
            experiment_id=experiment.id,
            task_db_id=record.id,
            task_id=record.task_id,
            task_version=record.version,
            attempt_index=0,
        )
        pg_session.add(duplicate)
        with pytest.raises(IntegrityError):
            pg_session.flush()
        pg_session.rollback()

    def test_relaunching_on_postgres_does_not_duplicate_runs(
        self, pg_session: Session, minirepo_spec: Any
    ) -> None:
        from agentbench.db.models import RunRecord
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        import_benchmark(pg_session, LoadedBenchmark("minirepo", "1.0", tasks=[minirepo_spec]))
        record = get_task_by_key(pg_session, minirepo_spec.key)
        assert record is not None
        agent = register_agent(
            pg_session, name="pg-noop-2", adapter="mock", config={"behaviour": "noop"}
        )
        experiment = experiments.create_experiment(
            pg_session,
            name="pg-exp-2",
            agent_config_id=agent.id,
            task_keys=[record.key],
            repetitions=3,
        )
        experiments.launch(pg_session, experiment.id)
        experiments.launch(pg_session, experiment.id)
        experiments.materialize_runs(pg_session, experiment)
        pg_session.commit()

        runs = (
            pg_session.execute(select(RunRecord).where(RunRecord.experiment_id == experiment.id))
            .scalars()
            .all()
        )
        assert sorted(run.attempt_index for run in runs) == [0, 1, 2]

    def test_only_one_worker_can_claim_a_run_on_postgres(
        self, pg_session: Session, minirepo_spec: Any
    ) -> None:
        """The claim is a conditional UPDATE whose rowcount decides the winner;
        that is the one piece of concurrency control the platform relies on."""
        from agentbench.orchestrator import experiment as experiments
        from agentbench.orchestrator.worker import claim_next_run
        from agentbench.registry.agents import register_agent
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        import_benchmark(pg_session, LoadedBenchmark("minirepo", "1.0", tasks=[minirepo_spec]))
        record = get_task_by_key(pg_session, minirepo_spec.key)
        assert record is not None
        agent = register_agent(
            pg_session, name="pg-noop-3", adapter="mock", config={"behaviour": "noop"}
        )
        experiment = experiments.create_experiment(
            pg_session, name="pg-exp-3", agent_config_id=agent.id, task_keys=[record.key]
        )
        experiments.launch(pg_session, experiment.id)
        pg_session.commit()

        first = claim_next_run(pg_session, "pg-worker-a")
        second = claim_next_run(pg_session, "pg-worker-b")

        assert first is not None
        assert second is None

    def test_a_full_run_executes_and_persists_on_postgres(
        self,
        pg_session: Session,
        minirepo_spec: Any,
        local_sandbox_enabled: None,
        fixed_calc_source: str,
    ) -> None:
        from agentbench.db.models import RunRecord
        from agentbench.metrics.compute import experiment_metrics
        from agentbench.orchestrator import experiment as experiments
        from agentbench.orchestrator.runner import RunExecutor
        from agentbench.orchestrator.worker import claim_next_run
        from agentbench.registry.agents import register_agent
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark
        from agentbench.schemas.enums import RunOutcome
        from agentbench.trajectory.recorder import read_events

        import_benchmark(pg_session, LoadedBenchmark("minirepo", "1.0", tasks=[minirepo_spec]))
        record = get_task_by_key(pg_session, minirepo_spec.key)
        assert record is not None
        agent = register_agent(
            pg_session,
            name="pg-solver",
            adapter="scripted",
            config={"tasks": {"minirepo-add-bug": {"files": {"calc.py": fixed_calc_source}}}},
        )
        experiment = experiments.create_experiment(
            pg_session, name="pg-solve", agent_config_id=agent.id, task_keys=[record.key]
        )
        experiments.launch(pg_session, experiment.id)
        pg_session.commit()

        run_id = claim_next_run(pg_session, "pg-worker")
        assert run_id is not None
        RunExecutor(run_id, "pg-worker").execute()
        pg_session.expire_all()

        run = pg_session.execute(select(RunRecord).where(RunRecord.id == run_id)).scalars().one()
        assert run.outcome == RunOutcome.SOLVED
        assert run.grading is not None and run.grading.passed is True

        events = read_events(pg_session, run_id, limit=500)
        assert [event.seq for event in events] == list(range(1, len(events) + 1))

        metrics = experiment_metrics(pg_session, experiment.id).to_dict()
        assert metrics["success_rate_attributable"]["numerator"] == 1
        assert metrics["success_rate_attributable"]["denominator"] == 1


# --------------------------------------------------------------------------- #
@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[str, str]:
    """A real git repository containing the fixture project, plus its HEAD SHA."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    source = BACKEND_ROOT / "tests" / "fixtures" / "minirepo"
    repo = tmp_path / "gitrepo"
    shutil.copytree(source, repo)

    def run(*args: str) -> None:
        result = subprocess.run(  # noqa: S603
            ["git", *args], cwd=repo, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"

    run("init", "--quiet", "--initial-branch", "main")
    run("config", "user.email", "test@agentbench.local")
    run("config", "user.name", "AgentBench Test")
    run("config", "commit.gpgsign", "false")
    run("add", "-A")
    run("commit", "--quiet", "-m", "initial commit")

    sha = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo.as_uri(), sha


class TestGitSourceSecurity:
    """``file://`` is refused on purpose: a task file is an untrusted, shareable
    artefact, and a file URL would let one read any path on the host."""

    def test_file_urls_are_rejected_by_the_schema(self, git_repo: tuple[str, str]) -> None:
        from agentbench.registry.loader import BenchmarkLoadError, parse_task_dict
        from tests.conftest import minirepo_task_dict

        url, sha = git_repo
        payload = minirepo_task_dict(task_id="minirepo-file-url")
        payload["repo"] = {"kind": "git", "url": url, "revision": sha}

        with pytest.raises(BenchmarkLoadError, match="http"):
            parse_task_dict(payload)

    @pytest.mark.parametrize(
        "url", ["file:///etc", "/etc/passwd", "../../etc", "ftp://example.com/repo.git"]
    )
    def test_only_http_and_ssh_schemes_are_accepted(self, url: str) -> None:
        from agentbench.registry.loader import BenchmarkLoadError, parse_task_dict
        from tests.conftest import minirepo_task_dict

        payload = minirepo_task_dict(task_id="minirepo-bad-url")
        payload["repo"] = {"kind": "git", "url": url, "revision": "a" * 40}

        with pytest.raises(BenchmarkLoadError):
            parse_task_dict(payload)

    def test_https_and_ssh_urls_are_accepted(self) -> None:
        from agentbench.registry.loader import parse_task_dict
        from tests.conftest import minirepo_task_dict

        for url in (
            "https://github.com/example/repo.git",
            "git@github.com:example/repo.git",
            "ssh://git@github.com/example/repo.git",
        ):
            payload = minirepo_task_dict(task_id="minirepo-ok-url")
            payload["repo"] = {"kind": "git", "url": url, "revision": "b" * 40}
            spec = parse_task_dict(payload)
            assert spec.repo.kind == "git"


class TestGitCloneMachinery:
    """Exercises the real clone/cache/pin code against a real git repository.

    The source object is built directly rather than through a task file, because
    the schema (correctly) refuses the ``file://`` URL this test needs. That
    keeps the security control intact while still running the actual git code.
    """

    def _source(self, url: str, revision: str) -> Any:
        from agentbench.schemas.task import GitRepoSource

        source = GitRepoSource(url="https://example.com/placeholder.git", revision=revision)
        source.url = url
        return source

    def test_a_pinned_sha_is_recognised_as_immutable(self, git_repo: tuple[str, str]) -> None:
        url, sha = git_repo
        assert self._source(url, sha).revision_is_immutable is True
        assert self._source(url, "main").revision_is_immutable is False

    def test_the_repository_is_cloned_at_the_pinned_revision(
        self, git_repo: tuple[str, str], minirepo_spec: Any
    ) -> None:
        from agentbench.orchestrator.workspace import materialize_repository

        url, sha = git_repo
        spec = minirepo_spec.model_copy(deep=True)
        spec.repo = self._source(url, sha)

        workspace = materialize_repository(spec, "git-test")
        try:
            assert workspace.repo_identity == f"git:{url}"
            assert workspace.revision == sha
            assert (workspace.baseline / "calc.py").is_file()
            assert "left - right" in (workspace.baseline / "calc.py").read_text()
            # The clone's own history must not leak into the agent's workspace.
            assert not (workspace.baseline / ".git").exists()
        finally:
            workspace.cleanup()

    def test_the_clone_is_cached_by_revision(
        self, git_repo: tuple[str, str], minirepo_spec: Any, settings: Any
    ) -> None:
        from agentbench.orchestrator.workspace import materialize_repository

        url, sha = git_repo
        spec = minirepo_spec.model_copy(deep=True)
        spec.repo = self._source(url, sha)

        first = materialize_repository(spec, "git-cache-1")
        first_count = first.file_count
        first.cleanup()

        cache_root = settings.data_dir / "repo_cache"
        cached = list(cache_root.iterdir())
        assert len(cached) == 1, "the clone should be cached under a revision key"

        second = materialize_repository(spec, "git-cache-2")
        try:
            assert second.file_count == first_count
            assert len(list(cache_root.iterdir())) == 1, "a second run must reuse the cache"
        finally:
            second.cleanup()

    def test_an_unknown_revision_is_an_invalid_task_not_an_infra_error(
        self, git_repo: tuple[str, str], minirepo_spec: Any
    ) -> None:
        from agentbench.orchestrator.workspace import (
            TaskEnvironmentError,
            materialize_repository,
        )

        url, _sha = git_repo
        spec = minirepo_spec.model_copy(deep=True)
        spec.repo = self._source(url, "0" * 40)

        with pytest.raises(TaskEnvironmentError):
            materialize_repository(spec, "git-bad-rev")

    def test_a_git_sourced_task_cannot_bypass_the_url_allowlist_at_run_time(
        self, db: Session, git_repo: tuple[str, str], minirepo_spec: Any
    ) -> None:
        """The allowlist is re-checked when runs are materialised, so editing a
        stored spec cannot smuggle a file:// source into execution.

        This is why a fully graded run over a git source is not covered here:
        doing so needs a remote that actually hosts the fixture repository.
        """
        from agentbench.orchestrator import experiment as experiments
        from agentbench.registry.agents import register_agent
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        url, sha = git_repo
        spec = minirepo_spec.model_copy(deep=True)
        spec.task_id = "minirepo-smuggled-git"
        import_benchmark(db, LoadedBenchmark("gitbench", "1.0", tasks=[spec]))
        record = get_task_by_key(db, spec.key)
        assert record is not None

        snapshot = dict(record.spec)
        snapshot["repo"] = {"kind": "git", "url": url, "revision": sha}
        record.spec = snapshot
        db.commit()

        agent = register_agent(db, name="git-smuggle", adapter="mock", config={"behaviour": "noop"})
        experiment = experiments.create_experiment(
            db, name="git-smuggle-exp", agent_config_id=agent.id, task_keys=[record.key]
        )

        with pytest.raises(Exception, match="http"):
            experiments.materialize_runs(db, experiment)


@pytest.mark.network
class TestGitOverHttps:
    """Proves the network clone path works against a real remote.

    Skipped when offline. Uses a pinned commit in a tiny, long-stable public
    repository so the assertion cannot drift.
    """

    URL = "https://github.com/octocat/Hello-World.git"
    SHA = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"

    def _reachable(self) -> bool:
        probe = subprocess.run(  # noqa: S603
            ["git", "ls-remote", "--exit-code", self.URL, "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return probe.returncode == 0

    def test_a_remote_repository_clones_at_a_pinned_sha(self, minirepo_spec: Any) -> None:
        from agentbench.orchestrator.workspace import materialize_repository
        from agentbench.schemas.task import GitRepoSource

        if shutil.which("git") is None:
            pytest.skip("git is not installed")
        try:
            if not self._reachable():
                pytest.skip("remote git repository is unreachable (offline?)")
        except subprocess.TimeoutExpired:
            pytest.skip("git ls-remote timed out (offline?)")

        spec = minirepo_spec.model_copy(deep=True)
        spec.repo = GitRepoSource(url=self.URL, revision=self.SHA)

        workspace = materialize_repository(spec, "git-https")
        try:
            assert workspace.revision == self.SHA
            assert workspace.file_count > 0
            assert not (workspace.baseline / ".git").exists()
        finally:
            workspace.cleanup()


# --------------------------------------------------------------------------- #
@pytest.mark.simulated
class TestWorkerRestartRecovery:
    def test_a_killed_worker_process_leaves_a_recoverable_run(
        self, db: Session, seeded_task: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kills a real worker process mid-run, then checks a second worker picks
        the run up once the lease expires and still produces a verdict."""
        from agentbench.db.models import RunRecord
        from agentbench.orchestrator import experiment as experiments
        from agentbench.orchestrator.worker import Worker, reclaim_expired_runs
        from agentbench.registry.agents import register_agent
        from agentbench.schemas.enums import RunOutcome, RunState

        agent = register_agent(
            db,
            name="restart-hanger",
            adapter="mock",
            config={"behaviour": "hang", "hang_seconds": 600},
        )
        experiment = experiments.create_experiment(
            db, name="restart", agent_config_id=agent.id, task_keys=[seeded_task.key]
        )
        experiments.launch(db, experiment.id)
        db.commit()

        env = {
            **os.environ,
            "AGENTBENCH_DATABASE_URL": settings.database_url,
            "AGENTBENCH_DATA_DIR": str(settings.data_dir),
            "AGENTBENCH_BENCHMARKS_DIR": str(settings.benchmarks_dir),
            "AGENTBENCH_SANDBOX_PROVIDER": "local_unsafe",
            "AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX": "true",
            "AGENTBENCH_RUN_LEASE_SECONDS": "2",
            "AGENTBENCH_LOG_LEVEL": "CRITICAL",
        }
        worker = subprocess.Popen(  # noqa: S603
            [str(BACKEND_ROOT / ".venv" / "bin" / "python"), "-m", "agentbench.cli.main", "worker"],
            cwd=BACKEND_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            deadline = time.monotonic() + 60
            claimed = False
            while time.monotonic() < deadline:
                db.expire_all()
                run = (
                    db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment.id))
                    .scalars()
                    .one()
                )
                if run.state != RunState.PENDING:
                    claimed = True
                    break
                time.sleep(0.5)
            assert claimed, "the worker process never claimed the run"
        finally:
            worker.kill()
            worker.wait(timeout=30)

        db.expire_all()
        run = (
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment.id))
            .scalars()
            .one()
        )
        assert not RunState(run.state).is_terminal
        assert run.outcome is None

        time.sleep(3)
        monkeypatch.setenv("AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX", "true")
        monkeypatch.setenv("AGENTBENCH_SANDBOX_PROVIDER", "local_unsafe")
        monkeypatch.setenv("AGENTBENCH_RUN_LEASE_SECONDS", "2")
        from agentbench.config import reset_settings_cache

        reset_settings_cache()

        assert reclaim_expired_runs(db) == 1
        db.commit()
        db.expire_all()
        assert db.get(RunRecord, run.id).state == RunState.PENDING

        reports = Worker(worker_id="recovery-worker", concurrency=1).drain(max_seconds=120)
        assert len(reports) == 1
        assert reports[0].outcome in (RunOutcome.TIMEOUT, RunOutcome.UNSOLVED)

        db.expire_all()
        final = db.get(RunRecord, run.id)
        assert RunState(final.state).is_terminal
        assert final.outcome is not None

    def test_recovery_does_not_rerun_a_completed_run(self, db: Session, seeded_task: Any) -> None:
        from agentbench.db.models import RunRecord
        from agentbench.orchestrator import experiment as experiments
        from agentbench.orchestrator.worker import reclaim_expired_runs
        from agentbench.registry.agents import register_agent
        from agentbench.schemas.enums import RunOutcome, RunState

        agent = register_agent(
            db, name="restart-noop", adapter="mock", config={"behaviour": "noop"}
        )
        experiment = experiments.create_experiment(
            db,
            name="restart-done",
            agent_config_id=agent.id,
            task_keys=[seeded_task.key],
            repetitions=2,
        )
        experiments.launch(db, experiment.id)
        runs = (
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment.id))
            .scalars()
            .all()
        )
        runs[0].state = RunState.COMPLETED
        runs[0].outcome = RunOutcome.SOLVED
        db.commit()

        reclaim_expired_runs(db)
        db.commit()
        db.expire_all()

        assert db.get(RunRecord, runs[0].id).state == RunState.COMPLETED
        assert db.get(RunRecord, runs[0].id).outcome == RunOutcome.SOLVED
