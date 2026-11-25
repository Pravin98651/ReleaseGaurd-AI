"""Structured JSON logging with mandatory secret-scrubbing filter."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.core.security import redact_dict, scrub_secrets


class SecretScrubbingFilter(logging.Filter):
    """Logging filter that scrubs credentials, Fernet ciphertexts, and API keys."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: scrub_secrets(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    scrub_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        for attr in ("request_id", "details", "error", "headers"):
            if hasattr(record, attr):
                val = getattr(record, attr)
                if isinstance(val, str):
                    setattr(record, attr, scrub_secrets(val))
                elif isinstance(val, (dict, list)):
                    setattr(record, attr, redact_dict(val))
        return True


class JsonFormatter(logging.Formatter):
    """Minimal structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        message = scrub_secrets(record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        if record.exc_info:
            payload["exception"] = scrub_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str | None = None) -> None:
    """Configure root and application loggers with JSON output and secret scrubbing."""
    log_level_name = (level or get_settings().LOG_LEVEL or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Replace existing handlers cleanly to avoid duplicates across reloads
    root_logger.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(log_level)
    handler.addFilter(SecretScrubbingFilter())
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

    for noisy in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
