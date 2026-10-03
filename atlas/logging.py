"""JSON logging for Atlas-owned application loggers."""

import json
import logging
from datetime import datetime, timezone
from typing import Any


_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_SENSITIVE_PARTS = ("authorization", "credential", "password", "secret", "token")


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def _safe_value(value: Any, key: str = "") -> Any:
    if key and _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _safe_value(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Render one machine-readable JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = _safe_value(value, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure the ``atlas`` logger without replacing third-party handlers."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger("atlas")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
