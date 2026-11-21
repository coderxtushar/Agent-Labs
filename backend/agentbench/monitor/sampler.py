"""Host memory and swap sampled while one run executes.

A run that took 15 minutes on a machine that was swapping says little about
the model. The sampler records what the host looked like during the run so
that a timing can be read with that context.

Threading: the sampler thread only *collects* readings into memory. It never
touches the database; the run's own thread writes one ``resource.summary``
event when the run ends. That keeps the run's session single-threaded.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from agentbench.monitor.host import memory_snapshot

#: Swap growth during a run above this is treated as the host swapping.
SWAP_GROWTH_THRESHOLD_BYTES = 256 * 1024 * 1024
MAX_SERIES_POINTS = 240
_PRESSURE_RANK = {None: 0, "normal": 1, "warning": 2, "critical": 3}

Probe = Callable[[], tuple[dict[str, Any], dict[str, Any]]]


class ResourceSampler:
    def __init__(self, interval_s: float, *, probe: Probe = memory_snapshot) -> None:
        self._interval = max(0.0, float(interval_s))
        self._probe = probe
        self._samples: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def _take(self) -> None:
        try:
            memory, swap = self._probe()
        except Exception as exc:  # noqa: BLE001 - a probe failure must never fail the run
            memory, swap = {"supported": False, "reason": f"probe failed: {exc}"}, {}
        with self._lock:
            self._samples.append((time.monotonic() - self._started, memory, swap))

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._take()

    def start(self) -> ResourceSampler:
        if not self.enabled:
            return self
        self._started = time.monotonic()
        self._take()
        self._thread = threading.Thread(target=self._loop, name="resource-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any] | None:
        """Stop sampling and return the summary; ``None`` when sampling is disabled."""
        if not self.enabled or self._thread is None:
            return None
        self._stop.set()
        self._thread.join(timeout=5)
        self._take()
        with self._lock:
            samples = list(self._samples)
        return summarize(samples, self._interval)


def summarize(
    samples: list[tuple[float, dict[str, Any], dict[str, Any]]], interval_s: float
) -> dict[str, Any]:
    supported = [s for s in samples if s[1].get("supported")]
    if not supported:
        reason = next((s[1].get("reason") for s in samples if s[1].get("reason")), "no samples")
        return {"supported": False, "reason": reason, "samples": len(samples)}

    ram = [float(m.get("percent") or 0.0) for _, m, _ in supported]
    swap_used = [int(w.get("used_bytes") or 0) for _, _, w in supported if w.get("supported")]
    pressures = [m.get("pressure") for _, m, _ in supported]
    worst = max(pressures, key=lambda p: _PRESSURE_RANK.get(p, 0))
    growth = (swap_used[-1] - swap_used[0]) if len(swap_used) >= 2 else 0
    peak_growth = (max(swap_used) - swap_used[0]) if swap_used else 0
    pressured = sum(1 for p in pressures if _PRESSURE_RANK.get(p, 0) >= 2)

    reasons: list[str] = []
    if peak_growth >= SWAP_GROWTH_THRESHOLD_BYTES:
        reasons.append(f"swap grew by {peak_growth // (1024 * 1024)} MB during the run")
    if pressured:
        reasons.append(f"memory pressure was {worst} in {pressured} of {len(supported)} samples")

    step = max(
        1, len(supported) // MAX_SERIES_POINTS + (1 if len(supported) % MAX_SERIES_POINTS else 0)
    )
    series = [
        [
            round(t, 1),
            round(float(m.get("percent") or 0.0), 1),
            round(int(w.get("used_bytes") or 0) / 1024**2),
        ]
        for t, m, w in supported[::step]
    ]

    first_memory = supported[0][1]
    return {
        "supported": True,
        "interval_s": interval_s,
        "samples": len(supported),
        "duration_s": round(supported[-1][0], 1),
        "source": first_memory.get("source"),
        "memory": {
            "total_bytes": first_memory.get("total_bytes"),
            "peak_used_percent": round(max(ram), 1),
            "mean_used_percent": round(sum(ram) / len(ram), 1),
        },
        "swap": {
            "start_used_bytes": swap_used[0] if swap_used else None,
            "end_used_bytes": swap_used[-1] if swap_used else None,
            "peak_used_bytes": max(swap_used) if swap_used else None,
            "growth_bytes": growth if swap_used else None,
        },
        "pressure": {"worst": worst, "elevated_samples": pressured},
        "host_constrained": bool(reasons),
        "reasons": reasons,
        "note": (
            "Host-wide readings (every process, not only this run). "
            "When host_constrained is true, this run's timings may be inflated by swapping."
        ),
        # [seconds since start, RAM used %, swap used MB]
        "series": series,
    }
