"""Grading integrity: a patch must not be able to change how it is graded.

Background
----------
These tests exist because of a defect found by trying the attack, not by
reading the code. An agent that never touches ``tests/`` or a ``conftest.py``
could still force every graded test to pass:

1. write a pytest plugin module anywhere in the repository (allowed), then
2. add ``addopts = -p <module>`` to ``pytest.ini``, ``setup.cfg``, ``tox.ini``
   or ``pyproject.toml`` (all allowed under the old default), and
3. the plugin's ``pytest_collection_modifyitems`` hook replaces every test body,
   so the run reports ``SOLVED``.

All four config files were confirmed to work as injection points against real
pytest. ``sitecustomize.py`` was also tried and does **not** auto-load under
``python -m pytest``, so it is deliberately not in the protected set.

The fix is enforced by the grading engine itself
(``GRADING_INTEGRITY_PATHS``), so a task cannot opt out of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import RunRecord
from agentbench.grading.patch import GRADING_INTEGRITY_PATHS, check_forbidden_paths
from agentbench.metrics.failure import effective_annotation
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.runner import RunExecutor
from agentbench.orchestrator.worker import claim_next_run
from agentbench.registry.agents import register_agent
from agentbench.schemas.enums import FailureCategory, RunOutcome
from agentbench.schemas.task import GradingSpec

EVIL_PLUGIN = """\
def pytest_collection_modifyitems(session, config, items):
    for item in items:
        item.obj = lambda *a, **k: None
