"""Provider-contract tests for ``openai_tools`` against a real local HTTP server.

What these are, precisely
-------------------------
These are **offline adapter tests**, not live-model runs. A
``http.server.ThreadingHTTPServer`` on loopback plays the role of an
OpenAI-compatible endpoint and returns scripted responses. The adapter's real
``httpx`` client, real retry loop, real tool dispatch and real sandbox
interaction all execute; only the model's intelligence is absent.

They exist because the failure modes that matter in production - rate limits,
truncated bodies, gateways that report errors with HTTP 200, tool calls with
malformed arguments - cannot be reached by a live-model test and must not be
left to a mock that stubs out the HTTP layer.

A genuine model run is a separate thing and is covered by
``TestOpenAIToolsAdapterLive`` in ``test_real_adapters.py``, which skips without
credentials. Nothing here is ever reported as a model benchmark result.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.adapters.base import AgentStatus
from agentbench.adapters.openai_tools import (
    _extract_turn,
    _retry_after_seconds,
    _usage_from_response,
)
from agentbench.db.models import RunRecord
from agentbench.orchestrator import experiment as experiments
from agentbench.orchestrator.runner import RunExecutor
from agentbench.orchestrator.worker import claim_next_run
from agentbench.registry.agents import register_agent
from agentbench.schemas.enums import EventType, RunOutcome
from agentbench.trajectory.recorder import read_events

pytestmark = pytest.mark.usefixtures("local_sandbox_enabled")


# --------------------------------------------------------------------------- #
# a scriptable OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #
class FakeProvider:
    """Serves a queue of scripted responses and records what it received."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []
        self._index = 0
        self._lock = threading.Lock()

    def next_response(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.requests.append(body)
            step = self.script[min(self._index, len(self.script) - 1)]
            self._index += 1
            return step

    @property
    def call_count(self) -> int:
        return len(self.requests)


def _handler_for(provider: FakeProvider) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) or b"{}"
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}

            step = provider.next_response(body)

            if step.get("sleep"):
                time.sleep(float(step["sleep"]))

            status = int(step.get("status", 200))
            payload = step.get("json")
            text = step.get("text")
            encoded = json.dumps(payload).encode() if payload is not None else (text or "").encode()

            self.send_response(status)
            self.send_header("Content-Type", step.get("content_type", "application/json"))
            for key, value in (step.get("headers") or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args: Any) -> None:
            return

    return Handler


@pytest.fixture
def provider_factory() -> Iterator[Any]:
    servers: list[ThreadingHTTPServer] = []

    def start(script: list[dict[str, Any]]) -> tuple[FakeProvider, str]:
        provider = FakeProvider(script)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(provider))
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address[0], server.server_address[1]
        return provider, f"http://{host}:{port}/v1"

    yield start

    for server in servers:
        server.shutdown()
        server.server_close()


def tool_call(name: str, arguments: Any, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }


