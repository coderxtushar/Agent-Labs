"""Adapter for an external CLI coding agent.

This is the general-purpose bridge to a *real* agent harness (Aider, Codex CLI,
Claude Code, an in-house script, anything that edits files in a working
directory).  The agent binary runs **inside the sandbox**, so it is subject to
the same isolation, resource limits and network policy as the task itself.

How it works
------------
1. The task brief is written to ``/workspace/.agentbench/task.json`` and
   ``task.md`` inside the sandbox.
2. The configured command runs in the repository workdir with
   ``AGENTBENCH_TASK_FILE`` / ``AGENTBENCH_TASK_MD`` / ``AGENTBENCH_WORKDIR``
   exported.
3. stdout and stderr are captured into the event stream.
4. Whatever the command leaves in the workspace becomes the patch.

Getting a real agent in here
----------------------------
The agent binary and its credentials must be present inside the sandbox.  That
means building an image that contains the CLI (extend
``docker/benchmark-python3.12.Dockerfile``), and passing the provider key via
``secret_env`` so it is injected into the container environment at run time and
redacted from logs and events.  The platform deliberately refuses to mount host
credential files or the Docker socket into a sandbox.

Agents that need to reach a model endpoint also need the task to declare
``limits.network = "model_only"``.  With the default ``none`` policy an
API-backed CLI cannot work, and this adapter says so rather than producing a
mysterious empty patch.
"""

from __future__ import annotations

import json
import os
from typing import Any

from agentbench.adapters.base import (
    AdapterCapabilities,
    AgentAdapter,
    AgentConfigError,
    AgentRunContext,
    AgentRunResult,
    AgentStatus,
    ReadinessReport,
    TokenUsage,
)
from agentbench.logging_setup import get_logger
from agentbench.schemas.enums import EventType
from agentbench.schemas.task import NetworkPolicy

log = get_logger(__name__)

BRIEF_DIR = "/workspace/.agentbench"


