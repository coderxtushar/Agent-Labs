"""A real model-driven coding agent over any OpenAI-compatible chat API.

Topology
--------
The model call happens on the **host**; the tools it invokes execute **inside
the sandbox**.  Nothing in the sandbox needs network access, so tasks keep the
default ``network: none`` policy while still being solved by a remote model.
The provider key never enters the container.

Tools exposed to the model
--------------------------
``list_files``, ``read_file``, ``write_file``, ``run_command`` and ``finish``.
Every call is recorded with its arguments and a truncated result, so the
trajectory is reconstructable.  Reads and writes are confined to the repository
workdir; attempts to escape it are refused and recorded rather than silently
clamped.

Honest limits
-------------
* Usage comes from the provider's ``usage`` object. When the provider omits it,
  the run records ``None``, not a guess.
* Cost is derived from a dated, in-repo price table and is always labelled
  ``estimated:<table-id>``, unless the gateway reports a real cost.
* ``context.compaction`` events are emitted when *this* adapter trims its own
  message list. A provider's internal context handling is not observable and is
  not reported.

Compatible with any endpoint speaking ``/chat/completions`` with tool calling:
OpenAI, OpenRouter, Together, Groq, vLLM, LM Studio, Ollama.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

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
from agentbench.adapters.pricing import PRICE_TABLE_ID, estimate_cost_usd
from agentbench.config import get_settings
from agentbench.logging_setup import get_logger
from agentbench.sandbox.base import SandboxError, sh_quote
from agentbench.schemas.enums import EventType

log = get_logger(__name__)

MAX_READ_CHARS = 60_000
MAX_TOOL_RESULT_CHARS = 20_000
MAX_MESSAGES_BEFORE_COMPACTION = 60
REQUEST_TIMEOUT_CEILING_S = 180.0
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

SYSTEM_PROMPT = """You are a software engineer working inside a sandboxed checkout of a repository.

Fix the task described by the user by editing files in the repository. Work only
through the provided tools.

Rules:
- The repository root is the working directory. All paths are relative to it.
- Read a file before you rewrite it. `write_file` replaces the entire file.
- Never modify any path listed as forbidden; doing so fails the task outright.
- Do not add dependencies; there is no network access inside the sandbox.
- Hidden tests grade your work, so satisfy the stated behaviour generally rather
  than special-casing the examples.
