"""Adapter contract conformance, configuration validation and cost reporting."""

from __future__ import annotations

import inspect

import pytest

from agentbench.adapters.base import (
    ADAPTER_CONTRACT_VERSION,
    AgentAdapter,
    AgentAdapterError,
    AgentConfigError,
    AgentRunResult,
    AgentStatus,
    TokenUsage,
)
from agentbench.adapters.pricing import (
    PRICE_TABLE_ID,
    estimate_cost_usd,
    known_models,
    lookup_prices,
)
from agentbench.adapters.registry import (
    UnknownAdapter,
    adapter_class,
    adapter_keys,
    build_adapter,
    capabilities_for,
    list_capabilities,
    readiness_for,
    validate_agent_config,
)

ALL_ADAPTERS = adapter_keys()


class TestAdapterContract:
    def test_registry_is_not_empty(self) -> None:
        assert ALL_ADAPTERS

    @pytest.mark.parametrize("key", ALL_ADAPTERS)
    def test_every_adapter_subclasses_the_base(self, key: str) -> None:
        assert issubclass(adapter_class(key), AgentAdapter)

    @pytest.mark.parametrize("key", ALL_ADAPTERS)
    def test_every_adapter_declares_matching_capabilities(self, key: str) -> None:
        capabilities = capabilities_for(key)
        assert capabilities.key == key
        assert capabilities.display_name
        assert capabilities.description
        assert capabilities.to_dict()["contract_version"] == ADAPTER_CONTRACT_VERSION

    @pytest.mark.parametrize("key", ALL_ADAPTERS)
    def test_every_adapter_implements_run_and_readiness(self, key: str) -> None:
        cls = adapter_class(key)
        assert "run" in cls.__dict__ or any("run" in base.__dict__ for base in cls.__mro__[1:-1])
        report = cls.readiness({})
        assert isinstance(report.ready, bool)
        assert report.detail

    @pytest.mark.parametrize("key", ALL_ADAPTERS)
    def test_run_signature_takes_only_a_context(self, key: str) -> None:
        signature = inspect.signature(adapter_class(key).run)
        assert list(signature.parameters) == ["self", "ctx"]

    @pytest.mark.parametrize("key", ALL_ADAPTERS)
    def test_adapter_requiring_credentials_is_not_ready_without_them(self, key: str) -> None:
        capabilities = capabilities_for(key)
        if not capabilities.requires_credentials:
            pytest.skip("adapter needs no credentials")
        report = readiness_for(key, {"model": "gpt-4o-mini"})
        assert report.ready is False
        assert report.missing

    def test_unknown_adapter_raises_with_a_helpful_message(self) -> None:
        with pytest.raises(UnknownAdapter) as exc:
            adapter_class("does-not-exist")
        assert "available" in str(exc.value)

    def test_simulated_adapters_are_flagged(self) -> None:
        simulated = {c.key for c in list_capabilities() if c.is_simulated}
        assert {"mock", "scripted"} <= simulated
        assert "openai_tools" not in simulated


class TestConfigValidation:
    def test_mock_rejects_an_unknown_behaviour(self) -> None:
        with pytest.raises(AgentConfigError) as exc:
            build_adapter("mock", {"behaviour": "teleport"})
        assert "behaviour" in str(exc.value)

    def test_mock_write_files_requires_files(self) -> None:
        with pytest.raises(AgentConfigError):
            build_adapter("mock", {"behaviour": "write_files", "files": {}})

    def test_mock_accepts_every_declared_behaviour(self) -> None:
        from agentbench.adapters.builtin import MockAgentAdapter

        for behaviour in MockAgentAdapter.BEHAVIOURS:
            config = {"behaviour": behaviour}
            if behaviour == "write_files":
                config["files"] = {"a.py": "x = 1\n"}
            build_adapter("mock", config)

    def test_scripted_requires_task_actions(self) -> None:
        with pytest.raises(AgentConfigError) as exc:
            build_adapter("scripted", {})
        assert "tasks" in str(exc.value)

    def test_scripted_rejects_an_action_with_no_effect(self) -> None:
        with pytest.raises(AgentConfigError) as exc:
            build_adapter("scripted", {"tasks": {"t": {}}})
        assert "neither files nor commands" in str(exc.value)

    def test_shell_command_requires_a_command(self) -> None:
        with pytest.raises(AgentConfigError) as exc:
            build_adapter("shell_command", {})
        assert "command" in str(exc.value)

    def test_openai_tools_requires_a_model(self) -> None:
        with pytest.raises(AgentConfigError) as exc:
            build_adapter("openai_tools", {})
        assert "model" in str(exc.value)

    def test_openai_tools_rejects_an_absurd_retry_count(self) -> None:
        with pytest.raises(AgentConfigError):
            build_adapter("openai_tools", {"model": "gpt-4o-mini", "request_retries": 99})

    def test_validate_agent_config_reports_unknown_adapters_as_config_errors(self) -> None:
        with pytest.raises(AgentConfigError):
            validate_agent_config("nope", {})


class TestResultContract:
    def test_cost_without_a_source_is_rejected(self) -> None:
        with pytest.raises(AgentAdapterError) as exc:
            AgentRunResult(AgentStatus.COMPLETED, cost_usd=0.5)
        assert "cost_source" in str(exc.value)

    def test_cost_with_a_source_is_accepted(self) -> None:
        result = AgentRunResult(
            AgentStatus.COMPLETED, cost_usd=0.5, cost_source="provider_reported"
        )
        assert result.to_dict()["cost_source"] == "provider_reported"

    def test_unreported_usage_stays_none_rather_than_zero(self) -> None:
        payload = TokenUsage().to_dict()
        assert payload["input_tokens"] is None
        assert payload["total_tokens"] is None
        assert payload["reported_by_provider"] is False

    def test_reported_usage_is_marked_as_provider_reported(self) -> None:
        payload = TokenUsage(input_tokens=10, output_tokens=5).to_dict()
        assert payload["total_tokens"] == 15
        assert payload["reported_by_provider"] is True

    def test_timeout_and_error_statuses_are_not_ok(self) -> None:
        assert not AgentRunResult(AgentStatus.TIMEOUT).ok
        assert not AgentRunResult(AgentStatus.ERROR).ok
        assert AgentRunResult(AgentStatus.COMPLETED).ok
        assert AgentRunResult(AgentStatus.ABSTAINED).ok


class TestPricing:
    def test_known_model_prefix_resolves(self) -> None:
        assert lookup_prices("gpt-4o-mini-2024-07-18") is not None

    def test_provider_namespace_is_stripped(self) -> None:
        assert lookup_prices("openai/gpt-4o-mini") == lookup_prices("gpt-4o-mini")

    def test_longest_prefix_wins(self) -> None:
        assert lookup_prices("gpt-4o-mini") != lookup_prices("gpt-4o")

    def test_unknown_model_returns_none_rather_than_a_guess(self) -> None:
        assert lookup_prices("totally-made-up-model") is None
        assert estimate_cost_usd("totally-made-up-model", 1000, 1000) is None

    def test_unreported_usage_yields_no_cost(self) -> None:
        assert estimate_cost_usd("gpt-4o-mini", None, None) is None

    def test_cost_matches_the_table_arithmetic(self) -> None:
        input_price, output_price = lookup_prices("gpt-4o-mini")
        expected = input_price + output_price
        assert estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(expected)

    def test_price_table_is_dated_for_provenance(self) -> None:
        assert "@" in PRICE_TABLE_ID
        assert known_models()
