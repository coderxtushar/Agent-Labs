"""System status, capabilities and effective settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from agentbench import __version__
from agentbench.adapters.registry import list_capabilities
from agentbench.api.deps import AppSettings, DbSession
from agentbench.db.models import AgentConfigRecord, ExperimentRecord, RunRecord, TaskRecord
from agentbench.grading.engine import GRADER_IMPLEMENTATION_VERSION
from agentbench.sandbox.docker_sandbox import image_present
from agentbench.sandbox.factory import all_provider_statuses, provider_status
from agentbench.schemas.enums import FailureCategory, RunOutcome, RunState
from agentbench.schemas.task import TASK_SCHEMA_VERSION

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness and dependency status")
def health(settings: AppSettings) -> dict[str, Any]:
    active = provider_status()
    return {
        "status": "ok",
        "version": __version__,
        "sandbox": {
            "provider": active.name,
            "available": active.available,
            "detail": active.detail,
            "isolation": active.isolation,
        },
        "database": settings.database_url.split("://", 1)[0],
    }


@router.get("/system/settings", summary="Effective configuration (no secrets)")
def read_settings(settings: AppSettings) -> dict[str, Any]:
    """Non-secret settings only.

    Credential *presence* is reported as a boolean so the UI can show whether an
    adapter is usable, without ever transmitting the value.
    """
    return {
        "env": settings.env,
        "api": {
            "host": settings.api_host,
            "port": settings.api_port,
            "auth_required": bool(settings.api_token),
            "cors_origins": settings.cors_origins,
        },
        "execution": {
            "sandbox_provider": settings.sandbox_provider,
            "local_unsafe_sandbox_allowed": settings.allow_local_unsafe_sandbox,
            "default_image": settings.default_sandbox_image,
            "max_concurrent_runs": settings.max_concurrent_runs,
            "infra_retry_limit": settings.infra_retry_limit,
            "run_lease_seconds": settings.run_lease_seconds,
            "worker_in_api_process": settings.worker_in_api_process,
        },
        "limits": {
            "max_agent_timeout_s": settings.max_agent_timeout_s,
            "max_setup_timeout_s": settings.max_setup_timeout_s,
            "max_test_timeout_s": settings.max_test_timeout_s,
            "max_memory_mb": settings.max_memory_mb,
            "max_cpus": settings.max_cpus,
        },
        "retention": {
            "event_payload_max_chars": settings.event_payload_max_chars,
            "log_tail_max_chars": settings.log_tail_max_chars,
            "event_retention_days": settings.event_retention_days,
        },
        "credentials_present": {
            "openai": bool(settings.openai_api_key),
            "anthropic": bool(settings.anthropic_api_key),
        },
        "versions": {
            "platform": __version__,
            "task_schema": TASK_SCHEMA_VERSION,
            "grader_implementation": GRADER_IMPLEMENTATION_VERSION,
        },
    }


@router.get("/system/capabilities", summary="Adapters, sandbox providers and enum vocabularies")
def capabilities(settings: AppSettings) -> dict[str, Any]:
    return {
        "adapters": [capability.to_dict() for capability in list_capabilities()],
        "sandbox_providers": [
            {
                "name": status.name,
                "available": status.available,
                "detail": status.detail,
                "isolation": status.isolation,
            }
            for status in all_provider_statuses()
        ],
        "default_image_present": image_present(settings.default_sandbox_image),
        "vocabularies": {
            "run_states": [str(state) for state in RunState],
            "run_outcomes": [
                {
                    "value": str(outcome),
                    "counts_toward_success_rate": outcome.counts_toward_success_rate,
                    "attributable_to_agent": outcome.is_attributable_to_agent,
                }
                for outcome in RunOutcome
            ],
            "failure_categories": [str(category) for category in FailureCategory],
        },
    }


@router.get("/system/stats", summary="Row counts, for onboarding and empty states")
def stats(session: DbSession) -> dict[str, Any]:
    def count(model: Any) -> int:
        return int(session.execute(select(func.count()).select_from(model)).scalar() or 0)

    tasks = count(TaskRecord)
    agents = count(AgentConfigRecord)
    experiments = count(ExperimentRecord)
    runs = count(RunRecord)
    return {
        "tasks": tasks,
        "agents": agents,
        "experiments": experiments,
        "runs": runs,
        "is_empty": tasks == 0 and experiments == 0,
        "needs_benchmark": tasks == 0,
        "needs_agent": agents == 0,
        "has_results": runs > 0,
    }
