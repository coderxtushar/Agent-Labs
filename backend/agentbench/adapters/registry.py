"""Adapter registry.

Adding an agent integration means implementing
:class:`agentbench.adapters.base.AgentAdapter` and registering it here.  Nothing
in the orchestrator, grading engine or API knows the concrete adapter types.
"""

from __future__ import annotations

from typing import Any

from agentbench.adapters.base import (
    AdapterCapabilities,
    AgentAdapter,
    AgentConfigError,
    ReadinessReport,
)
from agentbench.adapters.builtin import MockAgentAdapter, ScriptedAgentAdapter
from agentbench.adapters.openai_tools import OpenAIToolsAdapter
from agentbench.adapters.shell_command import ShellCommandAdapter

_ADAPTERS: dict[str, type[AgentAdapter]] = {
    MockAgentAdapter.capabilities.key: MockAgentAdapter,
    ScriptedAgentAdapter.capabilities.key: ScriptedAgentAdapter,
    ShellCommandAdapter.capabilities.key: ShellCommandAdapter,
    OpenAIToolsAdapter.capabilities.key: OpenAIToolsAdapter,
}


class UnknownAdapter(KeyError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        return f"unknown adapter '{self.key}'; available: {', '.join(sorted(_ADAPTERS))}"


def adapter_class(key: str) -> type[AgentAdapter]:
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        raise UnknownAdapter(key) from exc


def build_adapter(key: str, config: dict[str, Any] | None = None) -> AgentAdapter:
    """Instantiate an adapter, validating its configuration eagerly."""
    return adapter_class(key)(config or {})


def list_capabilities() -> list[AdapterCapabilities]:
    return [cls.capabilities for cls in _ADAPTERS.values()]


def capabilities_for(key: str) -> AdapterCapabilities:
    return adapter_class(key).capabilities


def readiness_for(key: str, config: dict[str, Any] | None = None) -> ReadinessReport:
    return adapter_class(key).readiness(config or {})


def validate_agent_config(key: str, config: dict[str, Any] | None = None) -> None:
    """Raise :class:`AgentConfigError` when the configuration is unusable."""
    try:
        build_adapter(key, config)
    except AgentConfigError:
        raise
    except UnknownAdapter as exc:
        raise AgentConfigError(str(exc)) from exc


def register_adapter(cls: type[AgentAdapter]) -> type[AgentAdapter]:
    _ADAPTERS[cls.capabilities.key] = cls
    return cls


def adapter_keys() -> list[str]:
    return sorted(_ADAPTERS)
