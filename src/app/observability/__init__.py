"""Observability: structured logging, Prometheus metrics, health checks (M15.9).

Security invariant: structured logs must NEVER contain passwords, JWTs,
refresh tokens, API keys, OAuth secrets, Authorization headers, storage
credentials, or DB passwords. The SensitiveFieldFilter enforces this at
the structlog processor level.
"""

from app.observability.logging_config import configure_logging, get_logger
from app.observability.metrics import METRICS, get_registry

__all__ = [
    "METRICS",
    "configure_logging",
    "get_logger",
    "get_registry",
]
