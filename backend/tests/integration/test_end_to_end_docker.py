"""Full pipeline against the bundled benchmark in real containers.

This is the acceptance test for the whole product: import the shipped
``textkit`` benchmark, validate it, run two different agents over it in Docker,
and check that the resulting metrics, failure classification, comparison and
exported report all follow from the stored evidence.

Both agents are deterministic and non-LLM, so this proves the harness is
correct, not that any model is capable. A real-model run needs provider
credentials and is covered by ``tests/integration/test_openai_adapter.py``
(skipped without a key).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import RunRecord
from agentbench.metrics.compare import compare_experiments
from agentbench.metrics.compute import experiment_metrics
from agentbench.metrics.export import render_report
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.worker import Worker
from agentbench.registry.agents import register_agent
from agentbench.registry.loader import load_benchmark_file
from agentbench.registry.service import import_benchmark, list_tasks
from agentbench.schemas.enums import (
    ExperimentState,
    FailureCategory,
    RunOutcome,
    TaskValidationStatus,
)

pytestmark = [pytest.mark.docker, pytest.mark.simulated]

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_BENCHMARKS = REPO_ROOT / "benchmarks"
IMAGE = "agentbench/benchmark-python:3.12"


@pytest.fixture
def real_benchmarks(monkeypatch: pytest.MonkeyPatch) -> Path:
    from agentbench.config import reset_settings_cache
    from agentbench.sandbox.docker_sandbox import docker_available, image_present

    available, detail = docker_available(use_cache=False)
    if not available:
        pytest.skip(f"no Docker daemon: {detail}")
    if not image_present(IMAGE):
        pytest.skip(f"benchmark image '{IMAGE}' is not built")
    if not (REAL_BENCHMARKS / "tasks" / "textkit.json").is_file():
        pytest.skip("bundled textkit benchmark is not present")

    monkeypatch.setenv("AGENTBENCH_BENCHMARKS_DIR", str(REAL_BENCHMARKS))
    reset_settings_cache()
    return REAL_BENCHMARKS


@pytest.fixture
def textkit(db: Session, real_benchmarks: Path) -> list[Any]:
    benchmark = load_benchmark_file(real_benchmarks / "tasks" / "textkit.json")
    import_benchmark(db, benchmark)
    db.commit()
    rows, _ = list_tasks(db, limit=50, benchmark_id="textkit")
    assert len(rows) == 3
    return rows


def solution(real_benchmarks: Path, name: str) -> str:
    return (real_benchmarks / "solutions" / "textkit" / name).read_text(encoding="utf-8")


def run_experiment(db: Session, name: str, agent_id: str, task_keys: list[str]) -> str:
    experiment = experiments.create_experiment(
        db, name=name, agent_config_id=agent_id, task_keys=task_keys
    )
    experiments.launch(db, experiment.id)
    db.commit()
    reports = Worker(worker_id=f"e2e-{name}", concurrency=1).drain(max_seconds=900)
    assert reports, "the worker executed nothing"
    db.expire_all()
    return experiment.id


# --------------------------------------------------------------------------- #
class TestBundledBenchmarkValidation:
    def test_every_bundled_task_validates_in_a_real_container(
        self, db: Session, textkit: list[Any], real_benchmarks: Path
    ) -> None:
        """Checks the shipped benchmark is executable AND falsifiable: the
        target tests must fail before a fix, and the regression guards must
        pass."""
        from agentbench.registry.service import set_validation_result, spec_of
        from agentbench.registry.validator import validate_task

        for record in textkit:
            report = validate_task(spec_of(record))
            set_validation_result(db, record, report.status, report.to_dict())

            assert report.status is TaskValidationStatus.VALID, (
                f"{record.key} failed validation: {report.to_dict()['failed_checks']}"
            )
            checks = {check.name: check.ok for check in report.checks}
            assert checks["fail_to_pass_fails_on_baseline"] is True
            assert checks["pass_to_pass_passes_on_baseline"] is True
            assert checks["acceptance_tests_protected"] is True


class TestFullPipeline:
    def test_a_correct_agent_solves_every_bundled_task(
        self, db: Session, textkit: list[Any], real_benchmarks: Path
    ) -> None:
        agent = register_agent(
            db,
            name="e2e-reference",
            adapter="scripted",
            config={
                "tasks": {
                    "textkit-slugify-collapse": {
                        "files": {"src/textkit/slug.py": solution(real_benchmarks, "slug.py")}
                    },
                    "textkit-wordwrap": {
                        "files": {"src/textkit/wrap.py": solution(real_benchmarks, "wrap.py")}
                    },
                    "textkit-truncate-boundary": {
                        "files": {
                            "src/textkit/truncate.py": solution(real_benchmarks, "truncate.py")
                        }
                    },
                }
            },
        )
        db.commit()

        experiment_id = run_experiment(
            db, "reference", agent.id, [record.key for record in textkit]
        )
        metrics = experiment_metrics(db, experiment_id).to_dict()

        assert metrics["outcomes"]["solved"] == 3
        assert metrics["outcomes"]["unsolved"] == 0
        assert metrics["outcomes"]["infra_error"] == 0
        assert metrics["success_rate_attributable"]["value"] == 1.0
        assert metrics["success_rate_attributable"]["denominator"] == 3
        assert metrics["completeness"]["is_complete"] is True
        assert experiments.refresh_state(db, experiment_id) is ExperimentState.COMPLETED

    def test_every_solved_run_has_a_patch_and_a_full_grading_trail(
        self, db: Session, textkit: list[Any], real_benchmarks: Path
    ) -> None:
        agent = register_agent(
            db,
            name="e2e-slug-only",
            adapter="scripted",
            config={
                "tasks": {
                    "textkit-slugify-collapse": {
                        "files": {"src/textkit/slug.py": solution(real_benchmarks, "slug.py")}
                    }
                }
            },
        )
        db.commit()
        experiment_id = run_experiment(
            db, "slug-only", agent.id, [record.key for record in textkit]
        )

        runs = list(
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment_id)).scalars()
        )
        solved = [run for run in runs if run.outcome == RunOutcome.SOLVED]
        unsolved = [run for run in runs if run.outcome == RunOutcome.UNSOLVED]

        assert len(solved) == 1
        assert len(unsolved) == 2
        assert solved[0].task_id == "textkit-slugify-collapse"
        assert "src/textkit/slug.py" in solved[0].patch
        assert solved[0].grading.passed is True
        assert {layer["name"] for layer in solved[0].grading.layers} == {
            "environment",
            "patch",
            "fail_to_pass",
            "pass_to_pass",
        }
        assert all(run.sandbox_provider == "docker" for run in runs)

    def test_the_pass_to_pass_layer_catches_a_real_regression(
        self, db: Session, textkit: list[Any], real_benchmarks: Path
    ) -> None:
        """The regression variant passes the hidden acceptance tests but breaks
        a pre-existing one; the run must be UNSOLVED and classified as a
        regression rather than silently credited."""
        from agentbench.metrics.failure import effective_annotation

        agent = register_agent(
            db,
            name="e2e-regressor",
            adapter="scripted",
            config={
                "tasks": {
                    "textkit-truncate-boundary": {
                        "files": {
                            "src/textkit/truncate.py": solution(
                                real_benchmarks, "truncate_regression.py"
                            )
                        }
                    }
                }
            },
        )
        db.commit()
        experiment_id = run_experiment(db, "regressor", agent.id, ["textkit-truncate-boundary@1.0"])

        run = (
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment_id))
            .scalars()
            .one()
        )
        layers = {layer["name"]: layer["status"] for layer in run.grading.layers}

        assert run.outcome == RunOutcome.UNSOLVED
        assert layers["fail_to_pass"] == "passed"
        assert layers["pass_to_pass"] == "failed"
        assert effective_annotation(db, run.id).category == FailureCategory.EXISTING_REGRESSION
        assert experiment_metrics(db, experiment_id).regression_count == 1

    def test_overwriting_the_hidden_tests_does_not_pass_the_task(
        self, db: Session, textkit: list[Any]
    ) -> None:
        from agentbench.metrics.failure import effective_annotation

        agent = register_agent(
            db, name="e2e-tamperer", adapter="mock", config={"behaviour": "touch_forbidden"}
        )
        db.commit()
        experiment_id = run_experiment(db, "tamperer", agent.id, ["textkit-wordwrap@1.0"])

        run = (
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment_id))
            .scalars()
            .one()
        )

        assert run.outcome == RunOutcome.UNSOLVED
        assert run.grading.passed is False
        assert effective_annotation(db, run.id).category == (
            FailureCategory.FORBIDDEN_FILES_MODIFIED
        )

    def test_no_containers_are_left_behind_after_a_full_experiment(
        self, db: Session, textkit: list[Any]
    ) -> None:
        import subprocess

        from agentbench.config import get_settings
        from agentbench.sandbox.docker_sandbox import LABEL_NAMESPACE

        agent = register_agent(db, name="e2e-cleanup", adapter="mock", config={"behaviour": "noop"})
        db.commit()
        run_experiment(db, "cleanup", agent.id, [record.key for record in textkit])

        proc = subprocess.run(  # noqa: S603
            [
                get_settings().docker_binary,
                "ps",
                "-a",
                "--filter",
                f"label={LABEL_NAMESPACE}.managed=true",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.stdout.strip() == ""


class TestComparisonFromRealRuns:
    def test_comparison_reports_per_task_movement_and_withholds_significance(
        self, db: Session, textkit: list[Any], real_benchmarks: Path
    ) -> None:
        full = register_agent(
            db,
            name="cmp-full",
            adapter="scripted",
            config={
                "tasks": {
                    "textkit-slugify-collapse": {
                        "files": {"src/textkit/slug.py": solution(real_benchmarks, "slug.py")}
                    },
                    "textkit-wordwrap": {
                        "files": {"src/textkit/wrap.py": solution(real_benchmarks, "wrap.py")}
                    },
                    "textkit-truncate-boundary": {
                        "files": {
                            "src/textkit/truncate.py": solution(real_benchmarks, "truncate.py")
                        }
                    },
                }
            },
        )
        partial = register_agent(
            db,
            name="cmp-partial",
            adapter="scripted",
            config={
                "tasks": {
                    "textkit-slugify-collapse": {
                        "files": {"src/textkit/slug.py": solution(real_benchmarks, "slug.py")}
                    }
                }
            },
        )
        db.commit()

        keys = [record.key for record in textkit]
        baseline_id = run_experiment(db, "cmp-baseline", full.id, keys)
        candidate_id = run_experiment(db, "cmp-candidate", partial.id, keys)

        comparison = compare_experiments(db, baseline_id, candidate_id).to_dict()

        assert comparison["comparable"] is True
        assert comparison["blockers"] == []
        assert comparison["delta"]["solved"] == -2
        assert set(comparison["movement"]["newly_broken"]) == {
            "textkit-wordwrap@1.0",
            "textkit-truncate-boundary@1.0",
        }
        assert comparison["movement"]["unchanged"] == ["textkit-slugify-collapse@1.0"]
        assert comparison["statistical_note"]["claims_significance"] is False
        assert "Newly broken" in comparison["summary"]

    def test_comparison_flags_an_incomparable_task_set(
        self, db: Session, textkit: list[Any], real_benchmarks: Path
    ) -> None:
        agent = register_agent(db, name="cmp-noop", adapter="mock", config={"behaviour": "noop"})
        db.commit()
        wide = run_experiment(db, "wide", agent.id, [record.key for record in textkit])
        narrow = run_experiment(db, "narrow", agent.id, ["textkit-wordwrap@1.0"])

        comparison = compare_experiments(db, wide, narrow).to_dict()

        assert comparison["comparable"] is False
        assert any("same task set" in blocker for blocker in comparison["blockers"])


class TestReportExport:
    def test_markdown_report_states_denominators_and_caveats(
        self, db: Session, textkit: list[Any]
    ) -> None:
        agent = register_agent(
            db, name="report-agent", adapter="mock", config={"behaviour": "noop"}
        )
        db.commit()
        experiment_id = run_experiment(db, "report", agent.id, [record.key for record in textkit])

        markdown = render_report(db, experiment_id, fmt="markdown")

        assert "# Experiment report" in markdown
        assert "Simulated agent" in markdown
        assert "Numerator / denominator" in markdown
        assert "0 / 3" in markdown
        assert "## Caveats" in markdown
        assert "## Reproduction" in markdown

    def test_json_report_round_trips_and_carries_every_run(
        self, db: Session, textkit: list[Any]
    ) -> None:
        agent = register_agent(
            db, name="report-agent-2", adapter="mock", config={"behaviour": "noop"}
        )
        db.commit()
        experiment_id = run_experiment(db, "report2", agent.id, [record.key for record in textkit])

        payload = json.loads(render_report(db, experiment_id, fmt="json"))

        assert len(payload["runs"]) == 3
        assert payload["metrics"]["outcomes"]["unsolved"] == 3
        assert payload["config_snapshot"]["execution"]["sandbox_provider"] == "docker"
        assert all(row["counts_toward_success_rate"] is True for row in payload["runs"])

    def test_csv_report_is_machine_readable(self, db: Session, textkit: list[Any]) -> None:
        import csv
        import io

        agent = register_agent(
            db, name="report-agent-3", adapter="mock", config={"behaviour": "noop"}
        )
        db.commit()
        experiment_id = run_experiment(db, "report3", agent.id, [record.key for record in textkit])

        rows = list(csv.DictReader(io.StringIO(render_report(db, experiment_id, fmt="csv"))))

        assert len(rows) == 3
        assert {row["outcome"] for row in rows} == {"unsolved"}
        assert all(row["failure_category"] == "no_patch_produced" for row in rows)