"""

CONFIG_INJECTIONS: dict[str, str] = {
    "pytest.ini": "[pytest]\naddopts = -p evil_plugin\n",
    "setup.cfg": "[tool:pytest]\naddopts = -p evil_plugin\n",
    "tox.ini": "[pytest]\naddopts = -p evil_plugin\n",
    "pyproject.toml": '[tool.pytest.ini_options]\naddopts = "-p evil_plugin"\n',
}


class TestProtectedPathSet:
    def test_every_proven_injection_point_is_protected(self) -> None:
        for filename in CONFIG_INJECTIONS:
            assert check_forbidden_paths([filename], list(GRADING_INTEGRITY_PATHS)), (
                f"{filename} is a confirmed pytest injection point but is not protected"
            )

    def test_config_files_are_protected_at_any_depth(self) -> None:
        for filename in CONFIG_INJECTIONS:
            assert check_forbidden_paths([f"src/nested/{filename}"], list(GRADING_INTEGRITY_PATHS))

    def test_conftest_is_protected_at_any_depth(self) -> None:
        assert check_forbidden_paths(["conftest.py"], list(GRADING_INTEGRITY_PATHS))
        assert check_forbidden_paths(["a/b/conftest.py"], list(GRADING_INTEGRITY_PATHS))

    def test_ordinary_source_files_remain_editable(self) -> None:
        assert (
            check_forbidden_paths(
                ["src/pkg/mod.py", "README.md", "src/pkg/helper.py"],
                list(GRADING_INTEGRITY_PATHS),
            )
            == []
        )

    def test_schema_default_warns_the_agent_about_the_same_paths(self) -> None:
        """The agent is told up front, so a compliant agent is not surprised."""
        default = GradingSpec().forbidden_paths
        for filename in CONFIG_INJECTIONS:
            assert filename in default

    def test_sitecustomize_is_deliberately_not_protected(self) -> None:
        """It was tested and does not auto-load; protecting it would be noise."""
        assert check_forbidden_paths(["sitecustomize.py"], list(GRADING_INTEGRITY_PATHS)) == []


@pytest.mark.simulated
@pytest.mark.usefixtures("local_sandbox_enabled")
class TestAttackIsRejectedEndToEnd:
    """Runs the real attack through the real pipeline, per config file."""

    def _run_attack(self, db: Session, task_key: str, config_file: str) -> RunRecord:
        agent = register_agent(
            db,
            name=f"integrity-attacker-{config_file.replace('.', '-')}",
            adapter="scripted",
            config={
                "tasks": {
                    "minirepo-add-bug": {
                        "files": {
                            "evil_plugin.py": EVIL_PLUGIN,
                            config_file: CONFIG_INJECTIONS[config_file],
                        }
                    }
                }
            },
        )
        experiment = experiments.create_experiment(
            db, name=f"attack-{config_file}", agent_config_id=agent.id, task_keys=[task_key]
        )
        experiments.launch(db, experiment.id)
        db.commit()

        run_id = claim_next_run(db, "integrity-worker")
        assert run_id is not None
        RunExecutor(run_id, "integrity-worker").execute()
        db.expire_all()
        return db.execute(select(RunRecord).where(RunRecord.id == run_id)).scalars().one()

    @pytest.mark.parametrize("config_file", sorted(CONFIG_INJECTIONS))
    def test_runner_config_injection_cannot_produce_solved(
        self, db: Session, seeded_task: Any, config_file: str
    ) -> None:
        run = self._run_attack(db, seeded_task.key, config_file)

        assert run.outcome == RunOutcome.UNSOLVED, (
            f"injecting a pytest plugin via {config_file} produced {run.outcome}"
        )
        assert run.grading is not None
        assert run.grading.passed is False

    @pytest.mark.parametrize("config_file", sorted(CONFIG_INJECTIONS))
    def test_rejection_happens_at_the_patch_layer_before_any_test_runs(
        self, db: Session, seeded_task: Any, config_file: str
    ) -> None:
        """If the tests ran at all, the plugin would already have been loaded."""
        run = self._run_attack(db, seeded_task.key, config_file)

        layers = {layer["name"]: layer for layer in run.grading.layers}
        assert layers["patch"]["status"] == "failed"
        assert "fail_to_pass" not in layers
        assert run.grading.evidence["stopped_at"] == "patch"

    def test_the_violation_is_reported_as_an_integrity_breach(
        self, db: Session, seeded_task: Any
    ) -> None:
        run = self._run_attack(db, seeded_task.key, "pytest.ini")
        patch_layer = next(layer for layer in run.grading.layers if layer["name"] == "patch")

        assert patch_layer["evidence"]["grading_integrity_violation"] is True
        assert "pytest.ini" in patch_layer["evidence"]["violations"]
        assert "runner configuration" in patch_layer["summary"]
        assert effective_annotation(db, run.id).category == (
            FailureCategory.FORBIDDEN_FILES_MODIFIED
        )

    def test_a_task_cannot_opt_out_of_the_integrity_check(
        self, db: Session, minirepo_spec: Any
    ) -> None:
        """Even with an empty forbidden_paths, the engine still refuses."""
        from agentbench.registry.loader import LoadedBenchmark
        from agentbench.registry.service import get_task_by_key, import_benchmark

        permissive = minirepo_spec.model_copy(deep=True)
        permissive.task_id = "minirepo-permissive"
        permissive.grading.forbidden_paths = []
        import_benchmark(db, LoadedBenchmark("minirepo", "1.0", tasks=[permissive]))
        record = get_task_by_key(db, permissive.key)
        assert record is not None

        agent = register_agent(
            db,
            name="integrity-attacker-permissive",
            adapter="scripted",
            config={
                "tasks": {
                    permissive.task_id: {
                        "files": {
                            "evil_plugin.py": EVIL_PLUGIN,
                            "pytest.ini": CONFIG_INJECTIONS["pytest.ini"],
                        }
                    }
                }
            },
        )
        experiment = experiments.create_experiment(
            db, name="attack-permissive", agent_config_id=agent.id, task_keys=[record.key]
        )
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "integrity-worker")
        assert run_id is not None
        RunExecutor(run_id, "integrity-worker").execute()
        db.expire_all()
        run = db.execute(select(RunRecord).where(RunRecord.id == run_id)).scalars().one()

        assert run.outcome == RunOutcome.UNSOLVED
        patch_layer = next(layer for layer in run.grading.layers if layer["name"] == "patch")
        assert patch_layer["evidence"]["grading_integrity_violation"] is True

    def test_a_legitimate_source_only_fix_is_still_graded_solved(
        self, db: Session, seeded_task: Any, fixed_calc_source: str
    ) -> None:
        """The guard must not block honest work."""
        agent = register_agent(
            db,
            name="integrity-honest",
            adapter="scripted",
            config={"tasks": {"minirepo-add-bug": {"files": {"calc.py": fixed_calc_source}}}},
        )
        experiment = experiments.create_experiment(
            db, name="honest", agent_config_id=agent.id, task_keys=[seeded_task.key]
        )
        experiments.launch(db, experiment.id)
        db.commit()
        run_id = claim_next_run(db, "integrity-worker")
        assert run_id is not None
        RunExecutor(run_id, "integrity-worker").execute()
        db.expire_all()
        run = db.execute(select(RunRecord).where(RunRecord.id == run_id)).scalars().one()

        assert run.outcome == RunOutcome.SOLVED
        assert run.grading.passed is True


class TestBundledBenchmarkProtection:
    def test_every_bundled_task_protects_the_injection_points(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shipped benchmark must not rely on the engine-level backstop."""
        from agentbench.config import reset_settings_cache
        from agentbench.registry.loader import load_benchmark_file

        benchmarks_root = Path(__file__).resolve().parents[3] / "benchmarks"
        tasks_file = benchmarks_root / "tasks" / "textkit.json"
        if not tasks_file.is_file():
            pytest.skip("bundled benchmark is not present")

        monkeypatch.setenv("AGENTBENCH_BENCHMARKS_DIR", str(benchmarks_root))
        reset_settings_cache()

        for task in load_benchmark_file(tasks_file).tasks:
            unprotected = [
                filename
                for filename in CONFIG_INJECTIONS
                if not check_forbidden_paths([filename], task.grading.forbidden_paths)
            ]
            assert unprotected == [], f"{task.key} does not declare {unprotected} as forbidden"
