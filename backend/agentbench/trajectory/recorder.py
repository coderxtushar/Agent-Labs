"""Structured trajectory recording.

Every observable step of a run is appended here with a monotonic per-run
sequence number.  Ordering is defined by ``seq``, never by timestamp, because
timestamps collide at this resolution.

Scope of what is recorded
-------------------------
Tool calls, commands and exit codes, file reads and writes the adapter reports,
provider-visible message metadata, context-compaction notices the adapter
exposes, errors and retries, the generated patch, and every grading layer.

This platform has no access to a model's hidden reasoning and does not pretend
otherwise: ``model.response`` carries token counts, finish reasons and the
assistant text the provider returned, not internal chain-of-thought.

Unavailable vs. empty
---------------------
A field that could not be observed is recorded as ``None`` with a sibling
``"<field>_available": false``; a field that was observed to be empty is
recorded as its empty value.  Truncated payloads set ``truncated=True`` on the
event row so a consumer can tell "nothing stored" from "nothing happened".
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentbench.config import get_settings
from agentbench.db.models import RunEventRecord
from agentbench.logging_setup import get_logger
from agentbench.schemas.enums import EventType
from agentbench.trajectory.redaction import active_secrets, redact_value

log = get_logger(__name__)

UNAVAILABLE = "__unavailable__"


@dataclass(slots=True)
class RecordedEvent:
    seq: int
    ts: datetime
    event_type: str
    payload: dict[str, Any]
    truncated: bool


class EventRecorder:
    """Append-only event writer for a single run.

    Thread-safe: an adapter may emit events from a worker thread while the
    orchestrator records lifecycle events from another.
    """

    def __init__(
        self,
        session: Session,
        run_id: str,
        *,
        start_seq: int | None = None,
        commit: bool = True,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._lock = threading.Lock()
        self._settings = get_settings()
        self._secrets = active_secrets()
        # Committing each event keeps the run's write transaction short. A long
        # transaction would both block the heartbeat/cancellation probe (which
        # uses its own session) and hide the trajectory from live readers until
        # the run ended.
        self._commit = commit
        self._seq = start_seq if start_seq is not None else self._load_max_seq()

    def _load_max_seq(self) -> int:
        current = self._session.execute(
            select(func.max(RunEventRecord.seq)).where(RunEventRecord.run_id == self._run_id)
        ).scalar()
        return int(current or 0)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def last_seq(self) -> int:
        return self._seq

    # -- writing -----------------------------------------------------------
    def record(
        self,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        *,
        ts: datetime | None = None,
    ) -> RecordedEvent:
        """Append one event. Never raises on payload problems - an event that
        cannot be serialised is stored as an ``error`` describing the problem,
        because losing the trajectory silently is worse."""
        payload = dict(payload or {})
        with self._lock:
            self._seq += 1
            seq = self._seq

            try:
                safe = redact_value(payload, self._secrets)
                # Round-trip through JSON so what is handed to the database is
                # natively serialisable. Checking with `default=str` alone would
                # pass values (sets, dataclasses, Paths) that SQLAlchemy's JSON
                # codec then rejects at INSERT time, losing the event entirely.
                encoded = json.dumps(safe, default=str)
                safe = json.loads(encoded)
                truncated = False
                limit = self._settings.event_payload_max_chars
                if len(encoded) > limit:
                    safe = self._shrink(safe, limit)
                    truncated = True
            except (TypeError, ValueError) as exc:
                safe = {"serialization_error": str(exc), "original_type": type(payload).__name__}
                truncated = True

            row = RunEventRecord(
                run_id=self._run_id,
                seq=seq,
                ts=ts or datetime.now(UTC),
                event_type=str(event_type),
                payload=safe,
                truncated=truncated,
            )
            self._session.add(row)
            self._session.flush()
            if self._commit:
                self._session.commit()

        log.debug(
            "event.recorded",
            extra={"run_id": self._run_id, "seq": seq, "event_type": str(event_type)},
        )
        return RecordedEvent(seq, row.ts, row.event_type, safe, truncated)

    def _shrink(self, payload: dict[str, Any], limit: int) -> dict[str, Any]:
        """Trim the largest string fields until the payload fits the budget."""
        out = dict(payload)
        string_fields = sorted(
            ((k, v) for k, v in out.items() if isinstance(v, str)),
            key=lambda kv: len(kv[1]),
            reverse=True,
        )
        budget = max(limit // max(len(string_fields), 1), 500)
        for key, value in string_fields:
            if len(json.dumps(out, default=str)) <= limit:
                break
            if len(value) > budget:
                out[key] = value[:budget] + f"\n...[truncated {len(value) - budget} chars]"
        encoded = json.dumps(out, default=str)
        if len(encoded) > limit:
            return {
                "truncated_payload": True,
                "keys": sorted(payload.keys()),
                "preview": encoded[:limit],
            }
        out["truncated_payload"] = True
        return out

    # -- convenience helpers ----------------------------------------------
    def error(self, message: str, **detail: Any) -> RecordedEvent:
        return self.record(EventType.ERROR, {"message": message, **detail})

    def command(self, result: Any, *, phase: str) -> RecordedEvent:
        """Record a sandbox command execution from an ``ExecResult``."""
        return self.record(
            EventType.COMMAND_EXEC,
            {
                "phase": phase,
                "command": getattr(result, "command", None),
                "exit_code": getattr(result, "exit_code", None),
                "timed_out": getattr(result, "timed_out", None),
                "duration_s": round(float(getattr(result, "duration_s", 0.0)), 3),
                "stdout_tail": (getattr(result, "stdout", "") or "")[-4000:],
                "stderr_tail": (getattr(result, "stderr", "") or "")[-4000:],
                "output_truncated": getattr(result, "output_truncated", False),
            },
        )

    def unavailable(self, field: str) -> dict[str, Any]:
        """Helper for payloads: mark a field as not observable."""
        return {field: None, f"{field}_available": False}


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def read_events(
    session: Session,
    run_id: str,
    *,
    after_seq: int = 0,
    limit: int = 1000,
    event_types: Sequence[str] | None = None,
) -> list[RunEventRecord]:
    stmt = (
        select(RunEventRecord)
        .where(RunEventRecord.run_id == run_id, RunEventRecord.seq > after_seq)
        .order_by(RunEventRecord.seq.asc())
        .limit(limit)
    )
    if event_types:
        stmt = stmt.where(RunEventRecord.event_type.in_(list(event_types)))
    return list(session.execute(stmt).scalars())


def count_events(session: Session, run_id: str) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(RunEventRecord).where(RunEventRecord.run_id == run_id)
        ).scalar()
        or 0
    )
