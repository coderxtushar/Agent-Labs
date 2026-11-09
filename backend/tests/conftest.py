"""Shared test fixtures.

Two execution modes are used by the suite:

* Most integration tests run through the ``local_unsafe`` provider against the
  self-contained fixture repository in ``tests/fixtures/minirepo``.  That keeps
  the suite fast and runnable without a Docker daemon, which matters for CI.
  It is enabled explicitly per fixture, never globally.
* Tests marked ``docker`` exercise the real container provider and are skipped
  when no daemon is reachable.

Tests that use the mock or scripted adapter are marked ``simulated``: they prove
the harness behaves correctly, and must never be read as model benchmark
results.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point every test at its own database, data dir and benchmarks root."""
    from agentbench.config import reset_settings_cache
    from agentbench.db.session import reset_engine

    for key in list(os.environ):
        if key.startswith("AGENTBENCH_"):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("AGENTBENCH_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("AGENTBENCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENTBENCH_BENCHMARKS_DIR", str(FIXTURES))
    monkeypatch.setenv("AGENTBENCH_LOG_LEVEL", "CRITICAL")
    monkeypatch.setenv("AGENTBENCH_LOG_FORMAT", "console")
    monkeypatch.setenv("AGENTBENCH_INFRA_RETRY_LIMIT", "1")
    monkeypatch.setenv("AGENTBENCH_MAX_CONCURRENT_RUNS", "2")

    reset_settings_cache()
    reset_engine()
    yield
    reset_settings_cache()
    reset_engine()


@pytest.fixture
def settings() -> Any:
    from agentbench.config import get_settings

    return get_settings()


@pytest.fixture
def db(settings: Any) -> Iterator[Session]:
    from agentbench.db.session import create_all, session_scope

    create_all()
    with session_scope() as session:
        yield session


@pytest.fixture
def local_sandbox_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt in to the deliberately unsafe host-local provider for this test."""
    from agentbench.config import reset_settings_cache

    monkeypatch.setenv("AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX", "true")
    monkeypatch.setenv("AGENTBENCH_SANDBOX_PROVIDER", "local_unsafe")
    reset_settings_cache()


# --------------------------------------------------------------------------- #
def minirepo_task_dict(*, interpreter: str | None = None, **overrides: Any) -> dict[str, Any]:
    """Task spec for the fixture repository.

    ``interpreter`` defaults to ``sys.executable`` so the host-local provider
    finds the interpreter that actually has pytest installed. Docker-marked
    tests pass ``"python"``, which resolves inside the benchmark image.
    """
    interpreter = interpreter or shlex.quote(sys.executable)
    spec: dict[str, Any] = {
        "task_id": "minirepo-add-bug",
        "version": "1.0",
        "title": "add() subtracts instead of adding",
        "problem_statement": (
            "calc.add returns left - right. Make it return the sum without changing mul."
        ),
        "repo": {"kind": "local_path", "path": "minirepo", "revision": "local"},
        "category": "bug_fix",
        "difficulty": "easy",
        "environment": {
            "image": "agentbench/benchmark-python:3.12",
            "setup_commands": [],
            "workdir": "/workspace/repo",
        },
        "tests": {
            "runner": "pytest",
            "command": (f"{interpreter} -m pytest -p no:cacheprovider -q --tb=short {{selection}}"),
            "fail_to_pass": [
                "tests/test_acceptance_add.py::test_add_two_positives",
                "tests/test_acceptance_add.py::test_add_negatives",
            ],
            "pass_to_pass": [
                "tests/test_calc.py::test_add_identity_still_works",
                "tests/test_calc.py::test_mul",
            ],
        },
        "grading": {
            "version": "1.0",
            "acceptance_tests": {
                "tests/test_acceptance_add.py": {"$file": "acceptance/calc_add.py"}
            },
            "verify_baseline": True,
            "forbidden_paths": ["tests/**"],
        },
        "limits": {
            "agent_timeout_s": 60,
            "setup_timeout_s": 60,
            "test_timeout_s": 120,
            "memory_mb": 512,
            "cpus": 1.0,
            "network": "none",
            "max_agent_steps": 10,
        },
        "context_files": ["calc.py"],
        "acceptance_criteria": ["add(2, 3) == 5", "mul is unchanged"],
        "hints": ["Change the minus sign to a plus sign."],
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def minirepo_spec() -> Any:
    from agentbench.registry.loader import parse_task_dict

    return parse_task_dict(minirepo_task_dict())


@pytest.fixture
def seeded_task(db: Session, minirepo_spec: Any) -> Any:
    from agentbench.registry.loader import LoadedBenchmark
    from agentbench.registry.service import import_benchmark

    import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[minirepo_spec]))
    from agentbench.registry.service import get_task_by_key

    record = get_task_by_key(db, minirepo_spec.key)
    assert record is not None
    # Committed so code running on a different session (the API, a worker) can
    # see the seeded row rather than reading an empty database.
    db.commit()
    return record


@pytest.fixture
def seeded_docker_task(db: Session) -> Any:
    """The fixture task with an in-image interpreter, for Docker-marked tests."""
    from agentbench.registry.loader import LoadedBenchmark, parse_task_dict
    from agentbench.registry.service import get_task_by_key, import_benchmark

    spec = parse_task_dict(
        minirepo_task_dict(task_id="minirepo-add-bug-docker", interpreter="python")
    )
    import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[spec]))
    record = get_task_by_key(db, spec.key)
    assert record is not None
    db.commit()
    return record


@pytest.fixture
def seeded_agent(db: Session) -> Any:
    from agentbench.registry.agents import register_agent

    record = register_agent(
        db,
        name="mock-noop",
        adapter="mock",
        description="test double",
        config={"behaviour": "noop"},
    )
    db.commit()
    return record


@pytest.fixture
def seeded_experiment(db: Session, seeded_task: Any, seeded_agent: Any) -> Any:
    from agentbench.orchestrator import experiment as experiments

    record = experiments.create_experiment(
        db,
        name="test-experiment",
        agent_config_id=seeded_agent.id,
        task_keys=[seeded_task.key],
    )
    db.commit()
    return record


@pytest.fixture
def seeded_run(db: Session, seeded_experiment: Any) -> Any:
    """A real, fully-linked run row. Foreign keys are enforced, so tests cannot
    fabricate orphan runs."""
    from sqlalchemy import select

    from agentbench.db.models import RunRecord
    from agentbench.orchestrator import experiment as experiments

    experiments.materialize_runs(db, seeded_experiment)
    run = (
        db.execute(select(RunRecord).where(RunRecord.experiment_id == seeded_experiment.id))
        .scalars()
        .first()
    )
    assert run is not None
    db.commit()
    return run


@pytest.fixture
def fixed_calc_source() -> str:
    return (FIXTURES / "solutions" / "calc_fixed.py").read_text(encoding="utf-8")


@pytest.fixture
def docker_available() -> bool:
    from agentbench.sandbox.docker_sandbox import docker_available as probe

    available, _ = probe()
    return available


@pytest.fixture
def api_client(db: Session) -> Iterator[Any]:
    """FastAPI test client with the background worker disabled."""
    from fastapi.testclient import TestClient

    from agentbench.api.main import app

    os.environ["AGENTBENCH_WORKER_IN_API_PROCESS"] = "false"
    from agentbench.config import reset_settings_cache

    reset_settings_cache()
    with TestClient(app) as client:
        yield client
    os.environ.pop("AGENTBENCH_WORKER_IN_API_PROCESS", None)
    reset_settings_cache()
