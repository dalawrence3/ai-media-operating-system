"""Structured JSON logging via structlog with sensitive field redaction.

Security invariant: the SensitiveFieldFilter processor drops or redacts any
event dict key whose name matches the SENSITIVE_FIELDS set before the log
entry is emitted. This prevents accidental logging of credentials.

Fields redacted (value replaced with '<redacted>'):
  password, password_hash, secret_key, access_token, refresh_token,
  token, api_key, authorization, x-api-key, db_password, credentials,
  client_secret, private_key, jwt, bearer, cookie, session_token.

Usage:
  from app.observability.logging_config import configure_logging, get_logger
  configure_logging(log_level="INFO", json_logs=True)
  logger = get_logger(__name__)
  logger.info("pipeline.stage.dispatched", pipeline_id="p-1", stage="research")
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# Keys whose values are NEVER emitted in logs (case-insensitive match).
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "hashed_password",
        "secret_key",
        "secret",
        "access_token",
        "refresh_token",
        "token",
        "token_hash",
        "api_key",
        "apikey",
        "authorization",
        "x-api-key",
        "x_api_key",
        "db_password",
        "database_password",
        "credentials",
        "client_secret",
        "private_key",
        "jwt",
        "bearer",
        "cookie",
        "session_token",
        "auth_token",
        "elevenlabs_api_key",
        "anthropic_api_key",
        "object_storage_secret_key",
        "object_storage_access_key",
    }
)


def _redact_sensitive(logger: Any, method: str, event_dict: dict) -> dict:
    """Structlog processor: redact sensitive fields before emission."""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "<redacted>"
    return event_dict


_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    _redact_sensitive,
]


def configure_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog and stdlib logging.

    Call once at application startup (e.g., in FastAPI lifespan or main.py).
    json_logs=True emits JSON (production); False emits coloured console (dev).
    """
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level_int)

    # Quiet noisy libraries in production
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to `name`."""
    return structlog.get_logger(name)
