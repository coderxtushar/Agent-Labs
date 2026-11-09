"""Redaction applied to everything persisted in the event stream.

Two layers:

* **Known secrets** - exact values from settings (provider keys, API token) are
  replaced wherever they appear.
* **Shaped secrets** - values that look like credentials (provider key prefixes,
  bearer tokens, PEM blocks, ``KEY=value`` assignments for sensitive-looking
  names) are masked even if the platform has never seen them, because adapter
  config and agent output are not fully under our control.

Redaction is applied to keys and values recursively before anything reaches the
database.  It is deliberately conservative: over-masking a log line is cheap,
leaking a key is not.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***REDACTED***"

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key|authorization|bearer)",
    re.IGNORECASE,
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Provider-style keys: sk-..., sk-ant-..., ghp_..., gho_..., xoxb-...
    re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{12,}|sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Authorization headers.
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{12,}"),
    # KEY=value / "key": "value" assignments with a sensitive-looking name.
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)[A-Z0-9_]*)"
        r"(\s*[:=]\s*)(\"?)([^\s\"',}]{6,})"
    ),
    # PEM blocks.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def _mask_assignment(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{match.group(3)}{MASK}"


def redact_text(text: str, known_secrets: list[str] | None = None) -> str:
    """Mask known and credential-shaped secrets in a string."""
    if not text:
        return text
    for secret in known_secrets or []:
        if secret and len(secret) >= 8 and secret in text:
            text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        if pattern.groups == 4:
            text = pattern.sub(_mask_assignment, text)
        else:
            text = pattern.sub(MASK, text)
    return text


def redact_value(value: Any, known_secrets: list[str] | None = None, *, _depth: int = 0) -> Any:
    """Recursively redact a JSON-compatible structure.

    A mapping key that looks sensitive has its whole value masked, regardless of
    the value's shape.
    """
    if _depth > 12:
        return "***DEPTH_LIMIT***"
    if isinstance(value, str):
        return redact_text(value, known_secrets)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                out[key_str] = MASK if item is not None else None
            else:
                out[key_str] = redact_value(item, known_secrets, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(item, known_secrets, _depth=_depth + 1) for item in value]
    return value


def active_secrets() -> list[str]:
    from agentbench.config import get_settings

    return get_settings().secret_values()
