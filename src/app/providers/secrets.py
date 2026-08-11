"""Read-only environment-variable secrets interface.

Security invariants:
  - All provider API keys and secrets live ONLY in environment variables.
  - Values are NEVER stored in the database, logged, or included in error messages.
  - get() raises SecretNotConfiguredError (not returns None) when a required secret
    is missing — callers cannot silently proceed with an empty string.
  - redact() produces a safe display string for logging: show first 4 chars + "***".
"""

from __future__ import annotations

import os


class SecretNotConfiguredError(Exception):
    """Raised when a required provider secret is not set in the environment."""


class SecretsInterface:
    """Thin, read-only facade over environment variables for provider credentials.

    Usage:
        secrets = SecretsInterface()
        key = secrets.get("ACE_ANTHROPIC_API_KEY", required=True)

    Values are read at call time — no caching — so secrets can be rotated
    without restarting the process (for long-running worker processes).
    """

    # Registry of known provider secret env var names and their display labels.
    # Never put actual values here.
    KNOWN_SECRETS: dict[str, str] = {
        "ACE_ANTHROPIC_API_KEY": "Anthropic API key",
        "ACE_ELEVENLABS_API_KEY": "ElevenLabs API key",
        "ACE_SECRET_KEY": "JWT signing secret",
        "ACE_OBJECT_STORAGE_ACCESS_KEY": "Object storage access key",
        "ACE_OBJECT_STORAGE_SECRET_KEY": "Object storage secret key",
        # YouTube OAuth — path to client secrets JSON, never the secret values themselves.
        "YOUTUBE_CLIENT_SECRETS_PATH": "YouTube OAuth client secrets file path",
    }

    def get(self, env_var: str, *, required: bool = False) -> str:
        """Return the env var value.

        Raises SecretNotConfiguredError if required=True and the var is unset/empty.
        Returns empty string when required=False and the var is unset.
        Never logs the returned value.
        """
        value = os.environ.get(env_var, "")
        if required and not value:
            label = self.KNOWN_SECRETS.get(env_var, env_var)
            raise SecretNotConfiguredError(
                f"Required provider secret is not configured: {label}. "
                f"Set {env_var} in the environment before enabling live provider calls."
            )
        return value

    def is_configured(self, env_var: str) -> bool:
        """Return True if the env var is set to a non-empty value."""
        return bool(os.environ.get(env_var, ""))

    @staticmethod
    def redact(value: str, *, prefix_chars: int = 4) -> str:
        """Return a safe display string for logging (never log the raw value).

        Returns first `prefix_chars` characters + '***', or '***' if too short.
        """
        if not value:
            return "<not set>"
        if len(value) <= prefix_chars:
            return "***"
        return value[:prefix_chars] + "***"

    def status(self) -> dict[str, bool]:
        """Return configuration status for all known secrets (True = configured).

        Safe to log — values are boolean, not the secrets themselves.
        """
        return {env_var: self.is_configured(env_var) for env_var in self.KNOWN_SECRETS}
