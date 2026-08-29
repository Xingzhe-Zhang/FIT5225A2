from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


SENSITIVE_FRAGMENTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "private_key",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_FRAGMENTS)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        context = getattr(record, "context", None)
        if request_id is not None:
            payload["request_id"] = request_id
        if context is not None:
            payload["context"] = redact_sensitive(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact_sensitive(payload), ensure_ascii=False, default=str)
