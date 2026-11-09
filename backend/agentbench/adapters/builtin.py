"""Deterministic adapters.

Neither of these calls a language model.  Both are marked ``is_simulated`` so
their results can never be presented as model benchmark numbers - the API, the
metrics layer and the UI all carry that flag through.

``mock``
    A behaviour-driven test double used to exercise the orchestrator and grading
    engine: it can solve, do nothing, produce a wrong patch, touch a protected
    path, hang until the timeout, or crash.  This is how the platform's own
    tests cover failure paths without burning model credits.

``scripted``
    Writes a fixed set of files per task and optionally runs fixed commands
    inside the sandbox.  Pointed at the bundled reference solutions it acts as a
    known-good agent, which is what makes an end-to-end "SOLVED" result
    meaningful: if the pipeline cannot pass a task with a correct patch in hand,
    the pipeline is broken.
"""

from __future__ import annotations

import time
from typing import Any

from agentbench.adapters.base import (
    AdapterCapabilities,
    AgentAdapter,
    AgentAdapterError,
    AgentConfigError,
    AgentRunContext,
    AgentRunResult,
    AgentStatus,
    ReadinessReport,
    TokenUsage,
)
from agentbench.logging_setup import get_logger
from agentbench.schemas.enums import EventType

log = get_logger(__name__)

_POLL_SLICE_S = 0.25


