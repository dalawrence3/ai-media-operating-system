"""Provider secrets and boundary gates (M15.6).

Architecture:
  - Provider API keys live ONLY in environment variables — never in the database.
  - SecretsInterface reads named env vars at call time (not at import time).
  - ProviderBoundary enforces Class A/B/C stage classification before any live call.
  - All values are redacted before logging; this module never emits secret values.
"""

from app.providers.boundaries import ProviderBoundary, StageClass
from app.providers.secrets import SecretNotConfiguredError, SecretsInterface

__all__ = [
    "ProviderBoundary",
    "SecretNotConfiguredError",
    "SecretsInterface",
    "StageClass",
]