class ShellCommandAdapter(AgentAdapter):
    """Runs a configured command inside the sandbox and captures its output."""

    capabilities = AdapterCapabilities(
        key="shell_command",
        display_name="External CLI agent",
        description=(
            "Runs an agent command line inside the sandbox. The CLI and its credentials must "
            "be present in the task image; nothing is mounted from the host."
        ),
        is_simulated=False,
        requires_network=False,
        reports_token_usage=False,
        reports_cost=False,
        config_schema={
            "command": {
                "type": "string",
                "required": True,
                "description": "shell command executed in the repo workdir",
            },
            "env": {"type": "object", "description": "non-secret environment variables"},
            "secret_env": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "names of host environment variables forwarded into the sandbox; "
                    "values are never stored or logged"
                ),
            },
            "usage_file": {
                "type": "string",
                "description": (
                    "optional sandbox path to a JSON file the agent writes with "
                    "input_tokens/output_tokens/cost_usd"
                ),
            },
            "expect_nonzero_exit_ok": {"type": "boolean", "default": False},
        },
    )

    def validate_config(self) -> None:
        command = self.config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise AgentConfigError("shell_command adapter requires a non-empty 'command'")
        for key in ("env",):
            if key in self.config and not isinstance(self.config[key], dict):
                raise AgentConfigError(f"'{key}' must be an object")
        if "secret_env" in self.config and not isinstance(self.config["secret_env"], list):
            raise AgentConfigError("'secret_env' must be a list of environment variable names")

    @classmethod
    def readiness(cls, config: dict[str, Any] | None = None) -> ReadinessReport:
        config = config or {}
        command = config.get("command")
        if not command:
            return ReadinessReport(False, "no command configured", ("command",))
        missing = [name for name in config.get("secret_env", []) if not os.environ.get(name)]
        if missing:
            return ReadinessReport(
                False,
                "required secret environment variables are not set on the host: "
                + ", ".join(missing),
                tuple(missing),
            )
        return ReadinessReport(
            True,
            "command configured; the agent binary must exist inside the task image "
            "(verified at run time, not here)",
        )

    def run(self, ctx: AgentRunContext) -> AgentRunResult:
        command: str = self.config["command"]
        task_file = f"{BRIEF_DIR}/task.json"
        md_file = f"{BRIEF_DIR}/task.md"

        ctx.sandbox.write_file(task_file, json.dumps(ctx.brief, indent=2))
        ctx.sandbox.write_file(md_file, _render_brief_markdown(ctx.brief))
        ctx.events.record(
            EventType.TASK_CONTEXT_SUPPLIED,
            {
                "transport": "files",
                "task_file": task_file,
                "markdown_file": md_file,
                "brief_keys": sorted(ctx.brief),
                "hints_included": "hints" in ctx.brief,
            },
        )

        if ctx.task.limits.network is NetworkPolicy.NONE and self.config.get("needs_network", True):
            # Not fatal - a local agent may legitimately need no network - but
            # recorded so an empty patch is not a mystery later.
            ctx.events.record(
                EventType.AGENT_MESSAGE,
                {
                    "level": "warning",
                    "text": (
                        "task network policy is 'none'; an agent that calls a remote model "
                        "endpoint will fail. Set limits.network='model_only' if that is needed."
                    ),
                },
            )

        env: dict[str, str] = {
            "AGENTBENCH_TASK_FILE": task_file,
            "AGENTBENCH_TASK_MD": md_file,
            "AGENTBENCH_WORKDIR": ctx.workdir,
            "AGENTBENCH_MAX_STEPS": str(ctx.max_steps),
            "AGENTBENCH_RUN_ID": ctx.run_id,
        }
        env.update({str(k): str(v) for k, v in (self.config.get("env") or {}).items()})
        if ctx.seed is not None:
            env["AGENTBENCH_SEED"] = str(ctx.seed)

        # Secrets are read from the host environment at run time and forwarded
        # directly to the container; they are never persisted in the agent
        # record, the event stream or the logs.
        secret_env = {
            name: os.environ[name]
            for name in self.config.get("secret_env", [])
            if os.environ.get(name)
        }
        if secret_env:
            ctx.events.record(
                EventType.AGENT_MESSAGE,
                {"text": "forwarded secret environment variables", "names": sorted(secret_env)},
            )

        if ctx.is_cancelled():
            return self._cancelled_result()

        remaining = int(ctx.seconds_remaining)
        if remaining <= 0:
            return self._timeout_result(0, "no time budget left before the agent started")

        ctx.events.record(
            EventType.TOOL_CALL,
            {"tool": "shell_command", "command": command, "timeout_s": remaining},
        )
        result = ctx.sandbox.exec(
            command, timeout_s=remaining, workdir=ctx.workdir, env={**env, **secret_env}
        )
        ctx.events.command(result, phase="agent")

        usage, cost, cost_source = self._read_usage(ctx)

        if result.timed_out:
            return AgentRunResult(
                AgentStatus.TIMEOUT,
                reason=f"agent command exceeded {remaining}s",
                steps=1,
                usage=usage,
                cost_usd=cost,
                cost_source=cost_source,
            )
        if ctx.is_cancelled():
            return self._cancelled_result(1)
        if result.exit_code != 0 and not self.config.get("expect_nonzero_exit_ok", False):
            return AgentRunResult(
                AgentStatus.ERROR,
                reason=(
                    f"agent command exited {result.exit_code}: "
                    f"{(result.stderr or result.stdout)[-800:]}"
                ),
                steps=1,
                usage=usage,
                cost_usd=cost,
                cost_source=cost_source,
            )

        ctx.events.record(EventType.AGENT_END, {"exit_code": result.exit_code, "steps": 1})
        return AgentRunResult(
            AgentStatus.COMPLETED,
            reason=f"agent command exited {result.exit_code}",
            steps=1,
            usage=usage,
            cost_usd=cost,
            cost_source=cost_source,
            metadata={"exit_code": result.exit_code},
        )

    def _read_usage(self, ctx: AgentRunContext) -> tuple[TokenUsage, float | None, str | None]:
        """Read optional usage written by the agent. Absent means unknown."""
        path = self.config.get("usage_file")
        if not path:
            return TokenUsage(), None, None
        try:
            raw = ctx.sandbox.read_file(path, max_bytes=65536)
            data = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return TokenUsage(), None, None
        if not isinstance(data, dict):
            return TokenUsage(), None, None

        usage = TokenUsage(
            input_tokens=_as_int(data.get("input_tokens")),
            output_tokens=_as_int(data.get("output_tokens")),
            cached_input_tokens=_as_int(data.get("cached_input_tokens")),
            reasoning_tokens=_as_int(data.get("reasoning_tokens")),
            requests=_as_int(data.get("requests")) or 0,
        )
        cost = data.get("cost_usd")
        cost_value = float(cost) if isinstance(cost, (int, float)) else None
        return usage, cost_value, "agent_reported" if cost_value is not None else None


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _render_brief_markdown(brief: dict[str, Any]) -> str:
    lines = [f"# {brief.get('title', 'Task')}", "", brief.get("problem_statement", ""), ""]
    if brief.get("relevant_files"):
        lines += ["## Possibly relevant files", ""]
        lines += [f"- `{path}`" for path in brief["relevant_files"]]
        lines.append("")
    if brief.get("acceptance_criteria"):
        lines += ["## Acceptance criteria", ""]
        lines += [f"- {item}" for item in brief["acceptance_criteria"]]
        lines.append("")
    if brief.get("forbidden_paths"):
        lines += ["## Do not modify", ""]
        lines += [f"- `{path}`" for path in brief["forbidden_paths"]]
        lines.append("")
    if brief.get("hints"):
        lines += ["## Hints", ""]
        lines += [f"- {item}" for item in brief["hints"]]
        lines.append("")
    lines += [
        "## Working directory",
        "",
        f"`{brief.get('workdir', '/workspace/repo')}`",
        "",
        "Edit files in place. Do not create a commit; the harness captures the diff.",
        "",
    ]
    return "\n".join(lines)