def _sleep_until_cancelled_or(ctx: AgentRunContext, seconds: float) -> bool:
    """Sleep in slices, returning True if cancellation was requested."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ctx.is_cancelled():
            return True
        time.sleep(min(_POLL_SLICE_S, max(0.0, end - time.monotonic())))
    return False


class MockAgentAdapter(AgentAdapter):
    """Behaviour-driven test double. Never calls a model."""

    BEHAVIOURS = (
        "noop",
        "write_files",
        "wrong_patch",
        "touch_forbidden",
        "hang",
        "crash",
        "sandbox_fault",
    )

    capabilities = AdapterCapabilities(
        key="mock",
        display_name="Mock agent (simulated)",
        description=(
            "Deterministic test double with selectable behaviour. Used for infrastructure "
            "tests; produces no model results."
        ),
        is_simulated=True,
        reports_token_usage=True,
        reports_cost=False,
        config_schema={
            "behaviour": {"type": "string", "enum": list(BEHAVIOURS), "default": "noop"},
            "files": {"type": "object", "description": "repo-relative path -> file content"},
            "hang_seconds": {"type": "number", "default": 3600},
            "fake_usage": {"type": "object", "description": "input_tokens/output_tokens to report"},
        },
    )

    def validate_config(self) -> None:
        behaviour = self.config.get("behaviour", "noop")
        if behaviour not in self.BEHAVIOURS:
            raise AgentConfigError(
                f"unknown mock behaviour '{behaviour}'; expected one of {self.BEHAVIOURS}"
            )
        files = self.config.get("files", {})
        if not isinstance(files, dict):
            raise AgentConfigError("mock 'files' must be an object of path -> content")
        if behaviour == "write_files" and not files:
            raise AgentConfigError("mock behaviour 'write_files' requires a non-empty 'files' map")

    @classmethod
    def readiness(cls, config: dict[str, Any] | None = None) -> ReadinessReport:
        return ReadinessReport(True, "always available (simulated adapter)")

    def run(self, ctx: AgentRunContext) -> AgentRunResult:
        behaviour = str(self.config.get("behaviour", "noop"))
        ctx.events.record(
            EventType.AGENT_START,
            {"adapter": "mock", "behaviour": behaviour, "simulated": True},
        )

        usage = TokenUsage(
            input_tokens=self.config.get("fake_usage", {}).get("input_tokens"),
            output_tokens=self.config.get("fake_usage", {}).get("output_tokens"),
            requests=1,
        )

        if behaviour == "crash":
            raise AgentAdapterError("mock adapter crashed on purpose (behaviour='crash')")

        if behaviour == "sandbox_fault":
            from agentbench.sandbox.base import SandboxError

            raise SandboxError("mock adapter raised a simulated sandbox fault")

        if behaviour == "hang":
            budget = min(float(self.config.get("hang_seconds", 3600)), ctx.seconds_remaining)
            if _sleep_until_cancelled_or(ctx, budget):
                return self._cancelled_result()
            return self._timeout_result(0, "mock adapter deliberately exhausted its budget")

        if behaviour == "noop":
            ctx.events.record(
                EventType.AGENT_MESSAGE,
                {"text": "mock adapter made no changes", "simulated": True},
            )
            return AgentRunResult(
                AgentStatus.COMPLETED, reason="noop behaviour", steps=0, usage=usage
            )

        files: dict[str, str] = dict(self.config.get("files", {}))
        if behaviour == "wrong_patch":
            files = files or {
                "src/_mock_wrong_patch.py": "# deliberately irrelevant change\nMOCK = True\n"
            }
        if behaviour == "touch_forbidden":
            target = next(iter(ctx.task.grading.acceptance_tests), None) or "tests/test_mock.py"
            files = {target: "def test_always_passes():\n    assert True\n"}

        steps = 0
        for rel_path, content in files.items():
            if ctx.is_cancelled():
                return self._cancelled_result(steps)
            path = f"{ctx.workdir}/{rel_path}"
            ctx.sandbox.write_file(path, content)
            steps += 1
            ctx.events.record(
                EventType.FILE_WRITE,
                {"path": rel_path, "bytes": len(content.encode()), "tool": "mock.write_file"},
            )

        ctx.events.record(EventType.AGENT_END, {"steps": steps, "behaviour": behaviour})
        return AgentRunResult(
            AgentStatus.COMPLETED, reason=f"{behaviour} behaviour", steps=steps, usage=usage
        )


class ScriptedAgentAdapter(AgentAdapter):
    """Applies a fixed, per-task set of file writes and commands.

    Configuration::

        {
          "tasks": {
            "<task_id>": {
              "files":    {"src/pkg/mod.py": "<file content>"},
              "commands": ["python -c 'import pkg'"]
            }
          },
          "on_unknown_task": "noop" | "error"
        }

    File contents may be written in a benchmark file as ``{"$file": "..."}``;
    they are inlined when the agent is registered, so the stored configuration
    is self-contained.
    """

    capabilities = AdapterCapabilities(
        key="scripted",
        display_name="Scripted agent (simulated)",
        description=(
            "Applies predefined file contents and commands per task. Exercises the full "
            "sandbox/patch/grading pipeline deterministically without a model."
        ),
        is_simulated=True,
        reports_token_usage=False,
        config_schema={
            "tasks": {"type": "object", "description": "task_id -> {files, commands}"},
            "on_unknown_task": {"type": "string", "enum": ["noop", "error"], "default": "noop"},
        },
    )

    def validate_config(self) -> None:
        tasks = self.config.get("tasks")
        if not isinstance(tasks, dict) or not tasks:
            raise AgentConfigError("scripted adapter requires a non-empty 'tasks' object")
        for task_id, action in tasks.items():
            if not isinstance(action, dict):
                raise AgentConfigError(f"scripted action for '{task_id}' must be an object")
            files = action.get("files", {})
            commands = action.get("commands", [])
            if not isinstance(files, dict):
                raise AgentConfigError(f"scripted 'files' for '{task_id}' must be an object")
            if not isinstance(commands, list):
                raise AgentConfigError(f"scripted 'commands' for '{task_id}' must be a list")
            if not files and not commands:
                raise AgentConfigError(
                    f"scripted action for '{task_id}' declares neither files nor commands"
                )
        if self.config.get("on_unknown_task", "noop") not in {"noop", "error"}:
            raise AgentConfigError("on_unknown_task must be 'noop' or 'error'")

    @classmethod
    def readiness(cls, config: dict[str, Any] | None = None) -> ReadinessReport:
        tasks = (config or {}).get("tasks") or {}
        if not tasks:
            return ReadinessReport(False, "no scripted task actions configured", ("tasks",))
        return ReadinessReport(True, f"{len(tasks)} scripted task action(s) configured")

    def run(self, ctx: AgentRunContext) -> AgentRunResult:
        action = self.config.get("tasks", {}).get(ctx.task.task_id)
        ctx.events.record(
            EventType.AGENT_START,
            {
                "adapter": "scripted",
                "task_id": ctx.task.task_id,
                "has_action": action is not None,
                "simulated": True,
            },
        )

        if action is None:
            if self.config.get("on_unknown_task", "noop") == "error":
                raise AgentAdapterError(
                    f"scripted adapter has no action for task '{ctx.task.task_id}'"
                )
            ctx.events.record(
                EventType.AGENT_MESSAGE,
                {"text": f"no scripted action for task '{ctx.task.task_id}'; made no changes"},
            )
            return AgentRunResult(
                AgentStatus.ABSTAINED,
                reason=f"no scripted action defined for task '{ctx.task.task_id}'",
            )

        steps = 0
        for rel_path, content in action.get("files", {}).items():
            if ctx.is_cancelled():
                return self._cancelled_result(steps)
            if ctx.expired:
                return self._timeout_result(steps)
            ctx.sandbox.write_file(f"{ctx.workdir}/{rel_path}", content)
            steps += 1
            ctx.events.record(
                EventType.FILE_WRITE,
                {
                    "path": rel_path,
                    "bytes": len(str(content).encode()),
                    "tool": "scripted.write_file",
                },
            )

        for command in action.get("commands", []):
            if ctx.is_cancelled():
                return self._cancelled_result(steps)
            remaining = int(ctx.seconds_remaining)
            if remaining <= 0:
                return self._timeout_result(steps)
            ctx.events.record(EventType.TOOL_CALL, {"tool": "scripted.run", "command": command})
            result = ctx.sandbox.exec(command, timeout_s=remaining, workdir=ctx.workdir)
            steps += 1
            ctx.events.command(result, phase="agent")
            if result.timed_out:
                return self._timeout_result(steps, f"scripted command timed out: {command}")

        ctx.events.record(EventType.AGENT_END, {"steps": steps})
        return AgentRunResult(AgentStatus.COMPLETED, reason="scripted action applied", steps=steps)