- Call `finish` once the change is complete, with a one-paragraph summary.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files under a directory, relative to the repository root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory, default '.'"},
                    "max_depth": {"type": "integer", "description": "Default 3"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write the complete new contents of a file, creating it if needed.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command in the repository root. There is no network access."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_s": {"type": "integer", "description": "Default 60, max 300"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Declare the work complete. Call this exactly once at the end.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


class OpenAIToolsAdapter(AgentAdapter):
    """Model-driven agent loop against an OpenAI-compatible chat endpoint."""

    capabilities = AdapterCapabilities(
        key="openai_tools",
        display_name="OpenAI-compatible tool-calling agent",
        description=(
            "Host-side agent loop; tools run inside the sandbox. Works with any endpoint "
            "exposing /chat/completions with tool calling."
        ),
        is_simulated=False,
        requires_network=True,
        requires_credentials=("AGENTBENCH_OPENAI_API_KEY",),
        reports_token_usage=True,
        reports_cost=True,
        config_schema={
            "model": {"type": "string", "required": True},
            "base_url": {"type": "string", "description": "Defaults to the configured provider"},
            "max_tokens": {"type": "integer", "default": 4096},
            "allow_run_command": {"type": "boolean", "default": True},
            "request_retries": {"type": "integer", "default": 3},
        },
    )

    def validate_config(self) -> None:
        if not self.config.get("model"):
            raise AgentConfigError("openai_tools adapter requires 'model'")
        retries = self.config.get("request_retries", 3)
        if not isinstance(retries, int) or retries < 0 or retries > 10:
            raise AgentConfigError("'request_retries' must be an integer between 0 and 10")

    @classmethod
    def readiness(cls, config: dict[str, Any] | None = None) -> ReadinessReport:
        config = config or {}
        settings = get_settings()
        if not config.get("model"):
            return ReadinessReport(False, "no model configured", ("model",))
        if not settings.openai_api_key:
            return ReadinessReport(
                False,
                "AGENTBENCH_OPENAI_API_KEY is not set, so this adapter cannot run",
                ("AGENTBENCH_OPENAI_API_KEY",),
            )
        return ReadinessReport(
            True, f"credentials present for {config.get('base_url') or settings.openai_base_url}"
        )

    # -- main loop ---------------------------------------------------------
    def run(self, ctx: AgentRunContext) -> AgentRunResult:
        settings = get_settings()
        api_key = settings.openai_api_key
        if not api_key:
            raise AgentAdapterError(
                "AGENTBENCH_OPENAI_API_KEY is not configured; refusing to start a model run"
            )

        model: str = self.config["model"]
        base_url: str = (self.config.get("base_url") or settings.openai_base_url).rstrip("/")
        tools = [
            tool
            for tool in TOOLS
            if tool["function"]["name"] != "run_command"
            or self.config.get("allow_run_command", True)
        ]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _render_user_message(ctx.brief)},
        ]
        usage = TokenUsage()
        steps = 0
        compactions = 0
        finished_summary: str | None = None

        ctx.events.record(
            EventType.AGENT_START,
            {
                "adapter": "openai_tools",
                "model": model,
                "base_url": base_url,
                "max_steps": ctx.max_steps,
                "tools": [tool["function"]["name"] for tool in tools],
                "sampling": ctx.sampling,
            },
        )
        ctx.events.record(
            EventType.TASK_CONTEXT_SUPPLIED,
            {
                "transport": "chat_messages",
                "brief_keys": sorted(ctx.brief),
                "hints_included": "hints" in ctx.brief,
                "prompt_chars": len(messages[1]["content"]),
            },
        )

        with httpx.Client(timeout=httpx.Timeout(REQUEST_TIMEOUT_CEILING_S)) as client:
            while steps < ctx.max_steps:
                if ctx.is_cancelled():
                    return self._cancelled_result(steps)
                if ctx.expired:
                    return self._timeout_result(steps)

                if len(messages) > MAX_MESSAGES_BEFORE_COMPACTION:
                    messages, dropped = _compact(messages)
                    compactions += 1
                    ctx.events.record(
                        EventType.CONTEXT_COMPACTION,
                        {
                            "performed_by": "adapter",
                            "dropped_messages": dropped,
                            "remaining_messages": len(messages),
                            "strategy": "drop oldest tool results, keep system+task+recent turns",
                        },
                    )

                response, call_usage = self._chat(
                    client, base_url, api_key, model, messages, tools, ctx
                )
                usage = _merge_usage(usage, call_usage)

                assistant_text, tool_calls, finish_reason = _extract_turn(response)

                ctx.events.record(
                    EventType.MODEL_RESPONSE,
                    {
                        "model": response.get("model", model),
                        "finish_reason": finish_reason,
                        "assistant_text": assistant_text[:4000],
                        "tool_call_count": len(tool_calls),
                        "tool_names": [tc.get("function", {}).get("name") for tc in tool_calls],
                        "usage": call_usage.to_dict(),
                        "note": (
                            "provider-visible output only; hidden model reasoning is not "
                            "observable to this platform"
                        ),
                    },
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_text,
                        **({"tool_calls": tool_calls} if tool_calls else {}),
                    }
                )

                if not tool_calls:
                    # No tool call and no `finish`: the model stopped talking.
                    ctx.events.record(
                        EventType.AGENT_MESSAGE,
                        {"text": assistant_text[:4000], "note": "model produced no tool call"},
                    )
                    finished_summary = assistant_text or "model stopped without calling finish"
                    break

                stop = False
                for tool_call in tool_calls:
                    steps += 1
                    name = tool_call.get("function", {}).get("name", "")
                    raw_args = tool_call.get("function", {}).get("arguments") or "{}"
                    tool_output: str | None = None
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except json.JSONDecodeError as exc:
                        args = {}
                        tool_output = f"ERROR: could not parse tool arguments as JSON: {exc}"

                    ctx.events.record(
                        EventType.TOOL_CALL,
                        {"tool": name, "arguments": _trim_args(args), "step": steps},
                    )

                    if name == "finish":
                        finished_summary = str(args.get("summary", ""))[:4000]
                        ctx.events.record(
                            EventType.TOOL_RESULT,
                            {"tool": name, "ok": True, "summary": finished_summary},
                        )
                        stop = True
                        break

                    if tool_output is None:
                        tool_output = self._dispatch(ctx, name, args)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", f"call_{steps}"),
                            "content": tool_output[:MAX_TOOL_RESULT_CHARS],
                        }
                    )
                    ctx.events.record(
                        EventType.TOOL_RESULT,
                        {
                            "tool": name,
                            "ok": not tool_output.startswith("ERROR:"),
                            "result_chars": len(tool_output),
                            "result_preview": tool_output[:2000],
                        },
                    )

                    if steps >= ctx.max_steps:
                        break
                if stop:
                    break

        reached_cap = finished_summary is None and steps >= ctx.max_steps
        cost, cost_source = _cost_for(model, usage)

        ctx.events.record(
            EventType.AGENT_END,
            {
                "steps": steps,
                "compactions": compactions,
                "finished_explicitly": finished_summary is not None,
                "hit_step_cap": reached_cap,
                "usage": usage.to_dict(),
            },
        )

        return AgentRunResult(
            status=AgentStatus.COMPLETED,
            reason=(
                f"step cap of {ctx.max_steps} reached without an explicit finish"
                if reached_cap
                else (finished_summary or "agent finished")
            ),
            steps=steps,
            usage=usage,
            cost_usd=cost,
            cost_source=cost_source,
            model=model,
            provider=base_url,
            metadata={
                "compactions": compactions,
                "finished_explicitly": finished_summary is not None,
                "hit_step_cap": reached_cap,
            },
        )

    # -- provider ----------------------------------------------------------
    def _chat(
        self,
        client: httpx.Client,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        ctx: AgentRunContext,
    ) -> tuple[dict[str, Any], TokenUsage]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": int(self.config.get("max_tokens", 4096)),
        }
        for key in ("temperature", "top_p", "seed"):
            if key in ctx.sampling:
                payload[key] = ctx.sampling[key]
        if ctx.seed is not None and "seed" not in payload:
            payload["seed"] = ctx.seed

        retries = int(self.config.get("request_retries", 3))
        last_error = ""
        retry_after: float | None = None
        for attempt in range(retries + 1):
            if ctx.is_cancelled():
                raise AgentAdapterError("cancelled while contacting the model provider")
            budget = min(ctx.seconds_remaining, REQUEST_TIMEOUT_CEILING_S)
            if budget <= 1:
                raise AgentAdapterError("no time budget left to contact the model provider")

            ctx.events.record(
                EventType.MODEL_REQUEST,
                {
                    "model": model,
                    "message_count": len(messages),
                    "tool_count": len(tools),
                    "attempt": attempt + 1,
                    "max_tokens": payload["max_tokens"],
                    "sampling": {
                        k: v for k, v in payload.items() if k in ("temperature", "top_p", "seed")
                    },
                },
            )
            try:
                response = client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=httpx.Timeout(budget),
                )
            except httpx.HTTPError as exc:
                last_error = f"transport error: {type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        # A 200 with a non-JSON body is a provider/proxy fault, not
                        # an agent failure, and is worth retrying.
                        last_error = f"malformed JSON in a 200 response: {exc}"
                        data = None
                    else:
                        if not isinstance(data, dict):
                            last_error = (
                                "provider returned JSON that is not an object: "
                                f"{type(data).__name__}"
                            )
                        elif data.get("error"):
                            # Some gateways report errors with HTTP 200.
                            raise AgentAdapterError(
                                f"model provider returned an error payload: "
                                f"{str(data['error'])[:600]}"
                            )
                        else:
                            return data, _usage_from_response(data)
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:600]}"
                    if response.status_code not in RETRYABLE_STATUS:
                        raise AgentAdapterError(
                            f"model provider rejected the request: {last_error}"
                        )
                    retry_after = _retry_after_seconds(response)

            if attempt < retries:
                # Honour Retry-After when the provider sends one; it is the only
                # rate-limit signal that reflects the provider's actual window.
                backoff = retry_after if retry_after is not None else 2.0 * (2**attempt)
                delay = min(backoff, max(0.0, ctx.seconds_remaining - 1))
                ctx.events.record(
                    EventType.RETRY,
                    {
                        "scope": "model_request",
                        "attempt": attempt + 1,
                        "of": retries,
                        "delay_s": round(delay, 2),
                        "retry_after_header_s": retry_after,
                        "error": last_error[:600],
                    },
                )
                if delay > 0:
                    time.sleep(delay)
                retry_after = None

        raise AgentAdapterError(
            f"model provider unavailable after {retries + 1} attempts: {last_error}"
        )

    # -- tools -------------------------------------------------------------
    def _dispatch(self, ctx: AgentRunContext, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "list_files":
                return self._tool_list(
                    ctx, str(args.get("path", ".")), int(args.get("max_depth", 3))
                )
            if name == "read_file":
                return self._tool_read(ctx, str(args.get("path", "")))
            if name == "write_file":
                return self._tool_write(
                    ctx, str(args.get("path", "")), str(args.get("content", ""))
                )
            if name == "run_command":
                return self._tool_run(ctx, str(args.get("command", "")), args.get("timeout_s"))
        except SandboxError:
            raise
        except ValueError as exc:
            return f"ERROR: {exc}"
        return f"ERROR: unknown tool '{name}'"

    @staticmethod
    def _safe_rel(path: str) -> str:
        cleaned = path.strip().lstrip("/")
        parts = [p for p in cleaned.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError(f"path '{path}' escapes the repository root; refused")
        return "/".join(parts) or "."

    def _tool_list(self, ctx: AgentRunContext, path: str, max_depth: int) -> str:
        rel = self._safe_rel(path)
        depth = max(1, min(max_depth, 6))
        command = (
            f"find {sh_quote(rel)} -maxdepth {depth} "
            "-not -path '*/.git/*' -not -path '*/__pycache__/*' "
            "-not -path '*/.pytest_cache/*' | sort | head -400"
        )
        result = ctx.sandbox.exec(command, timeout_s=60, workdir=ctx.workdir)
        if not result.ok:
            return f"ERROR: could not list '{rel}': {(result.stderr or result.stdout)[:1000]}"
        return result.stdout or "(no entries)"

    def _tool_read(self, ctx: AgentRunContext, path: str) -> str:
        rel = self._safe_rel(path)
        try:
            content = ctx.sandbox.read_file(f"{ctx.workdir}/{rel}", max_bytes=MAX_READ_CHARS)
        except FileNotFoundError:
            return f"ERROR: file not found: {rel}"
        ctx.events.record(
            EventType.FILE_READ, {"path": rel, "chars": len(content), "tool": "read_file"}
        )
        numbered = "\n".join(
            f"{i:>5}| {line}" for i, line in enumerate(content.splitlines(), start=1)
        )
        return numbered or "(empty file)"

    def _tool_write(self, ctx: AgentRunContext, path: str, content: str) -> str:
        rel = self._safe_rel(path)
        if rel == ".":
            return "ERROR: a file path is required"
        ctx.sandbox.write_file(f"{ctx.workdir}/{rel}", content)
        ctx.events.record(
            EventType.FILE_WRITE,
            {
                "path": rel,
                "bytes": len(content.encode()),
                "lines": content.count("\n") + 1,
                "tool": "write_file",
            },
        )
        return f"wrote {len(content.encode())} bytes to {rel}"

    def _tool_run(self, ctx: AgentRunContext, command: str, timeout_s: Any) -> str:
        if not command.strip():
            return "ERROR: a command is required"
        try:
            budget = int(timeout_s) if timeout_s is not None else 60
        except (TypeError, ValueError):
            budget = 60
        budget = max(1, min(budget, 300, int(ctx.seconds_remaining) or 1))
        result = ctx.sandbox.exec(command, timeout_s=budget, workdir=ctx.workdir)
        ctx.events.command(result, phase="agent")
        header = f"exit_code={result.exit_code} timed_out={result.timed_out}"
        body = f"{result.stdout}\n{result.stderr}".strip()
        return f"{header}\n{body[:MAX_TOOL_RESULT_CHARS]}"


# --------------------------------------------------------------------------- #
def _render_user_message(brief: dict[str, Any]) -> str:
    lines = [f"# Task: {brief.get('title', '')}", "", brief.get("problem_statement", ""), ""]
    if brief.get("relevant_files"):
        lines += ["## Possibly relevant files", *(f"- {p}" for p in brief["relevant_files"]), ""]
    if brief.get("acceptance_criteria"):
        lines += ["## Acceptance criteria", *(f"- {c}" for c in brief["acceptance_criteria"]), ""]
    if brief.get("forbidden_paths"):
        lines += [
            "## You must NOT modify these paths",
            *(f"- {p}" for p in brief["forbidden_paths"]),
            "",
        ]
    if brief.get("hints"):
        lines += ["## Hints", *(f"- {h}" for h in brief["hints"]), ""]
    lines += [f"Repository root: {brief.get('workdir', '/workspace/repo')}"]
    return "\n".join(lines)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse ``Retry-After``, which may be seconds or an HTTP date."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    from datetime import UTC, datetime

    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - datetime.now(UTC)).total_seconds())


