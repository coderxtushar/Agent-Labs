"""Failure classification.

Every classification is derived from stored evidence - grading layers, exit
codes, runner output, the patch stats - and records *why* it reached its
conclusion.  Nothing is inferred from vibes, and the honest answer
``UNKNOWN`` is used instead of a plausible guess.

Provenance is explicit.  Rule-derived classifications are tagged
``ClassificationSource.RULE``; a model-produced one would be tagged ``MODEL``
and shown as such in the UI.  Human corrections are appended as new rows that
*supersede* the previous classification: the raw evidence in
``grading_results`` and ``run_events`` is never mutated, so a wrong
classification can always be audited and reverted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbench.db.models import FailureAnnotationRecord, RunRecord
from agentbench.grading.engine import GradingOutcome
from agentbench.schemas.enums import (
    ClassificationSource,
    FailureCategory,
    GradingLayerStatus,
    RunOutcome,
)


@dataclass(slots=True)
class Classification:
    category: FailureCategory
    source: ClassificationSource = ClassificationSource.RULE
    confidence: float | None = None
    rationale: str = ""
    evidence_refs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": str(self.category),
            "source": str(self.source),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence_refs": self.evidence_refs,
            "model_generated": self.source is ClassificationSource.MODEL,
        }


def _layers(
    grading: GradingOutcome | None, record_layers: list[Any] | None
) -> dict[str, dict[str, Any]]:
    if grading is not None:
        return {layer.name: layer.to_dict() for layer in grading.layers}
    out: dict[str, dict[str, Any]] = {}
    for item in record_layers or []:
        if isinstance(item, dict) and "name" in item:
            out[str(item["name"])] = item
    return out


def _status(layer: dict[str, Any] | None) -> str | None:
    return None if layer is None else str(layer.get("status"))


def classify_failure(
    session: Session, run: RunRecord, grading: GradingOutcome | None = None
) -> Classification:
    """Derive a failure category for a terminated, non-solved run."""
    outcome = RunOutcome(run.outcome) if run.outcome else RunOutcome.INFRA_ERROR
    stored_layers: list[Any] | None = None
    if grading is None and run.grading is not None:
        stored_layers = list(run.grading.layers or [])
    layers = _layers(grading, stored_layers)
    refs: dict[str, Any] = {
        "run_id": run.id,
        "outcome": str(outcome),
        "layer_statuses": {name: _status(layer) for name, layer in layers.items()},
    }

    if outcome is RunOutcome.INFRA_ERROR:
        return Classification(
            FailureCategory.INFRASTRUCTURE_FAILURE,
            confidence=1.0,
            rationale=(
                "the run ended with an infrastructure error after "
                f"{run.infra_attempts} attempt(s): {(run.error_message or '')[:400]}"
            ),
            evidence_refs={**refs, "infra_attempts": run.infra_attempts},
        )

    if outcome is RunOutcome.TIMEOUT:
        return Classification(
            FailureCategory.TIMEOUT,
            confidence=1.0,
            rationale=(
                "the agent exhausted its wall-clock budget "
                f"({(run.limits or {}).get('agent_timeout_s', 'unknown')}s)"
            ),
            evidence_refs={**refs, "timings": run.timings},
        )

    if outcome is RunOutcome.CANCELLED:
        return Classification(
            FailureCategory.UNKNOWN,
            confidence=1.0,
            rationale="the run was cancelled before it could be graded",
            evidence_refs=refs,
        )

    if outcome is RunOutcome.INVALID_TASK:
        env_layer = layers.get("environment")
        if _status(env_layer) in {
            str(GradingLayerStatus.ERRORED),
            str(GradingLayerStatus.FAILED),
        } or (run.termination_reason and "setup" in run.termination_reason.lower()):
            return Classification(
                FailureCategory.ENVIRONMENT_SETUP_FAILURE,
                confidence=0.9,
                rationale=(
                    "the task's own environment setup failed, so the agent was never "
                    f"meaningfully evaluated: {(run.termination_reason or '')[:400]}"
                ),
                evidence_refs={**refs, "environment_layer": env_layer},
            )
        return Classification(
            FailureCategory.INVALID_TASK,
            confidence=0.8,
            rationale=f"the task definition could not be executed: {(run.termination_reason or '')[:400]}",
            evidence_refs=refs,
        )

    if outcome is RunOutcome.AGENT_ERROR:
        return Classification(
            FailureCategory.TOOL_INVOCATION_ERROR,
            confidence=0.7,
            rationale=(
                "the agent harness itself failed rather than producing a wrong answer: "
                f"{(run.termination_reason or '')[:400]}"
            ),
            evidence_refs=refs,
        )

    if outcome is RunOutcome.GRADING_ERROR:
        for name, layer in layers.items():
            dep = (layer.get("evidence") or {}).get("missing_dependency")
            if dep:
                return Classification(
                    FailureCategory.MISSING_DEPENDENCY,
                    confidence=0.85,
                    rationale=f"the test runner reported a missing dependency '{dep}' in layer '{name}'",
                    evidence_refs={**refs, "missing_dependency": dep, "layer": name},
                )
        errored = [
            name
            for name, layer in layers.items()
            if _status(layer) == str(GradingLayerStatus.ERRORED)
        ]
        return Classification(
            FailureCategory.GRADING_FAILURE,
            confidence=0.8,
            rationale=(
                "grading could not produce a verdict; errored layer(s): "
                + (", ".join(errored) or "unknown")
            ),
            evidence_refs={**refs, "errored_layers": errored},
        )

    # --- UNSOLVED: a real, graded agent failure --------------------------
    patch_layer = layers.get("patch")
    if _status(patch_layer) == str(GradingLayerStatus.FAILED):
        evidence = (patch_layer or {}).get("evidence") or {}
        if evidence.get("violations"):
            return Classification(
                FailureCategory.FORBIDDEN_FILES_MODIFIED,
                confidence=1.0,
                rationale=(
                    "the patch modified protected paths, which fails the task regardless of "
                    f"test results: {', '.join(evidence['violations'][:5])}"
                ),
                evidence_refs={**refs, "violations": evidence["violations"]},
            )
        return Classification(
            FailureCategory.NO_PATCH_PRODUCED,
            confidence=1.0,
            rationale="the agent finished without changing any file in the workspace",
            evidence_refs={**refs, "patch_stats": run.patch_stats},
        )

    ftp = layers.get("fail_to_pass")
    ptp = layers.get("pass_to_pass")
    ftp_failed = _status(ftp) == str(GradingLayerStatus.FAILED)
    ptp_failed = _status(ptp) == str(GradingLayerStatus.FAILED)

    for candidate in (ftp, ptp):
        dep = ((candidate or {}).get("evidence") or {}).get("missing_dependency")
        if dep:
            return Classification(
                FailureCategory.MISSING_DEPENDENCY,
                confidence=0.85,
                rationale=f"the test run reported a missing dependency '{dep}'",
                evidence_refs={**refs, "missing_dependency": dep},
            )

    if ftp_failed and ptp_failed:
        return Classification(
            FailureCategory.TESTS_FAILING_AFTER_PATCH,
            confidence=0.9,
            rationale=(
                "after the patch, both the task's target tests and the pre-existing suite "
                "fail, so the change is broadly incorrect"
            ),
            evidence_refs={
                **refs,
                "fail_to_pass_failing": ((ftp or {}).get("evidence") or {}).get("failing_nodes"),
                "pass_to_pass_failing": ((ptp or {}).get("evidence") or {}).get("failing_nodes"),
            },
        )

    if ptp_failed and not ftp_failed:
        return Classification(
            FailureCategory.EXISTING_REGRESSION,
            confidence=0.95,
            rationale=(
                "the target tests now pass but previously-passing tests broke, so the patch "
                "introduced a regression"
            ),
            evidence_refs={
                **refs,
                "pass_to_pass_failing": ((ptp or {}).get("evidence") or {}).get("failing_nodes"),
            },
        )

    if ftp_failed:
        failing = ((ftp or {}).get("evidence") or {}).get("failing_nodes") or []
        return Classification(
            FailureCategory.INCORRECT_IMPLEMENTATION,
            confidence=0.9,
            rationale=(
                "the pre-existing suite still passes but the task's target tests do not, so "
                f"the implementation is incorrect or incomplete ({len(failing)} target test(s) failing)"
            ),
            evidence_refs={**refs, "failing_nodes": failing[:20]},
        )

    static_failed = [
        name
        for name, layer in layers.items()
        if name.startswith("static:")
        and _status(layer) == str(GradingLayerStatus.FAILED)
        and layer.get("required")
    ]
    if static_failed:
        return Classification(
            FailureCategory.INCORRECT_IMPLEMENTATION,
            confidence=0.7,
            rationale=f"required static check(s) failed: {', '.join(static_failed)}",
            evidence_refs={**refs, "static_failed": static_failed},
        )

    return Classification(
        FailureCategory.UNKNOWN,
        confidence=None,
        rationale=(
            "the run failed but no stored evidence matched a known pattern; inspect the "
            "event timeline and grading layers"
        ),
        evidence_refs=refs,
    )


# --------------------------------------------------------------------------- #
def store_classification(
    session: Session,
    run_id: str,
    classification: Classification,
    *,
    author: str | None = None,
) -> FailureAnnotationRecord:
    """Append a classification, superseding the previous effective one."""
    previous = effective_annotation(session, run_id)
    if previous is not None:
        previous.superseded = True

    record = FailureAnnotationRecord(
        run_id=run_id,
        category=str(classification.category),
        source=str(classification.source),
        confidence=classification.confidence,
        rationale=classification.rationale[:4000],
        evidence_refs=classification.evidence_refs,
        author=author,
        supersedes_id=previous.id if previous is not None else None,
    )
    session.add(record)
    session.flush()
    return record


def effective_annotation(session: Session, run_id: str) -> FailureAnnotationRecord | None:
    return session.execute(
        select(FailureAnnotationRecord)
        .where(
            FailureAnnotationRecord.run_id == run_id,
            FailureAnnotationRecord.superseded.is_(False),
        )
        .order_by(FailureAnnotationRecord.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def annotation_history(session: Session, run_id: str) -> list[FailureAnnotationRecord]:
    return list(
        session.execute(
            select(FailureAnnotationRecord)
            .where(FailureAnnotationRecord.run_id == run_id)
            .order_by(FailureAnnotationRecord.created_at.asc())
        ).scalars()
    )
