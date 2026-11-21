"""Host memory/swap sampling during runs: the summary must flag real swapping only."""

from __future__ import annotations

import time
from typing import Any

from agentbench.monitor.sampler import (
    SWAP_GROWTH_THRESHOLD_BYTES,
    ResourceSampler,
    summarize,
)

MB = 1024 * 1024


def _mem(percent: float, pressure: str | None = "normal") -> dict[str, Any]:
    return {
        "supported": True,
        "percent": percent,
        "total_bytes": 16 * 1024 * MB,
        "pressure": pressure,
        "source": "test",
    }


def _swap(used_mb: int) -> dict[str, Any]:
    return {"supported": True, "used_bytes": used_mb * MB, "total_bytes": 4096 * MB}


def test_steady_host_is_not_flagged() -> None:
    summary = summarize([(0.0, _mem(60), _swap(500)), (10.0, _mem(62), _swap(520))], 10)
    assert summary["supported"] is True
    assert summary["host_constrained"] is False and summary["reasons"] == []
    assert summary["memory"]["peak_used_percent"] == 62.0
    assert summary["swap"]["growth_bytes"] == 20 * MB
    assert summary["series"] == [[0.0, 60.0, 500], [10.0, 62.0, 520]]


def test_swap_growth_beyond_the_threshold_flags_the_run() -> None:
    grown = SWAP_GROWTH_THRESHOLD_BYTES // MB + 10
    summary = summarize([(0.0, _mem(80), _swap(100)), (5.0, _mem(95), _swap(100 + grown))], 5)
    assert summary["host_constrained"] is True
    assert any("swap grew" in reason for reason in summary["reasons"])


def test_memory_pressure_flags_the_run_even_without_swap_growth() -> None:
    summary = summarize(
        [(0.0, _mem(90, "normal"), _swap(100)), (5.0, _mem(97, "warning"), _swap(100))], 5
    )
    assert summary["host_constrained"] is True
    assert summary["pressure"] == {"worst": "warning", "elevated_samples": 1}


def test_unsupported_platform_is_reported_not_guessed() -> None:
    unsupported = {"supported": False, "reason": "not implemented for plan9"}
    summary = summarize([(0.0, unsupported, unsupported)], 10)
    assert summary == {"supported": False, "reason": "not implemented for plan9", "samples": 1}


def test_long_runs_are_downsampled_to_a_bounded_series() -> None:
    samples = [(float(i), _mem(50), _swap(10)) for i in range(1000)]
    summary = summarize(samples, 1)
    assert summary["samples"] == 1000
    assert len(summary["series"]) <= 240


def test_disabled_sampler_records_nothing() -> None:
    sampler = ResourceSampler(0, probe=lambda: (_mem(1), _swap(1))).start()
    assert sampler.stop() is None


def test_the_sampler_thread_collects_readings_until_stopped() -> None:
    calls: list[float] = []

    def probe() -> tuple[dict[str, Any], dict[str, Any]]:
        calls.append(time.monotonic())
        return _mem(50), _swap(10)

    sampler = ResourceSampler(0.02, probe=probe).start()
    time.sleep(0.15)
    summary = sampler.stop()
    assert summary is not None and summary["supported"] is True
    assert summary["samples"] >= 4  # first + periodic + final
    count = len(calls)
    time.sleep(0.06)
    assert len(calls) == count, "the thread kept sampling after stop()"


def test_a_failing_probe_never_raises() -> None:
    def broken() -> tuple[dict[str, Any], dict[str, Any]]:
        raise RuntimeError("vm_stat exploded")

    sampler = ResourceSampler(0.01, probe=broken).start()
    summary = sampler.stop()
    assert summary is not None and summary["supported"] is False
    assert "vm_stat exploded" in summary["reason"]
