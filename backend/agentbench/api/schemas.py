"""API request and response models.

Responses are plain dictionaries assembled by serialiser functions rather than
response models, because the metrics payloads are deeply nested and already
have a stable, documented shape.  Requests are strict pydantic models so bad
input is rejected at the edge with a field-level error.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentbench.db.models import (
    ExperimentRecord,
    FailureAnnotationRecord,
    GradingResultRecord,
    RunEventRecord,
    RunRecord,
    TaskRecord,
)
from agentbench.schemas import provenance
from agentbench.schemas.enums import FailureCategory, RunOutcome, RunState
from agentbench.trajectory.redaction import redact_value


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
class BenchmarkImportRequest(StrictRequest):
    #: Raw JSON/JSONL benchmark content, or a parsed object/array.
    content: str | dict[str, Any] | list[Any]
    filename: str = "benchmark.json"
    allow_overwrite: bool = False


class TaskEnableRequest(StrictRequest):
    enabled: bool


class AgentCreateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    adapter: str = Field(min_length=1, max_length=64)
    version: str = "1.0"
    description: str = ""
    provider: str | None = None
    model: str | None = None
    prompt_version: str = "1.0"
    tools_version: str = "1.0"
    config: dict[str, Any] = Field(default_factory=dict)
    sampling: dict[str, Any] = Field(default_factory=dict)
    allow_update: bool = False


class AgentEnableRequest(StrictRequest):
    enabled: bool


class ExperimentCreateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=160)
    agent_config_id: str
    #: ``task_id@version`` strings. Use the tasks endpoint to discover them.
    task_keys: list[str] = Field(min_length=1)
    description: str = ""
    repetitions: int = Field(default=1, ge=1, le=50)
    concurrency: int = Field(default=1, ge=1, le=16)
    seed: int | None = None
    include_hints: bool = False
    limits_override: dict[str, Any] = Field(default_factory=dict)
    baseline_experiment_id: str | None = None
    launch: bool = True


class ResumeRequest(StrictRequest):
    force: bool = False


class AnnotationRequest(StrictRequest):
    category: FailureCategory
    rationale: str = Field(default="", max_length=4000)
    author: str | None = Field(default=None, max_length=120)


# --------------------------------------------------------------------------- #
def serialize_task(record: TaskRecord, *, include_spec: bool = False) -> dict[str, Any]:
    """Task view.

    ``grading.acceptance_tests`` and ``hints`` are stripped unless the caller
    explicitly asks for the full spec, so the default listing cannot leak the
    hidden grading tests to a UI that an agent operator can see.
    """
    payload: dict[str, Any] = {
        "id": record.id,
        "task_id": record.task_id,
        "version": record.version,
        "key": record.key,
        "title": record.title,
        "description": record.description,
        "category": record.category,
        "difficulty": record.difficulty,
        "tags": record.tags or [],
        "benchmark_id": record.benchmark_id,
        "benchmark_version": record.benchmark_version,
        "validation_status": record.validation_status,
        "validated_at": record.validated_at.isoformat() if record.validated_at else None,
        "validation_report": record.validation_report,
        "enabled": record.enabled,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
    spec = dict(record.spec or {})
    grading = dict(spec.get("grading") or {})
    tests = dict(spec.get("tests") or {})
    payload["summary"] = {
        "image": (spec.get("environment") or {}).get("image"),
        "repo": spec.get("repo"),
        "fail_to_pass_count": len(tests.get("fail_to_pass") or []),
        "pass_to_pass_count": len(tests.get("pass_to_pass") or []),
        "hidden_acceptance_files": sorted((grading.get("acceptance_tests") or {}).keys()),
        "forbidden_paths": grading.get("forbidden_paths") or [],
        "limits": spec.get("limits"),
        "hint_count": len(spec.get("hints") or []),
        "acceptance_criteria": spec.get("acceptance_criteria") or [],
        "problem_statement": spec.get("problem_statement", ""),
        "context_files": spec.get("context_files") or [],
    }
    if include_spec:
        payload["spec"] = spec
    return payload


def serialize_experiment(record: ExperimentRecord) -> dict[str, Any]:
    snapshot = record.agent_snapshot or {}
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "state": record.state,
        "benchmark_id": record.benchmark_id,
        "benchmark_version": record.benchmark_version,
        "agent": {
            "config_id": record.agent_config_id,
            "label": f"{snapshot.get('name')}@{snapshot.get('version')}",
            "adapter": snapshot.get("adapter"),
            "model": snapshot.get("model"),
            "provider": snapshot.get("provider"),
            "prompt_version": snapshot.get("prompt_version"),
            "tools_version": snapshot.get("tools_version"),
            "is_simulated": bool(snapshot.get("is_simulated")),
            "readiness_at_launch": snapshot.get("readiness_at_launch"),
        },
        "provenance": provenance.describe(provenance.from_agent_snapshot(snapshot)),
        "grading_version": record.grading_version,
        "repetitions": record.repetitions,
        "concurrency": record.concurrency,
        "seed": record.seed,
        "include_hints": record.include_hints,
        "limits_override": record.limits_override or {},
        "task_keys": record.task_keys or [],
        "baseline_experiment_id": record.baseline_experiment_id,
        "cancel_requested": record.cancel_requested,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "config_snapshot": redact_value(record.config_snapshot or {}),
    }


def serialize_run(record: RunRecord, *, include_patch: bool = False) -> dict[str, Any]:
    state = RunState(record.state)
    outcome = RunOutcome(record.outcome) if record.outcome else None
    payload: dict[str, Any] = {
        "id": record.id,
        "experiment_id": record.experiment_id,
        "task_key": record.task_key,
        "task_id": record.task_id,
        "task_version": record.task_version,
        "task_title": (record.task_snapshot or {}).get("title"),
        "task_category": (record.task_snapshot or {}).get("category"),
        "task_difficulty": (record.task_snapshot or {}).get("difficulty"),
        "task_validation_status": record.task_validation_status,
        "attempt_index": record.attempt_index,
        "state": record.state,
        "is_terminal": state.is_terminal,
        "is_active": state.is_active,
        "outcome": record.outcome,
        "counts_toward_success_rate": (outcome.counts_toward_success_rate if outcome else None),
        "attributable_to_agent": outcome.is_attributable_to_agent if outcome else None,
        "termination_reason": record.termination_reason,
        "error_message": record.error_message,
        "infra_attempts": record.infra_attempts,
        "seed": record.seed,
        "limits": record.limits or {},
        "patch_stats": record.patch_stats,
        "has_patch": bool(record.patch),
        "usage": record.usage,
        "cost_usd": record.cost_usd,
        "cost_source": record.cost_source,
        "timings": record.timings or {},
        "sandbox_provider": record.sandbox_provider,
        "agent": {
            "label": f"{(record.agent_snapshot or {}).get('name')}@"
            f"{(record.agent_snapshot or {}).get('version')}",
            "adapter": (record.agent_snapshot or {}).get("adapter"),
            "model": (record.agent_snapshot or {}).get("observed_model")
            or (record.agent_snapshot or {}).get("model"),
            "is_simulated": bool((record.agent_snapshot or {}).get("is_simulated")),
        },
        "provenance": provenance.describe(
            provenance.from_agent_snapshot(record.agent_snapshot or {})
        ),
        "claimed_by": record.claimed_by,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }
    if include_patch:
        payload["patch"] = record.patch
    return payload


def serialize_grading(record: GradingResultRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "grader_version": record.grader_version,
        "passed": record.passed,
        "score": record.score,
        "tests": {
            "passed": record.tests_passed,
            "failed": record.tests_failed,
            "errored": record.tests_errored,
            "skipped": record.tests_skipped,
        },
        "layers": record.layers or [],
        "evidence": record.evidence or {},
        "judge": record.judge,
        "judge_note": ("model-assisted review is reported separately and never affects `passed`"),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def serialize_event(record: RunEventRecord) -> dict[str, Any]:
    return {
        "seq": record.seq,
        "ts": record.ts.isoformat() if record.ts else None,
        "event_type": record.event_type,
        "payload": record.payload or {},
        "truncated": record.truncated,
    }


def serialize_annotation(record: FailureAnnotationRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "category": record.category,
        "source": record.source,
        "model_generated": record.source == "model",
        "confidence": record.confidence,
        "rationale": record.rationale,
        "evidence_refs": record.evidence_refs or {},
        "author": record.author,
        "supersedes_id": record.supersedes_id,
        "superseded": record.superseded,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


ReportFormat = Literal["json", "csv", "markdown"]
