"""Agent configuration persistence.

An agent configuration is the second half of a reproducible experiment: adapter,
model, prompt and tool versions, sampling parameters and adapter config.
Configurations are validated against the adapter's own contract at registration
time, so a broken agent is rejected before it can waste a benchmark run.

Secrets never live here.  Adapters read credentials from the process
environment at run time (see
:class:`agentbench.adapters.shell_command.ShellCommandAdapter`), and any
credential-shaped value in a stored config is masked by
:mod:`agentbench.trajectory.redaction` before it reaches a log or an API
response.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentbench.adapters.base import AgentConfigError
from agentbench.adapters.registry import capabilities_for, readiness_for, validate_agent_config
from agentbench.config import get_settings
from agentbench.db.models import AgentConfigRecord, ExperimentRecord
from agentbench.logging_setup import get_logger
from agentbench.registry.loader import inline_file_refs
from agentbench.trajectory.redaction import redact_value

log = get_logger(__name__)


class AgentConflict(ValueError):
    """An agent with this name and version already exists."""


def register_agent(
    session: Session,
    *,
    name: str,
    adapter: str,
    version: str = "1.0",
    description: str = "",
    provider: str | None = None,
    model: str | None = None,
    prompt_version: str = "1.0",
    tools_version: str = "1.0",
    config: dict[str, Any] | None = None,
    sampling: dict[str, Any] | None = None,
    allow_update: bool = False,
) -> AgentConfigRecord:
    resolved = inline_file_refs(dict(config or {}), get_settings().benchmarks_dir)
    if not isinstance(resolved, dict):
        raise AgentConfigError("agent config must be an object")

    validate_agent_config(adapter, resolved)
    capabilities = capabilities_for(adapter)

    existing = get_agent(session, name, version)
    if existing is not None and not allow_update:
        raise AgentConflict(
            f"agent '{name}@{version}' already exists; bump the version so existing "
            "experiment results keep referring to the configuration they used"
        )

    record = existing or AgentConfigRecord(name=name, version=version)
    record.description = description
    record.adapter = adapter
    record.provider = provider
    record.model = model
    record.prompt_version = prompt_version
    record.tools_version = tools_version
    record.config = resolved
    record.sampling = dict(sampling or {})
    record.is_simulated = capabilities.is_simulated
    if existing is None:
        session.add(record)
    session.flush()

    log.info(
        "agents.registered",
        extra={
            "agent": record.label,
            "adapter": adapter,
            "model": model,
            "simulated": record.is_simulated,
            "updated": existing is not None,
        },
    )
    return record


def get_agent(session: Session, name: str, version: str | None = None) -> AgentConfigRecord | None:
    stmt = select(AgentConfigRecord).where(AgentConfigRecord.name == name)
    stmt = (
        stmt.where(AgentConfigRecord.version == version)
        if version
        else stmt.order_by(AgentConfigRecord.version.desc())
    )
    return session.execute(stmt.limit(1)).scalar_one_or_none()


def get_agent_by_label(session: Session, label: str) -> AgentConfigRecord | None:
    name, _, version = label.partition("@")
    return get_agent(session, name, version or None)


def list_agents(
    session: Session, *, limit: int = 100, offset: int = 0, include_simulated: bool = True
) -> tuple[list[AgentConfigRecord], int]:
    stmt = select(AgentConfigRecord).order_by(
        AgentConfigRecord.name.asc(), AgentConfigRecord.version.asc()
    )
    if not include_simulated:
        stmt = stmt.where(AgentConfigRecord.is_simulated.is_(False))
    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = list(session.execute(stmt.limit(limit).offset(offset)).scalars())
    return rows, total


def set_enabled(session: Session, agent_id: str, enabled: bool) -> AgentConfigRecord:
    record = session.get(AgentConfigRecord, agent_id)
    if record is None:
        raise AgentConfigError(f"unknown agent configuration: {agent_id}")
    record.enabled = enabled
    session.flush()
    return record


def delete_agent(session: Session, agent_id: str) -> None:
    """Refuse to delete an agent that experiments still reference."""
    record = session.get(AgentConfigRecord, agent_id)
    if record is None:
        raise AgentConfigError(f"unknown agent configuration: {agent_id}")
    used = int(
        session.execute(
            select(func.count())
            .select_from(ExperimentRecord)
            .where(ExperimentRecord.agent_config_id == agent_id)
        ).scalar()
        or 0
    )
    if used:
        raise AgentConfigError(
            f"agent '{record.label}' is referenced by {used} experiment(s); disable it instead "
            "so historical results stay interpretable"
        )
    session.delete(record)
    session.flush()


def describe(record: AgentConfigRecord) -> dict[str, Any]:
    """API-facing view, with capabilities, live readiness and redacted config."""
    capabilities = capabilities_for(record.adapter)
    readiness = readiness_for(record.adapter, record.config or {})
    return {
        "id": record.id,
        "name": record.name,
        "version": record.version,
        "label": record.label,
        "description": record.description,
        "adapter": record.adapter,
        "provider": record.provider,
        "model": record.model,
        "prompt_version": record.prompt_version,
        "tools_version": record.tools_version,
        "config": redact_value(record.config or {}),
        "sampling": record.sampling or {},
        "is_simulated": record.is_simulated,
        "enabled": record.enabled,
        "capabilities": capabilities.to_dict(),
        "readiness": readiness.to_dict(),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