def chat_response(
    *, tool_calls: list[dict[str, Any]] | None = None, content: Any = "", usage: Any = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "model": "fake-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


# --------------------------------------------------------------------------- #
def run_with_provider(
    db: Session,
    task_key: str,
    base_url: str,
    *,
    name: str,
    config_extra: dict[str, Any] | None = None,
    limits_override: dict[str, Any] | None = None,
) -> RunRecord:
    agent = register_agent(
        db,
        name=name,
        adapter="openai_tools",
        model="fake-model",
        config={
            "model": "fake-model",
            "base_url": base_url,
            "max_tokens": 256,
            "request_retries": 2,
            **(config_extra or {}),
        },
    )
    experiment = experiments.create_experiment(
        db,
        name=name,
        agent_config_id=agent.id,
        task_keys=[task_key],
        limits_override=limits_override or {},
    )
    experiments.launch(db, experiment.id)
    db.commit()

    run_id = claim_next_run(db, "provider-worker")
    assert run_id is not None
    RunExecutor(run_id, "provider-worker").execute()
    db.expire_all()
    return db.execute(select(RunRecord).where(RunRecord.id == run_id)).scalars().one()


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentbench.config import reset_settings_cache

    monkeypatch.setenv("AGENTBENCH_OPENAI_API_KEY", "sk-test-not-a-real-key-000000000000")
    reset_settings_cache()


# --------------------------------------------------------------------------- #
class TestHappyPath:
    def test_the_adapter_solves_a_task_through_a_real_http_round_trip(
        self,
        db: Session,
        seeded_task: Any,
        provider_factory: Any,
        with_key: None,
        fixed_calc_source: str,
    ) -> None:
        provider, base_url = provider_factory(
            [
                {
                    "json": chat_response(
                        tool_calls=[
                            tool_call(
                                "write_file", {"path": "calc.py", "content": fixed_calc_source}
                            )
                        ],
                        usage={"prompt_tokens": 800, "completion_tokens": 120},
                    )
                },
                {
                    "json": chat_response(
                        tool_calls=[tool_call("finish", {"summary": "fixed add"}, "call_2")],
                        usage={"prompt_tokens": 900, "completion_tokens": 20},
                    )
                },
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-happy")

        assert run.outcome == RunOutcome.SOLVED
        assert run.grading.passed is True
        assert "calc.py" in run.patch
        assert provider.call_count == 2

    def test_provider_reported_usage_and_cost_provenance_are_persisted(
        self,
        db: Session,
        seeded_task: Any,
        provider_factory: Any,
        with_key: None,
        fixed_calc_source: str,
    ) -> None:
        _, base_url = provider_factory(
            [
                {
                    "json": chat_response(
                        tool_calls=[
                            tool_call(
                                "write_file", {"path": "calc.py", "content": fixed_calc_source}
                            )
                        ],
                        usage={"prompt_tokens": 1000, "completion_tokens": 200},
                    )
                },
                {
                    "json": chat_response(
                        tool_calls=[tool_call("finish", {"summary": "done"}, "call_2")],
                        usage={"prompt_tokens": 1100, "completion_tokens": 50},
                    )
                },
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-usage")

        assert run.usage["input_tokens"] == 2100
        assert run.usage["output_tokens"] == 250
        assert run.usage["reported_by_provider"] is True
        assert run.usage["requests"] == 2
        # "fake-model" is not in the price table, so cost must stay unknown
        # rather than being invented.
        assert run.cost_usd is None
        assert run.cost_source is None

    def test_the_request_carries_the_tools_and_the_task_but_not_the_hints(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        provider, base_url = provider_factory(
            [{"json": chat_response(tool_calls=[tool_call("finish", {"summary": "noop"})])}]
        )

        run_with_provider(db, seeded_task.key, base_url, name="prov-request")

        body = provider.requests[0]
        assert body["model"] == "fake-model"
        tool_names = {tool["function"]["name"] for tool in body["tools"]}
        assert {"read_file", "write_file", "list_files", "finish"} <= tool_names
        prompt = json.dumps(body["messages"])
        assert "calc.add returns left - right" in prompt
        assert "Change the minus sign" not in prompt

    def test_tool_events_are_recorded_with_arguments_and_results(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory(
            [
                {"json": chat_response(tool_calls=[tool_call("read_file", {"path": "calc.py"})])},
                {
                    "json": chat_response(
                        tool_calls=[tool_call("finish", {"summary": "read it"}, "call_2")]
                    )
                },
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-events")
        events = read_events(db, run.id, limit=500)
        types = [event.event_type for event in events]

        assert EventType.MODEL_REQUEST in types
        assert EventType.MODEL_RESPONSE in types
        assert EventType.TOOL_CALL in types
        assert EventType.TOOL_RESULT in types
        assert EventType.FILE_READ in types

        read_event = next(e for e in events if e.event_type == EventType.FILE_READ)
        assert read_event.payload["path"] == "calc.py"

    def test_no_api_key_appears_anywhere_in_the_event_stream(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory(
            [{"json": chat_response(tool_calls=[tool_call("finish", {"summary": "x"})])}]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-secret")
        serialized = json.dumps([event.payload for event in read_events(db, run.id, limit=500)])

        assert "sk-test-not-a-real-key-000000000000" not in serialized


class TestCredentialAndConfigValidation:
    def test_a_run_without_credentials_is_an_agent_error_not_a_false_unsolved(
        self, db: Session, seeded_task: Any, provider_factory: Any
    ) -> None:
        _, base_url = provider_factory([{"json": chat_response()}])

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-nokey")

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "AGENTBENCH_OPENAI_API_KEY" in (run.termination_reason or "")
        assert run.grading is None

    def test_readiness_reports_the_missing_credential_before_any_run(self) -> None:
        from agentbench.adapters.registry import readiness_for

        report = readiness_for("openai_tools", {"model": "fake-model"})
        assert report.ready is False
        assert "AGENTBENCH_OPENAI_API_KEY" in report.missing

    def test_a_config_without_a_model_is_rejected_at_registration(self, db: Session) -> None:
        from agentbench.adapters.base import AgentConfigError
        from agentbench.registry.agents import register_agent as register

        with pytest.raises(AgentConfigError, match="model"):
            register(db, name="prov-nomodel", adapter="openai_tools", config={})


class TestProviderErrors:
    def test_an_unreachable_endpoint_is_retried_then_reported_as_agent_error(
        self, db: Session, seeded_task: Any, with_key: None
    ) -> None:
        run = run_with_provider(
            db, seeded_task.key, "http://127.0.0.1:9/v1", name="prov-unreachable"
        )

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "unavailable after" in (run.termination_reason or "")
        retries = [
            event
            for event in read_events(db, run.id, limit=500)
            if event.event_type == EventType.RETRY and event.payload.get("scope") == "model_request"
        ]
        assert len(retries) == 2

    def test_a_non_retryable_status_fails_immediately_without_retrying(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        provider, base_url = provider_factory(
            [{"status": 401, "json": {"error": {"message": "invalid api key"}}}]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-401")

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "rejected the request" in (run.termination_reason or "")
        assert provider.call_count == 1

    def test_a_rate_limit_is_retried_and_then_succeeds(
        self,
        db: Session,
        seeded_task: Any,
        provider_factory: Any,
        with_key: None,
        fixed_calc_source: str,
    ) -> None:
        provider, base_url = provider_factory(
            [
                {
                    "status": 429,
                    "headers": {"Retry-After": "0"},
                    "json": {"error": {"message": "rate limited"}},
                },
                {
                    "json": chat_response(
                        tool_calls=[
                            tool_call(
                                "write_file", {"path": "calc.py", "content": fixed_calc_source}
                            )
                        ]
                    )
                },
                {"json": chat_response(tool_calls=[tool_call("finish", {"summary": "ok"}, "c2")])},
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-429")

        assert run.outcome == RunOutcome.SOLVED
        assert provider.call_count == 3
        retry = next(
            event
            for event in read_events(db, run.id, limit=500)
            if event.event_type == EventType.RETRY
        )
        assert retry.payload["retry_after_header_s"] == 0.0
        assert "429" in retry.payload["error"]

    def test_a_server_error_is_retried(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        provider, base_url = provider_factory([{"status": 503, "json": {"error": "upstream down"}}])

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-503")

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert provider.call_count == 3

    def test_an_error_payload_served_with_http_200_is_not_silently_accepted(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        """Some gateways do this; treating it as a normal turn would record an
        empty agent response and a misleading UNSOLVED."""
        provider, base_url = provider_factory(
            [{"status": 200, "json": {"error": {"message": "context length exceeded"}}}]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-200err")

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "error payload" in (run.termination_reason or "")
        assert provider.call_count == 1


class TestMalformedResponses:
    def test_a_non_json_body_with_http_200_is_retried_not_crashed(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        provider, base_url = provider_factory(
            [{"status": 200, "text": "<html>proxy error</html>", "content_type": "text/html"}]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-nonjson")

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "malformed JSON" in (run.termination_reason or "")
        assert provider.call_count == 3

    def test_json_that_is_not_an_object_is_retried(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory([{"status": 200, "json": ["not", "an", "object"]}])

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-notobject")

        assert run.outcome == RunOutcome.AGENT_ERROR
        assert "not an object" in (run.termination_reason or "")

    def test_an_empty_choices_array_ends_the_turn_without_crashing(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory([{"json": {"model": "fake-model", "choices": []}}])

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-nochoices")

        assert run.outcome == RunOutcome.UNSOLVED
        assert run.grading is not None

    def test_malformed_tool_arguments_are_reported_back_to_the_model(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory(
            [
                {"json": chat_response(tool_calls=[tool_call("write_file", "{not valid json")])},
                {
                    "json": chat_response(
                        tool_calls=[tool_call("finish", {"summary": "gave up"}, "c2")]
                    )
                },
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-badargs")

        results = [
            event
            for event in read_events(db, run.id, limit=500)
            if event.event_type == EventType.TOOL_RESULT
        ]
        assert any(
            "could not parse tool arguments" in str(event.payload.get("result_preview", ""))
            for event in results
        )
        assert run.outcome == RunOutcome.UNSOLVED

    def test_an_unknown_tool_name_is_refused_without_ending_the_run(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory(
            [
                {"json": chat_response(tool_calls=[tool_call("delete_everything", {})])},
                {
                    "json": chat_response(
                        tool_calls=[tool_call("finish", {"summary": "oops"}, "c2")]
                    )
                },
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-badtool")

        results = [
            event
            for event in read_events(db, run.id, limit=500)
            if event.event_type == EventType.TOOL_RESULT
        ]
        assert any(
            "unknown tool" in str(event.payload.get("result_preview", "")) for event in results
        )
        assert run.outcome == RunOutcome.UNSOLVED

    def test_a_path_escape_attempt_is_refused_and_recorded(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory(
            [
                {
                    "json": chat_response(
                        tool_calls=[
                            tool_call("write_file", {"path": "../../escape.py", "content": "x"})
                        ]
                    )
                },
                {"json": chat_response(tool_calls=[tool_call("finish", {"summary": "no"}, "c2")])},
            ]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-escape")

        results = [
            event
            for event in read_events(db, run.id, limit=500)
            if event.event_type == EventType.TOOL_RESULT
        ]
        assert any(
            "escapes the repository" in str(event.payload.get("result_preview", ""))
            for event in results
        )
        assert "escape.py" not in (run.patch or "")


class TestBudgets:
    def test_a_provider_slower_than_the_agent_budget_is_cut_off(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        """The HTTP read timeout is derived from the remaining agent budget, so a
        provider that stalls past it must abort the run rather than hang."""
        _, base_url = provider_factory([{"sleep": 30, "json": chat_response()}])

        run = run_with_provider(
            db,
            seeded_task.key,
            base_url,
            name="prov-slow",
            config_extra={"request_retries": 0},
            limits_override={"agent_timeout_s": 3},
        )

        assert run.outcome in (RunOutcome.TIMEOUT, RunOutcome.AGENT_ERROR)
        assert run.grading is None
        assert float(run.timings.get("agent_s", 0)) < 25

    def test_the_step_cap_stops_a_model_that_never_calls_finish(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        _, base_url = provider_factory(
            [{"json": chat_response(tool_calls=[tool_call("list_files", {"path": "."})])}]
        )

        run = run_with_provider(db, seeded_task.key, base_url, name="prov-stepcap")

        assert run.outcome == RunOutcome.UNSOLVED
        end = next(
            event
            for event in read_events(db, run.id, limit=2000)
            if event.event_type == EventType.AGENT_END
        )
        assert end.payload["hit_step_cap"] is True
        assert end.payload["steps"] == 10


class TestResponseParsingUnits:
    def test_content_blocks_are_flattened(self) -> None:
        text, calls, finish = _extract_turn(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "hello "},
                                {"type": "text", "text": "world"},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        assert text == "hello world"
        assert calls == []
        assert finish == "stop"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"choices": None},
            {"choices": []},
            {"choices": ["not a dict"]},
            {"choices": [{"message": "not a dict"}]},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"tool_calls": "not a list"}}]},
            {"choices": [{"message": {"tool_calls": ["not a dict"]}}]},
            {"choices": [{"message": {"tool_calls": [{"function": "not a dict"}]}}]},
        ],
    )
    def test_malformed_shapes_degrade_instead_of_raising(self, payload: dict[str, Any]) -> None:
        text, calls, _finish = _extract_turn(payload)
        assert isinstance(text, str)
        assert calls == []

    def test_well_formed_tool_calls_survive(self) -> None:
        _text, calls, _finish = _extract_turn(
            {"choices": [{"message": {"tool_calls": [tool_call("read_file", {"path": "a.py"})]}}]}
        )
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"

    def test_non_dict_usage_is_treated_as_unreported(self) -> None:
        usage = _usage_from_response({"usage": "lots"})
        assert usage.input_tokens is None
        assert usage.to_dict()["reported_by_provider"] is False

    def test_retry_after_accepts_seconds(self) -> None:
        import httpx

        response = httpx.Response(429, headers={"Retry-After": "2.5"})
        assert _retry_after_seconds(response) == 2.5

    def test_retry_after_accepts_an_http_date(self) -> None:
        import httpx

        response = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})
        value = _retry_after_seconds(response)
        assert value is not None and value > 0

    def test_retry_after_absent_or_garbage_is_none(self) -> None:
        import httpx

        assert _retry_after_seconds(httpx.Response(429)) is None
        assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": "soon"})) is None


class TestCancellation:
    def test_cancelling_mid_flight_stops_the_model_loop(
        self, db: Session, seeded_task: Any, provider_factory: Any, with_key: None
    ) -> None:
        from agentbench.adapters.base import AgentRunContext
        from agentbench.adapters.registry import build_adapter

        _, base_url = provider_factory([{"json": chat_response()}])
        adapter = build_adapter("openai_tools", {"model": "fake-model", "base_url": base_url})

        class _Sandbox:
            def exec(self, *_a: Any, **_k: Any) -> Any:
                raise AssertionError("no tool should run after cancellation")

        class _Events:
            def record(self, *_a: Any, **_k: Any) -> None:
                return None

            def command(self, *_a: Any, **_k: Any) -> None:
                return None

        ctx = AgentRunContext(
            run_id="r",
            sandbox=_Sandbox(),  # type: ignore[arg-type]
            task=None,  # type: ignore[arg-type]
            brief={"title": "t", "problem_statement": "p"},
            workdir="/workspace/repo",
            events=_Events(),  # type: ignore[arg-type]
            deadline=time.monotonic() + 60,
            max_steps=5,
            is_cancelled=lambda: True,
        )

        result = adapter.run(ctx)
        assert result.status is AgentStatus.CANCELLED

    def test_an_expired_budget_returns_timeout_rather_than_contacting_the_provider(
        self, db: Session, provider_factory: Any, with_key: None
    ) -> None:
        from agentbench.adapters.base import AgentRunContext
        from agentbench.adapters.registry import build_adapter

        provider, base_url = provider_factory([{"json": chat_response()}])
        adapter = build_adapter("openai_tools", {"model": "fake-model", "base_url": base_url})

        class _Events:
            def record(self, *_a: Any, **_k: Any) -> None:
                return None

            def command(self, *_a: Any, **_k: Any) -> None:
                return None

        ctx = AgentRunContext(
            run_id="r",
            sandbox=None,  # type: ignore[arg-type]
            task=None,  # type: ignore[arg-type]
            brief={},
            workdir="/workspace/repo",
            events=_Events(),  # type: ignore[arg-type]
            deadline=time.monotonic() - 1,
            max_steps=5,
        )

        result = adapter.run(ctx)

        assert result.status is AgentStatus.TIMEOUT
        assert provider.call_count == 0
