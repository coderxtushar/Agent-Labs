"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class RedactingFilter(logging.Filter):
    """Masks configured secret values anywhere in a formatted log record."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        for secret in self._secrets:
            if secret in msg:
                msg = msg.replace(secret, "***REDACTED***")
                record.msg = msg
                record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)-34s %(message)s", "%H:%M:%S")


_configured = False


def setup_logging(level: str = "INFO", fmt: str = "json", secrets: list[str] | None = None) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    handler.addFilter(RedactingFilter(secrets or []))
    root.addHandler(handler)

    logging.getLogger("uvicorn.access").propagate = False
    _configured = True


def ensure_logging() -> None:
    if not _configured:
        from agentbench.config import get_settings

        s = get_settings()
        setup_logging(s.log_level, s.log_format, s.secret_values())


class _SafeLogger(logging.LoggerAdapter):
    """Renames ``extra`` keys that collide with ``LogRecord`` attributes.

    ``logging`` raises ``KeyError`` if an extra shadows a built-in field such as
    ``created``, ``module`` or ``args``. Structured payloads here are built from
    domain dictionaries, so collisions are a matter of when, not if; renaming is
    preferable to losing the log line.
    """

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = kwargs.get("extra")
        if isinstance(extra, dict):
            kwargs["extra"] = {
                (f"x_{key}" if key in _RESERVED or key == "message" else key): value
                for key, value in extra.items()
            }
        return msg, kwargs


def get_logger(name: str) -> logging.Logger:
    ensure_logging()
    return _SafeLogger(logging.getLogger(name), {})  # type: ignore[return-value]
