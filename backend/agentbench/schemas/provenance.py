"""Provenance of a result.

``is_simulated`` is a boolean, and a boolean cannot distinguish the four kinds
of evidence this platform produces. Conflating them is how a pipeline
demonstration ends up quoted as a model benchmark number, so the kind is
recorded explicitly on every experiment at launch and carried into metrics,
exported reports and the UI.

The classification is derived from facts observed at launch - the adapter's
declared capabilities and whether the credentials it requires were actually
present - not from anything a caller asserts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Provenance(StrEnum):
    #: Deterministic, non-model adapter (``mock``, ``scripted``). Validates the
    #: harness. Never a statement about model capability.
    SIMULATED = "simulated"
    #: A model-backed adapter that ran without the credentials it declares, so
    #: no inference happened. Exercises the adapter's offline paths only.
    OFFLINE_ADAPTER_TEST = "offline_adapter_test"
    #: A model-backed adapter with its credentials present: real inference.
    LIVE_MODEL = "live_model"
    #: A real agent that is not driven by this platform's model client, e.g. an
    #: external CLI agent bridged through ``shell_command``.
    EXTERNAL_AGENT = "external_agent"

    @property
    def is_model_result(self) -> bool:
        """True only when a language model actually produced the work."""
        return self is Provenance.LIVE_MODEL

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def caveat(self) -> str:
        return _CAVEATS[self]


_LABELS: dict[Provenance, str] = {
    Provenance.SIMULATED: "Simulated agent",
    Provenance.OFFLINE_ADAPTER_TEST: "Offline adapter test",
    Provenance.LIVE_MODEL: "Live model run",
    Provenance.EXTERNAL_AGENT: "External agent",
}

_CAVEATS: dict[Provenance, str] = {
    Provenance.SIMULATED: (
        "Produced by a deterministic, non-model agent. These numbers validate the "
        "evaluation pipeline and are not a model benchmark result."
    ),
    Provenance.OFFLINE_ADAPTER_TEST: (
        "A model-backed adapter ran without its credentials, so no inference took "
        "place. These numbers describe the adapter's offline behaviour only."
    ),
    Provenance.LIVE_MODEL: (
        "Produced by real model inference. Scope is limited to the tasks in this "
        "benchmark; it does not measure general coding capability."
    ),
    Provenance.EXTERNAL_AGENT: (
        "Produced by an external agent process. What the agent did internally is "
        "outside this platform's observation."
    ),
}


def classify(
    *,
    is_simulated: bool,
    requires_credentials: tuple[str, ...] | list[str],
    readiness_ready: bool,
    model: str | None,
) -> Provenance:
    """Derive provenance from launch-time facts."""
    if is_simulated:
        return Provenance.SIMULATED
    if requires_credentials:
        # The adapter calls a model itself; whether it could is the question.
        return Provenance.LIVE_MODEL if readiness_ready else Provenance.OFFLINE_ADAPTER_TEST
    return Provenance.EXTERNAL_AGENT if not model else Provenance.LIVE_MODEL


def describe(provenance: Provenance | str) -> dict[str, Any]:
    value = Provenance(provenance)
    return {
        "value": str(value),
        "label": value.label,
        "caveat": value.caveat,
        "is_model_result": value.is_model_result,
    }


def from_agent_snapshot(snapshot: dict[str, Any]) -> Provenance:
    """Read provenance off a stored snapshot, deriving it for older rows."""
    stored = snapshot.get("provenance")
    if isinstance(stored, str):
        try:
            return Provenance(stored)
        except ValueError:
            pass
    capabilities = snapshot.get("capabilities") or {}
    readiness = snapshot.get("readiness_at_launch") or {}
    return classify(
        is_simulated=bool(snapshot.get("is_simulated")),
        requires_credentials=tuple(capabilities.get("requires_credentials") or ()),
        readiness_ready=bool(readiness.get("ready")),
        model=snapshot.get("model"),
    )