def _extract_turn(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
    """Pull assistant text, tool calls and finish reason out of a chat response.

    Written defensively because a response that merely *parses* as JSON is not
    necessarily shaped like the OpenAI schema: gateways and local servers return
    ``choices: null``, string choices, ``content`` as a list of blocks, and tool
    call entries that are not objects. Any of those used to raise an
    ``AttributeError`` deep in the loop, which the orchestrator then recorded as
    an infrastructure failure rather than the provider-contract violation it is.
    """
    choices = response.get("choices")
    choice: dict[str, Any] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]

    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}

    content = message.get("content")
    if isinstance(content, list):
        # Anthropic-style content blocks served over an OpenAI-shaped endpoint.
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        ]
        assistant_text = "".join(parts)
    elif isinstance(content, str):
        assistant_text = content
    else:
        assistant_text = ""

    raw_calls = message.get("tool_calls")
    tool_calls = [
        call
        for call in (raw_calls if isinstance(raw_calls, list) else [])
        if isinstance(call, dict) and isinstance(call.get("function"), dict)
    ]

    finish_reason = choice.get("finish_reason")
    return assistant_text, tool_calls, finish_reason if isinstance(finish_reason, str) else None


def _usage_from_response(data: dict[str, Any]) -> TokenUsage:
    raw = data.get("usage")
    if not isinstance(raw, dict):
        return TokenUsage(requests=1)
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    completion_details = raw.get("completion_tokens_details")
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    return TokenUsage(
        input_tokens=_int_or_none(raw.get("prompt_tokens")),
        output_tokens=_int_or_none(raw.get("completion_tokens")),
        cached_input_tokens=_int_or_none(details.get("cached_tokens")),
        reasoning_tokens=_int_or_none(completion_details.get("reasoning_tokens")),
        requests=1,
    )


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _merge_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    def add(a: int | None, b: int | None) -> int | None:
        if a is None and b is None:
            return None
        return (a or 0) + (b or 0)

    return TokenUsage(
        input_tokens=add(left.input_tokens, right.input_tokens),
        output_tokens=add(left.output_tokens, right.output_tokens),
        cached_input_tokens=add(left.cached_input_tokens, right.cached_input_tokens),
        reasoning_tokens=add(left.reasoning_tokens, right.reasoning_tokens),
        requests=left.requests + right.requests,
    )


def _cost_for(model: str, usage: TokenUsage) -> tuple[float | None, str | None]:
    cost = estimate_cost_usd(model, usage.input_tokens, usage.output_tokens)
    if cost is None:
        return None, None
    return cost, f"estimated:{PRICE_TABLE_ID}"


def _compact(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop the oldest tool results, keeping the system prompt, the task message
    and the most recent turns."""
    keep_head = messages[:2]
    tail = messages[2:]
    keep_tail = tail[-24:]
    dropped = len(tail) - len(keep_tail)
    if dropped <= 0:
        return messages, 0
    notice = {
        "role": "user",
        "content": (
            f"[harness notice] {dropped} earlier tool messages were removed to fit the "
            "context window. Re-read any file you still need."
        ),
    }
    return [*keep_head, notice, *keep_tail], dropped


def _trim_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 2000:
            out[key] = value[:2000] + f"...[{len(value) - 2000} chars omitted]"
        else:
            out[key] = value
    return out
