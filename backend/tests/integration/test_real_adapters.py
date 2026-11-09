"""The two non-simulated adapters.

``shell_command`` is exercised for real: a small shell program runs *inside the
container* as the agent, edits the repository, and the harness grades whatever
it left behind. That is the same code path a production CLI agent (Aider, Codex
CLI, Claude Code) takes, minus the model, so it validates the bridge end to end
without credentials.

``openai_tools`` needs a provider key. Its offline behaviour (refusing to start,
honest readiness reporting) is asserted here; the live loop is skipped unless
``AGENTBENCH_OPENAI_API_KEY`` is set, and is never faked.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.adapters.base import AgentAdapterError
from agentbench.adapters.registry import build_adapter, readiness_for
from agentbench.db.models import RunRecord
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.worker import Worker
from agentbench.registry.agents import register_agent
from agentbench.schemas.enums import EventType, RunOutcome
from agentbench.trajectory.recorder import read_events

IMAGE = "agentbench/benchmark-python:3.12"

AGENT_SCRIPT = r"""
set -eu
test -f "$AGENTBENCH_TASK_FILE" || { echo "no task file"; exit 1; }
test -f "$AGENTBENCH_TASK_MD" || { echo "no task markdown"; exit 1; }
echo "agent starting in $AGENTBENCH_WORKDIR for run $AGENTBENCH_RUN_ID"
grep -q 'left - right' calc.py
sed -i 's/return left - right/return left + right/' calc.py
echo "patched calc.py"
printf '{"input_tokens": 120, "output_tokens": 45, "requests": 1}\n' > /tmp/usage.json
"""


@pytest.fixture
def docker_ready() -> None:
    from agentbench.sandbox.docker_sandbox import docker_available, image_present

    available, detail = docker_available(use_cache=False)
    if not available:
        pytest.skip(f"no Docker daemon: {detail}")
    if not image_present(IMAGE):
        pytest.skip(f"benchmark image '{IMAGE}' is not built")


# --------------------------------------------------------------------------- #
@pytest.mark.docker
class TestShellCommandAdapter:
    def _run(self, db: Session, task_key: str, config: dict[str, Any]) -> RunRecord:
        agent = register_agent(db, name="cli-agent", adapter="shell_command", config=config)
        experiment = experiments.create_experiment(
            db, name="cli", agent_config_id=agent.id, task_keys=[task_key]
        )
        experiments.launch(db, experiment.id)
        db.commit()
        Worker(worker_id="cli-worker", concurrency=1).drain(max_seconds=600)
        db.expire_all()
        return (
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment.id))
            .scalars()
            .one()
        )

    def test_an_external_shell_agent_can_solve_a_task(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db,
            seeded_docker_task.key,
            {"command": AGENT_SCRIPT, "usage_file": "/tmp/usage.json", "needs_network": False},
        )

        assert run.outcome == RunOutcome.SOLVED
        assert run.grading.passed is True
        assert "calc.py" in run.patch

    def test_the_task_brief_is_delivered_as_files_in_the_sandbox(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db, seeded_docker_task.key, {"command": AGENT_SCRIPT, "needs_network": False}
        )
        events = read_events(db, run.id, limit=500)
        supplied = [
            event
            for event in events
            if event.event_type == EventType.TASK_CONTEXT_SUPPLIED
            and event.payload.get("transport") == "files"
        ]
        assert supplied
        assert supplied[0]["task_file"] if isinstance(supplied[0], dict) else True
        assert supplied[0].payload["task_file"].endswith("task.json")
        assert supplied[0].payload["hints_included"] is False

    def test_agent_reported_usage_is_recorded_with_its_provenance(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db,
            seeded_docker_task.key,
            {"command": AGENT_SCRIPT, "usage_file": "/tmp/usage.json", "needs_network": False},
        )
        assert run.usage["input_tokens"] == 120
        assert run.usage["output_tokens"] == 45
        assert run.usage["reported_by_provider"] is True

    def test_no_usage_file_means_usage_is_unknown_not_zero(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db, seeded_docker_task.key, {"command": AGENT_SCRIPT, "needs_network": False}
        )
        assert run.usage["input_tokens"] is None
        assert run.usage["reported_by_provider"] is False
        assert run.cost_usd is None

    def test_a_failing_agent_command_is_an_agent_error(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db, seeded_docker_task.key, {"command": "echo failing; exit 3", "needs_network": False}
        )
        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "exited 3" in (run.termination_reason or "")

    def test_an_agent_that_changes_nothing_is_unsolved(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db, seeded_docker_task.key, {"command": "echo thinking", "needs_network": False}
        )
        assert run.outcome == RunOutcome.UNSOLVED

    def test_agent_stdout_is_captured_in_the_event_stream(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        run = self._run(
            db, seeded_docker_task.key, {"command": AGENT_SCRIPT, "needs_network": False}
        )
        commands = [
            event
            for event in read_events(db, run.id, limit=500)
            if event.event_type == EventType.COMMAND_EXEC and event.payload.get("phase") == "agent"
        ]
        assert commands
        assert "patched calc.py" in commands[0].payload["stdout_tail"]


class TestShellCommandReadiness:
    def test_missing_command_is_reported_as_not_ready(self) -> None:
        report = readiness_for("shell_command", {})
        assert report.ready is False
        assert "command" in report.missing

    def test_missing_host_secret_is_reported_by_name(self) -> None:
        report = readiness_for(
            "shell_command",
            {"command": "run-agent", "secret_env": ["AGENTBENCH_TEST_ABSENT_KEY"]},
        )
        assert report.ready is False
        assert "AGENTBENCH_TEST_ABSENT_KEY" in report.missing

    def test_present_host_secret_satisfies_readiness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTBENCH_TEST_PRESENT_KEY", "value")
        report = readiness_for(
            "shell_command",
            {"command": "run-agent", "secret_env": ["AGENTBENCH_TEST_PRESENT_KEY"]},
        )
        assert report.ready is True


class TestOpenAIToolsAdapterOffline:
    def test_readiness_names_the_missing_credential(self) -> None:
        report = readiness_for("openai_tools", {"model": "gpt-4o-mini"})
        assert report.ready is False
        assert "AGENTBENCH_OPENAI_API_KEY" in report.missing

    def test_capabilities_declare_the_requirements_honestly(self) -> None:
        from agentbench.adapters.registry import capabilities_for

        capabilities = capabilities_for("openai_tools")
        assert capabilities.is_simulated is False
        assert capabilities.requires_network is True
        assert capabilities.reports_token_usage is True
        assert capabilities.requires_credentials == ("AGENTBENCH_OPENAI_API_KEY",)

    def test_running_without_a_key_raises_instead_of_pretending(self) -> None:
        adapter = build_adapter("openai_tools", {"model": "gpt-4o-mini"})
        with pytest.raises(AgentAdapterError) as exc:
            adapter.run(None)  # type: ignore[arg-type]
        assert "AGENTBENCH_OPENAI_API_KEY" in str(exc.value)

    def test_parent_traversal_is_refused(self) -> None:
        from agentbench.adapters.openai_tools import OpenAIToolsAdapter

        for path in ("../etc/passwd", "a/../../b", "src/../../outside.py"):
            with pytest.raises(ValueError, match="escapes"):
                OpenAIToolsAdapter._safe_rel(path)

    def test_an_absolute_path_is_clamped_into_the_repository(self) -> None:
        """A leading slash is treated as repo-relative rather than rejected, so
        a model writing `/src/mod.py` edits the repo and cannot reach the
        container root."""
        from agentbench.adapters.openai_tools import OpenAIToolsAdapter

        assert OpenAIToolsAdapter._safe_rel("/src/mod.py") == "src/mod.py"
        assert OpenAIToolsAdapter._safe_rel("/etc/passwd") == "etc/passwd"

    def test_safe_relative_paths_are_normalised(self) -> None:
        from agentbench.adapters.openai_tools import OpenAIToolsAdapter

        assert OpenAIToolsAdapter._safe_rel("./src//mod.py") == "src/mod.py"

    def test_context_compaction_keeps_the_task_and_recent_turns(self) -> None:
        from agentbench.adapters.openai_tools import _compact

        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
        messages += [{"role": "tool", "content": f"result {i}"} for i in range(100)]

        compacted, dropped = _compact(messages)

        assert dropped > 0
        assert compacted[0]["content"] == "sys"
        assert compacted[1]["content"] == "task"
        assert "harness notice" in compacted[2]["content"]
        assert compacted[-1]["content"] == "result 99"

    def test_usage_merging_treats_unreported_as_unknown(self) -> None:
        from agentbench.adapters.base import TokenUsage
        from agentbench.adapters.openai_tools import _merge_usage

        merged = _merge_usage(TokenUsage(), TokenUsage())
        assert merged.input_tokens is None

        merged = _merge_usage(TokenUsage(input_tokens=5), TokenUsage(input_tokens=7, requests=1))
        assert merged.input_tokens == 12

    def test_usage_is_parsed_from_a_provider_response(self) -> None:
        from agentbench.adapters.openai_tools import _usage_from_response

        usage = _usage_from_response(
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 40},
                }
            }
        )
        assert usage.input_tokens == 100
        assert usage.output_tokens == 20
        assert usage.cached_input_tokens == 40

    def test_absent_usage_block_yields_unknown_not_zero(self) -> None:
        from agentbench.adapters.openai_tools import _usage_from_response

        usage = _usage_from_response({})
        assert usage.input_tokens is None
        assert usage.to_dict()["reported_by_provider"] is False


@pytest.mark.llm
@pytest.mark.docker
@pytest.mark.skipif(
    not os.environ.get("AGENTBENCH_OPENAI_API_KEY"),
    reason="no model provider credentials configured; a real-model run is not simulated",
)
class TestOpenAIToolsAdapterLive:
    def test_a_real_model_run_produces_a_graded_outcome(
        self, db: Session, seeded_docker_task: Any, docker_ready: None
    ) -> None:
        """Asserts the pipeline, not the model: any graded outcome is a pass,
        because a model failing a task is a legitimate result."""
        agent = register_agent(
            db,
            name="live-model",
            adapter="openai_tools",
            model=os.environ.get("AGENTBENCH_TEST_MODEL", "gpt-4o-mini"),
            config={
                "model": os.environ.get("AGENTBENCH_TEST_MODEL", "gpt-4o-mini"),
                "max_tokens": 2048,
            },
        )
        experiment = experiments.create_experiment(
            db, name="live", agent_config_id=agent.id, task_keys=[seeded_docker_task.key]
        )
        experiments.launch(db, experiment.id)
        db.commit()
        Worker(worker_id="live-worker", concurrency=1).drain(max_seconds=900)
        db.expire_all()

        run = (
            db.execute(select(RunRecord).where(RunRecord.experiment_id == experiment.id))
            .scalars()
            .one()
        )

        assert run.outcome in (RunOutcome.SOLVED, RunOutcome.UNSOLVED, RunOutcome.TIMEOUT)
        types = {event.event_type for event in read_events(db, run.id, limit=1000)}
        assert EventType.MODEL_REQUEST in types
        assert EventType.MODEL_RESPONSE in types
