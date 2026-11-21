"""Host resource snapshot (RAM, swap, CPU) and Docker daemon status.

macOS is read through ``sysctl``/``vm_stat``/``ps`` and Linux through
``/proc``; ``psutil`` is used instead when it happens to be installed. Other
platforms report each metric as unsupported. Probes never use a shell.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

PROBE_TIMEOUT_S = 4.0
DOCKER_LABEL = "io.agentbench.managed=true"


def _run(argv: list[str], timeout: float = PROBE_TIMEOUT_S) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _percent(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def _unsupported(reason: str) -> dict[str, Any]:
    return {"supported": False, "reason": reason}


# --------------------------------------------------------------------------- #
_SWAP_RE = re.compile(r"(total|used|free)\s*=\s*([\d.]+)([KMGT])")
_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_vm_stat(text: str) -> dict[str, int]:
    """Parse ``vm_stat`` into page counts plus ``page_size``."""
    out: dict[str, int] = {}
    size = re.search(r"page size of (\d+) bytes", text)
    out["page_size"] = int(size.group(1)) if size else 4096
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().rstrip(".")
        if value.isdigit():
            out[key.strip().strip('"').lower()] = int(value)
    return out


def parse_swapusage(text: str) -> dict[str, int]:
    return {
        name: int(float(number) * _UNITS[unit]) for name, number, unit in _SWAP_RE.findall(text)
    }


def _darwin_memory() -> tuple[dict[str, Any], dict[str, Any]]:
    total_raw = _run(["sysctl", "-n", "hw.memsize"])
    vm_raw = _run(["vm_stat"])
    if not total_raw or not vm_raw:
        return _unsupported("sysctl/vm_stat unavailable"), _unsupported("sysctl unavailable")
    total = int(total_raw.strip())
    pages = parse_vm_stat(vm_raw)
    page = pages["page_size"]
    # Activity Monitor's "Memory Used" = app memory + wired + compressed.
    app = max(0, pages.get("anonymous pages", 0) - pages.get("pages purgeable", 0))
    used = (
        app + pages.get("pages wired down", 0) + pages.get("pages occupied by compressor", 0)
    ) * page
    pressure_raw = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
    pressure = {"1": "normal", "2": "warning", "4": "critical"}.get((pressure_raw or "").strip())
    memory = {
        "supported": True,
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": max(0, total - used),
        "percent": _percent(used, total),
        "compressed_bytes": pages.get("pages occupied by compressor", 0) * page,
        "wired_bytes": pages.get("pages wired down", 0) * page,
        "pressure": pressure,
        "source": "vm_stat (app + wired + compressed, as Activity Monitor reports 'Memory Used')",
    }
    swap_raw = _run(["sysctl", "vm.swapusage"])
    if swap_raw:
        values = parse_swapusage(swap_raw)
        swap: dict[str, Any] = {
            "supported": True,
            "total_bytes": values.get("total", 0),
            "used_bytes": values.get("used", 0),
            "percent": _percent(values.get("used"), values.get("total")),
            "source": "sysctl vm.swapusage",
        }
    else:
        swap = _unsupported("sysctl vm.swapusage unavailable")
    return memory, swap


def _darwin_cpu(cores: int) -> dict[str, Any]:
    raw = _run(["ps", "-A", "-o", "%cpu="])
    if raw is None:
        return {"utilization_percent": None, "utilization_note": "ps unavailable"}
    total = sum(float(v) for v in raw.split() if re.fullmatch(r"[\d.]+", v))
    return {
        "utilization_percent": round(min(100.0, total / max(1, cores)), 1),
        "utilization_note": (
            "estimate: sum of per-process decaying CPU averages from ps, divided by cores"
        ),
    }


def _linux_memory() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            info = {
                line.split(":")[0]: int(line.split()[1]) * 1024
                for line in handle
                if len(line.split()) >= 2 and line.split()[1].isdigit()
            }
    except OSError:
        return _unsupported("/proc/meminfo unreadable"), _unsupported("/proc/meminfo unreadable")
    total, available = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    swap_total, swap_free = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    memory = {
        "supported": True,
        "total_bytes": total,
        "used_bytes": total - available,
        "available_bytes": available,
        "percent": _percent(total - available, total),
        "pressure": None,
        "source": "/proc/meminfo (MemTotal - MemAvailable)",
    }
    swap = {
        "supported": True,
        "total_bytes": swap_total,
        "used_bytes": swap_total - swap_free,
        "percent": _percent(swap_total - swap_free, swap_total),
        "source": "/proc/meminfo",
    }
    return memory, swap


def _linux_cpu() -> dict[str, Any]:
    def sample() -> tuple[int, int] | None:
        try:
            with open("/proc/stat", encoding="ascii") as handle:
                fields = [int(v) for v in handle.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)

    first = sample()
    time.sleep(0.2)
    second = sample()
    if not first or not second or second[1] == first[1]:
        return {"utilization_percent": None, "utilization_note": "/proc/stat unreadable"}
    busy = 1 - (second[0] - first[0]) / (second[1] - first[1])
    return {
        "utilization_percent": round(100 * busy, 1),
        "utilization_note": "measured from /proc/stat over 200 ms",
    }


def _psutil_snapshot() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    memory = {
        "supported": True,
        "total_bytes": vm.total,
        "used_bytes": vm.total - vm.available,
        "available_bytes": vm.available,
        "percent": _percent(vm.total - vm.available, vm.total),
        "pressure": None,
        "source": "psutil",
    }
    swap = {
        "supported": True,
        "total_bytes": sw.total,
        "used_bytes": sw.used,
        "percent": _percent(sw.used, sw.total),
        "source": "psutil",
    }
    cpu = {
        "utilization_percent": psutil.cpu_percent(interval=0.2),
        "utilization_note": "psutil over 200 ms",
    }
    return memory, swap, cpu


def resource_snapshot() -> dict[str, Any]:
    cores = os.cpu_count() or 1
    try:
        load: list[float] | None = [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        load = None

    via_psutil = _psutil_snapshot()
    if via_psutil:
        memory, swap, cpu_extra = via_psutil
    elif sys.platform == "darwin":
        memory, swap = _darwin_memory()
        cpu_extra = _darwin_cpu(cores)
    elif sys.platform.startswith("linux"):
        memory, swap = _linux_memory()
        cpu_extra = _linux_cpu()
    else:
        memory = _unsupported(f"memory metrics are not implemented for {sys.platform}")
        swap = _unsupported(f"swap metrics are not implemented for {sys.platform}")
        cpu_extra = {"utilization_percent": None, "utilization_note": "unsupported platform"}

    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "memory": memory,
        "swap": swap,
        "cpu": {
            "cores": cores,
            "load_average": load,
            "load_per_core": round(load[0] / cores, 2) if load else None,
            **cpu_extra,
        },
        "captured_at": datetime.now(UTC).isoformat(),
    }


def memory_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    """Memory and swap only: the cheap probe used while runs execute (no CPU sampling)."""
    via_psutil = _psutil_snapshot()
    if via_psutil:
        return via_psutil[0], via_psutil[1]
    if sys.platform == "darwin":
        return _darwin_memory()
    if sys.platform.startswith("linux"):
        return _linux_memory()
    reason = f"memory metrics are not implemented for {sys.platform}"
    return _unsupported(reason), _unsupported(reason)


# --------------------------------------------------------------------------- #
def _json_lines(text: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def docker_status(binary: str) -> dict[str, Any]:
    """Daemon facts plus live stats for this platform's own sandbox containers."""
    info_raw = _run(
        [
            binary,
            "info",
            "--format",
            '{"server":"{{.ServerVersion}}","cpus":{{.NCPU}},"mem":{{.MemTotal}},'
            '"running":{{.ContainersRunning}},"images":{{.Images}}}',
        ]
    )
    if info_raw is None:
        return {
            "available": False,
            "detail": f"`{binary} info` failed or timed out; is Docker Desktop running?",
            "sandboxes": [],
        }
    info = _json_lines(info_raw)
    facts = info[0] if info else {}
    containers = _json_lines(
        _run([binary, "ps", "--filter", f"label={DOCKER_LABEL}", "--format", "{{json .}}"])
    )
    stats: dict[str, dict[str, Any]] = {}
    if containers:
        ids = [str(c.get("ID")) for c in containers if c.get("ID")]
        for row in _json_lines(
            _run([binary, "stats", "--no-stream", "--format", "{{json .}}", *ids], timeout=8.0)
        ):
            stats[str(row.get("ID", ""))[:12]] = row
    sandboxes = []
    for container in containers:
        cid = str(container.get("ID", ""))[:12]
        live = stats.get(cid, {})
        labels = str(container.get("Labels", ""))
        run_id = next(
            (
                part.split("=", 1)[1]
                for part in labels.split(",")
                if part.startswith("io.agentbench.run_id=")
            ),
            None,
        )
        sandboxes.append(
            {
                "id": cid,
                "name": container.get("Names"),
                "image": container.get("Image"),
                "status": container.get("Status"),
                "run_id": run_id,
                "cpu_percent": live.get("CPUPerc"),
                "memory_usage": live.get("MemUsage"),
                "memory_percent": live.get("MemPerc"),
            }
        )
    return {
        "available": True,
        "server_version": facts.get("server"),
        "cpus": facts.get("cpus"),
        "memory_bytes": facts.get("mem"),
        "containers_running": facts.get("running"),
        "images": facts.get("images"),
        "sandboxes": sandboxes,
        "detail": "docker info",
    }
