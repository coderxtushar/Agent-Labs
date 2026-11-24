"""Static, read-only snapshot of the API for hosting the UI without a backend.

``agentbench snapshot export`` replays every read the UI makes against the real
FastAPI app, in-process, and writes each response byte-for-byte to disk with a
manifest. The frontend built in snapshot mode (``npm run build:snapshot``) reads
those files instead of calling an API, so a static host (Cloudflare Pages,
Netlify, Vercel) can serve real recorded results with no server, no Docker and
no model.

Nothing is recomputed here: every metric, interval and provenance label comes
from the same endpoint the live UI calls. The only logic the browser adds is
filtering and paging of the five list endpoints, whose full contents are
exported as *collections* (see ``frontend/src/api/snapshot.ts``).

Request keys are ``<path>`` or ``<path>?<k>=<v>&...`` with parameters sorted by
name, raw (not URL-encoded) values, booleans as ``true``/``false``, and empty
values dropped. ``snapshot.ts`` builds the same key; change both together.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path
from typing import Any, Protocol

from agentbench import __version__

API_PREFIX = "/api/v1"
SNAPSHOT_FORMAT = 1
MANIFEST = "manifest.json"
MISSING_NOTE = "not-in-snapshot.txt"
#: The API's maximum page size (``api/deps.py``); collections page through it.
MAX_PAGE = 200
#: The run timeline the UI requests (``useRunEvents``).
EVENTS_LIMIT = 2000

_EXTENSIONS = {
    "application/json": ".json",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/plain": ".txt",
}

ParamValue = str | int | bool | None


class SnapshotError(RuntimeError):
    """The export could not produce a faithful snapshot."""


class _Response(Protocol):
    status_code: int
    content: bytes
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class _Client(Protocol):
    def get(self, url: str, params: Any = None) -> _Response: ...


def _param(value: ParamValue) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def request_key(path: str, params: Mapping[str, ParamValue] | None = None) -> str:
    """Canonical key for one GET; ``snapshot.ts`` must compute the same string."""
    pairs = sorted(
        (name, text)
        for name, value in (params or {}).items()
        if (text := _param(value)) is not None
    )
    if not pairs:
        return path
    return path + "?" + "&".join(f"{name}={text}" for name, text in pairs)


@dataclass
class SnapshotWriter:
    """Writes responses and collections under ``out_dir`` and records them."""

    client: _Client
    out_dir: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    collections: dict[str, str] = field(default_factory=dict)
    home: str = field(default_factory=lambda: str(Path.home()) if Path.home() != Path("/") else "")
    _files: dict[str, str] = field(default_factory=dict, init=False)

    def _file_for(self, key: str, extension: str) -> str:
        name = f"files/{hashlib.sha256(key.encode()).hexdigest()[:20]}{extension}"
        owner = self._files.setdefault(name, key)
        if owner != key:  # pragma: no cover - 80-bit prefix; guarded, not expected
            raise SnapshotError(f"file name collision between {owner!r} and {key!r}")
        return name

    def _write(self, name: str, content: bytes) -> None:
        # The only machine detail found in responses is the home directory inside
        # paths (e.g. the Docker socket in an error message); publish it as "~".
        if self.home:
            content = content.replace(self.home.encode(), b"~")
        target = self.out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def get(self, path: str, params: Mapping[str, ParamValue] | None = None) -> _Response:
        """Record one GET exactly as the API answered it, errors included.

        A 4xx is part of what the UI would see (an incompatible comparison, a run
        without a patch) and is stored with its status. A 5xx means the export
        itself is broken, so it stops rather than publish a server crash.
        """
        key = request_key(path, params)
        sent = {name: text for name, value in (params or {}).items() if (text := _param(value))}
        response = self.client.get(API_PREFIX + path, params=sent or None)
        if response.status_code >= 500:
            raise SnapshotError(f"GET {key} returned {response.status_code}")
        content_type = response.headers.get("content-type", "application/json").split(";")[0]
        name = self._file_for(key, _EXTENSIONS.get(content_type.strip(), ".txt"))
        self._write(name, response.content)
        self.entries[key] = {"file": name, "status": response.status_code, "type": content_type}
        return response

    def get_json(self, path: str, params: Mapping[str, ParamValue] | None = None) -> Any:
        response = self.get(path, params)
        if response.status_code != 200:
            raise SnapshotError(f"GET {request_key(path, params)} returned {response.status_code}")
        return response.json()

    def collection(
        self, path: str, params: Mapping[str, ParamValue] | None = None
    ) -> list[dict[str, Any]]:
        """Every item of a paginated list, in the server's order, as one file.

        The browser filters and pages this; extra top-level fields of the last
        page (none today) are not needed because they are recomputed there.
        """
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = {**(params or {}), "limit": MAX_PAGE, "offset": offset}
            response = self.client.get(API_PREFIX + path, params=query)
            if response.status_code != 200:
                raise SnapshotError(f"GET {path} (offset {offset}) returned {response.status_code}")
            page = response.json()
            items.extend(page["items"])
            if not page["pagination"]["has_more"]:
                break
            offset += MAX_PAGE
        name = self._file_for(f"collection:{path}", ".json")
        body = {"items": items, "total": len(items), "source": f"GET {path}, every page"}
        self._write(name, json.dumps(body, separators=(",", ":")).encode())
        self.collections[path] = name
        return items


def _prepare(out_dir: Path) -> None:
    """Empty ``out_dir``, but only if it is empty already or a previous snapshot."""
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / MANIFEST).is_file():
            raise SnapshotError(
                f"{out_dir} is not empty and holds no {MANIFEST}; refusing to overwrite it"
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)


def export_snapshot(
    client: _Client,
    out_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Export every read the UI makes; return the manifest that was written."""
    _prepare(out_dir)
    writer = SnapshotWriter(client=client, out_dir=out_dir)

    # Platform-wide, parameter-free views.
    for path in (
        "/health",
        "/system/stats",
        "/system/settings",
        "/system/capabilities",
        "/overview",
        "/analytics",
        "/published-results",
        "/tasks/benchmarks",
        "/agents/adapters",
    ):
        writer.get(path)
    # Not exported: /system/resources, /system/docker and /system/local-models
    # describe the live machine; frozen, they would only show the export host.
    for fmt in ("json", "jsonl"):
        writer.get("/tasks/export", {"format": fmt})

    # Lists the browser filters itself.
    tasks = writer.collection("/tasks")
    experiments = writer.collection("/experiments", {"sort": "newest"})
    runs = writer.collection("/runs")
    writer.collection("/failures")

    agents = writer.get_json("/agents", {"limit": 200})["items"]
    for agent in agents:
        writer.get(f"/agents/{agent['id']}/profile")

    for task in tasks:
        writer.get(f"/tasks/{task['key']}")
        writer.get(f"/tasks/{task['key']}/history")

    benchmark_ids = sorted({str(task["benchmark_id"]) for task in tasks})
    for benchmark_id in [None, *benchmark_ids]:
        for include_non_model in (None, True):
            writer.get(
                "/leaderboard",
                {"benchmark_id": benchmark_id, "include_non_model": include_non_model},
            )

    experiment_ids = [str(experiment["id"]) for experiment in experiments]
    for experiment_id in experiment_ids:
        writer.get(f"/experiments/{experiment_id}")
        writer.get(f"/experiments/{experiment_id}/metrics")
        writer.collection(f"/experiments/{experiment_id}/runs")
        for fmt in ("markdown", "csv", "json"):
            writer.get(f"/experiments/{experiment_id}/report", {"format": fmt})

    # Every ordered pair: the two-arm view, its report, and the matrix view.
    # Larger matrices (3-6 arms) grow combinatorially and are not exported.
    for baseline, candidate in permutations(experiment_ids, 2):
        writer.get("/compare", {"baseline": baseline, "candidate": candidate})
        writer.get("/compare/report", {"baseline": baseline, "candidate": candidate})
        writer.get("/compare/matrix", {"experiment_ids": f"{baseline},{candidate}"})

    for run in runs:
        writer.get(f"/runs/{run['id']}")
        writer.get(f"/runs/{run['id']}/events", {"limit": EVENTS_LIMIT})
        # Also without a patch: the Patch tab asks regardless and shows the 404.
        writer.get(f"/runs/{run['id']}/patch")

    (out_dir / MISSING_NOTE).write_text(
        "This file is not part of the Agent Labs static snapshot.\n"
        "Run Agent Labs locally to download it: "
        "https://github.com/coderxtushar/Agent-Labs\n",
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "agentlabs_version": __version__,
        "counts": {
            "tasks": len(tasks),
            "experiments": len(experiments),
            "runs": len(runs),
            "agents": len(agents),
            "responses": len(writer.entries),
        },
        "notes": [
            "Read-only export of a local Agent Labs instance; nothing here is live.",
            "Comparison matrices are exported for pairs only.",
            "Host monitor views (RAM, Docker, Ollama) are live-only and not exported.",
        ],
        "entries": dict(sorted(writer.entries.items())),
        "collections": dict(sorted(writer.collections.items())),
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return manifest


def snapshot_size(out_dir: Path) -> int:
    return sum(path.stat().st_size for path in _files_under(out_dir))


def _files_under(root: Path) -> Iterable[Path]:
    return (path for path in root.rglob("*") if path.is_file())
