"""Agent adapter contract.

An adapter's only job is to take a prepared sandbox plus a task brief and leave
the workspace in whatever state the agent produced.  It does **not** compute the
patch, run tests, decide success, or touch the database.  That separation is
what keeps grading independent of the agent.

What an adapter is given
------------------------
A :class:`AgentRunContext` carrying the sandbox, the agent-visible task payload
(never the hints, never the hidden acceptance tests), the resource budget, a
deadline, an :class:`EventSink` for recording observable steps, and a
cancellation flag it must poll.

What an adapter returns
-----------------------
An :class:`AgentRunResult` describing how the agent terminated, plus whatever
usage and cost information the provider actually reported.  Unknown values stay
``None``; an adapter must never invent a token count or a cost.

Failure contract
----------------
* Raise :class:`AgentAdapterError` for a fault in the adapter or provider -
  recorded as ``AGENT_ERROR``.
* Raise :class:`agentbench.sandbox.base.SandboxError` for a sandbox fault -
  recorded as ``INFRA_ERROR`` and eligible for an infrastructure retry.
* Return normally with ``status=TIMEOUT`` when the agent ran out of budget.
* Returning normally means "the agent is finished", not "the agent succeeded".
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agentbench.sandbox.base import Sandbox
from agentbench.schemas.enums import EventType
from agentbench.schemas.task import TaskSpec

ADAPTER_CONTRACT_VERSION = "1.0"


class AgentAdapterError(RuntimeError):
    """A fault attributable to the agent, its harness or its model provider."""


class AgentConfigError(ValueError):
    """The adapter configuration is invalid; raised before a run starts."""


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"
    #: The agent ran but explicitly declined to attempt the task.
    ABSTAINED = "abstained"


class EventSink(Protocol):
    """The subset of the recorder an adapter is allowed to use."""

    def record(self, event_type: EventType | str, payload: dict[str, Any] | None = None) -> Any: ...
    def command(self, result: Any, *, phase: str) -> Any: ...


@dataclass(slots=True)
class TokenUsage:
    """Usage as *reported by the provider*. ``None`` means not reported."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    requests: int = 0

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.requests,
            "reported_by_provider": self.input_tokens is not None or self.output_tokens is not None,
        }


@dataclass(slots=True)
class AgentRunContext:
    run_id: str
    sandbox: Sandbox
    task: TaskSpec
    #: Exactly what the agent may see. Built by
    #: :meth:`TaskSpec.agent_visible_payload`.
    brief: dict[str, Any]
    workdir: str
    events: EventSink
    #: Monotonic deadline. An adapter must stop by this point.
    deadline: float
    max_steps: int
    #: Non-secret adapter configuration from the agent record.
    config: dict[str, Any] = field(default_factory=dict)
    sampling: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    #: Poll this; return ``AgentStatus.CANCELLED`` promptly when it returns True.
    is_cancelled: Callable[[], bool] = lambda: False

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    @property
    def expired(self) -> bool:
        return self.seconds_remaining <= 0


@dataclass(slots=True)
class AgentRunResult:
    status: AgentStatus
    #: Free-text reason, always set for non-``COMPLETED`` statuses.
    reason: str = ""
    steps: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    #: Cost in USD, only when it can be computed from a known source.
    cost_usd: float | None = None
    #: Provenance of ``cost_usd``, e.g. ``"provider_reported"`` or
    #: ``"estimated:<price-table-id>"``. Required whenever cost is set.
    cost_source: str | None = None
    #: Model/harness identity actually used, for the run record.
    model: str | None = None
    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cost_usd is not None and not self.cost_source:
            raise AgentAdapterError("cost_usd was reported without a cost_source")

    @property
    def ok(self) -> bool:
        return self.status in (AgentStatus.COMPLETED, AgentStatus.ABSTAINED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "reason": self.reason,
            "steps": self.steps,
            "usage": self.usage.to_dict(),
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "model": self.model,
            "provider": self.provider,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Declared, checkable capabilities. The UI shows these verbatim."""

    key: str
    display_name: str
    description: str
    #: True for deterministic test doubles whose results are not model results.
    is_simulated: bool
    requires_network: bool = False
    requires_credentials: tuple[str, ...] = ()
    reports_token_usage: bool = False
    reports_cost: bool = False
    supports_cancellation: bool = True
    streams_events: bool = True
    config_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "description": self.description,
            "is_simulated": self.is_simulated,
            "requires_network": self.requires_network,
            "requires_credentials": list(self.requires_credentials),
            "reports_token_usage": self.reports_token_usage,
            "reports_cost": self.reports_cost,
            "supports_cancellation": self.supports_cancellation,
            "streams_events": self.streams_events,
            "contract_version": ADAPTER_CONTRACT_VERSION,
            "config_schema": self.config_schema,
        }


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    detail: str
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "detail": self.detail, "missing": list(self.missing)}


class AgentAdapter(ABC):
    """Base class for all agent adapters."""

    capabilities: AdapterCapabilities

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.validate_config()

    def validate_config(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Reject bad configuration before any sandbox is created.

        A no-op by default: an adapter with no configuration to check should not
        be forced to implement an empty override.
        """

    @classmethod
    @abstractmethod
    def readiness(cls, config: dict[str, Any] | None = None) -> ReadinessReport:
        """Can this adapter run right now? Must not have side effects."""

    @abstractmethod
    def run(self, ctx: AgentRunContext) -> AgentRunResult:
        """Drive the agent to completion inside ``ctx.sandbox``."""

    # -- helpers for subclasses -------------------------------------------
    @staticmethod
    def _cancelled_result(steps: int = 0) -> AgentRunResult:
        return AgentRunResult(
            AgentStatus.CANCELLED, reason="cancellation requested by the orchestrator", steps=steps
        )

    @staticmethod
    def _timeout_result(steps: int, detail: str = "") -> AgentRunResult:
        return AgentRunResult(
            AgentStatus.TIMEOUT,
            reason=detail or "the agent exceeded its wall-clock budget",
            steps=steps,
        )
