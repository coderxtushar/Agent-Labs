"""Read-only view of a local Ollama server (installed and loaded models).

Only ``GET /api/version``, ``/api/tags`` and ``/api/ps`` are called. Pulling,
deleting or stopping models is deliberately not exposed: those change the
user's machine and stay a manual ``ollama`` CLI action.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_S = 2.5


def _get(client: httpx.Client, url: str) -> dict[str, Any] | None:
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def normalise_name(name: str | None) -> str:
    """``foo`` and ``foo:latest`` name the same Ollama model."""
    if not name:
        return ""
    return name if ":" in name else f"{name}:latest"


def ollama_status(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    with httpx.Client(timeout=TIMEOUT_S) as client:
        version = _get(client, f"{base}/api/version")
        if version is None:
            return {
                "reachable": False,
                "base_url": base,
                "detail": "no response; start it with `ollama serve` or `brew services start ollama`",
                "models": [],
                "running_count": 0,
            }
        tags = _get(client, f"{base}/api/tags") or {}
        loaded = _get(client, f"{base}/api/ps") or {}

    running = {
        normalise_name(str(item.get("name") or item.get("model"))): item
        for item in loaded.get("models") or []
        if isinstance(item, dict)
    }
    models: list[dict[str, Any]] = []
    for item in tags.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "")
        details = item.get("details") or {}
        live = running.get(normalise_name(name))
        models.append(
            {
                "name": name,
                "size_bytes": item.get("size"),
                "modified_at": item.get("modified_at"),
                "digest": str(item.get("digest") or "")[:12] or None,
                "family": details.get("family"),
                "parent_model": details.get("parent_model") or None,
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "format": details.get("format"),
                "max_context_length": details.get("context_length"),
                "capabilities": item.get("capabilities"),
                "running": live is not None,
                "loaded": (
                    {
                        "size_bytes": live.get("size"),
                        "vram_bytes": live.get("size_vram"),
                        "context_length": live.get("context_length"),
                        "expires_at": live.get("expires_at"),
                    }
                    if live
                    else None
                ),
            }
        )
    models.sort(key=lambda m: str(m["name"]))
    return {
        "reachable": True,
        "base_url": base,
        "version": version.get("version"),
        "models": models,
        "running_count": len(running),
        "detail": "GET /api/tags and /api/ps",
    }
