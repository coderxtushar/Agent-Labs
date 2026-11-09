"""Agent configuration endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from agentbench.adapters.base import AgentConfigError
from agentbench.adapters.registry import (
    UnknownAdapter,
    adapter_keys,
    list_capabilities,
    readiness_for,
)
from agentbench.api.deps import DbSession, Pagination, WriteAccess, bad_request, conflict, not_found
from agentbench.api.schemas import AgentCreateRequest, AgentEnableRequest
from agentbench.db.models import AgentConfigRecord
from agentbench.registry.agents import (
    AgentConflict,
    delete_agent,
    describe,
    list_agents,
    register_agent,
    set_enabled,
)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", summary="List agent configurations with live readiness")
def list_endpoint(
    session: DbSession,
    page: Pagination,
    include_simulated: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    rows, total = list_agents(
        session, limit=page.limit, offset=page.offset, include_simulated=include_simulated
    )
    return page.envelope([describe(row) for row in rows], total)


@router.get("/adapters", summary="Available adapters and their declared capabilities")
def adapters_endpoint() -> dict[str, Any]:
    return {
        "adapters": [capability.to_dict() for capability in list_capabilities()],
        "keys": adapter_keys(),
    }


@router.post("/adapters/{adapter_key}/readiness", summary="Check whether an adapter can run")
def readiness_endpoint(
    adapter_key: str, config: dict[str, Any] | None = None, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        report = readiness_for(adapter_key, config or {})
    except UnknownAdapter as exc:
        raise not_found("adapter", adapter_key) from exc
    return report.to_dict()


@router.get("/{agent_id}", summary="One agent configuration")
def get_endpoint(session: DbSession, agent_id: str) -> dict[str, Any]:
    record = session.get(AgentConfigRecord, agent_id)
    if record is None:
        raise not_found("agent configuration", agent_id)
    return describe(record)


@router.post("", summary="Register an agent configuration", status_code=201)
def create_endpoint(
    session: DbSession, body: AgentCreateRequest, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        record = register_agent(
            session,
            name=body.name,
            adapter=body.adapter,
            version=body.version,
            description=body.description,
            provider=body.provider,
            model=body.model,
            prompt_version=body.prompt_version,
            tools_version=body.tools_version,
            config=body.config,
            sampling=body.sampling,
            allow_update=body.allow_update,
        )
    except AgentConflict as exc:
        raise conflict(str(exc)) from exc
    except (AgentConfigError, UnknownAdapter, ValueError) as exc:
        raise bad_request(str(exc)) from exc
    return describe(record)


@router.patch("/{agent_id}/enabled", summary="Enable or disable an agent configuration")
def enable_endpoint(
    session: DbSession, agent_id: str, body: AgentEnableRequest, _: WriteAccess = None
) -> dict[str, Any]:
    try:
        record = set_enabled(session, agent_id, body.enabled)
    except AgentConfigError as exc:
        raise not_found("agent configuration", agent_id) from exc
    return describe(record)


@router.delete("/{agent_id}", summary="Delete an unreferenced agent configuration", status_code=204)
def delete_endpoint(session: DbSession, agent_id: str, _: WriteAccess = None) -> None:
    try:
        delete_agent(session, agent_id)
    except AgentConfigError as exc:
        message = str(exc)
        if message.startswith("unknown agent configuration"):
            raise not_found("agent configuration", agent_id) from exc
        raise conflict(message) from exc
