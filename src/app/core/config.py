"""Application configuration sourced from environment variables and defaults."""

from __future__ import annotations

import os
from pathlib import Path


def _default_db_path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "ai-content-engine" / "content.db"


class Config:
    def __init__(self) -> None:
        raw = os.environ.get("ACE_DB_PATH", "")
        self.db_path: Path = Path(raw) if raw else _default_db_path()
        self.log_level: str = os.environ.get("ACE_LOG_LEVEL", "WARNING").upper()

        # Runtime environment: "development", "staging", or "production".
        # Controls dev-auth availability, CORS localhost origins, and startup strictness.
        # Default is "production" — never silently fall back to a permissive mode.
        self.ace_env: str = os.environ.get("ACE_ENV", "production").lower()

        # Dev auth gate (only honoured when ace_env == "development").
        self.dev_auth_enabled: bool = (
            os.environ.get("ACE_DEV_AUTH", "enabled") == "enabled" and self.ace_env == "development"
        )

        # Phase 15 — PostgreSQL production database URL.
        # When set, overrides ACE_DB_PATH and uses PostgreSQL instead of SQLite.
        # Format: postgresql://user:password@host:5432/dbname
        # Unset (default) → SQLite at db_path (dev/test behaviour unchanged).
        self.database_url: str = os.environ.get("ACE_DATABASE_URL", "")

        # Phase 15 — Redis URL for job queue and rate limiting.
        self.redis_url: str = os.environ.get("ACE_REDIS_URL", "redis://localhost:6379/0")

        # Phase 15 — JWT signing secret. Production MUST set this to a random ≥32-byte value.
        # Empty string means auth is not configured; endpoints requiring auth will reject requests.
        self.secret_key: str = os.environ.get("ACE_SECRET_KEY", "")

        # Phase 15 — JWT token lifetimes (seconds).
        self.access_token_expire_seconds: int = int(
            os.environ.get("ACE_ACCESS_TOKEN_EXPIRE_SECONDS", "900")
        )
        self.refresh_token_expire_seconds: int = int(
            os.environ.get("ACE_REFRESH_TOKEN_EXPIRE_SECONDS", str(7 * 24 * 3600))
        )

        # Phase 15 — Object storage backend: "local" or "s3".
        self.object_storage_backend: str = os.environ.get(
            "ACE_OBJECT_STORAGE_BACKEND", "local"
        ).lower()
        raw_bucket = os.environ.get("ACE_OBJECT_STORAGE_BUCKET", "")
        self.object_storage_bucket: str = raw_bucket or "ace-artifacts"
        self.object_storage_endpoint: str = os.environ.get("ACE_OBJECT_STORAGE_ENDPOINT", "")
        self.object_storage_access_key: str = os.environ.get("ACE_OBJECT_STORAGE_ACCESS_KEY", "")
        self.object_storage_secret_key: str = os.environ.get("ACE_OBJECT_STORAGE_SECRET_KEY", "")
        self.object_storage_region: str = os.environ.get("ACE_OBJECT_STORAGE_REGION", "us-east-1")

        # Phase 15 — CORS origins (comma-separated, supplements hard-coded dev origins).
        self.cors_origins: list[str] = [
            o.strip() for o in os.environ.get("ACE_CORS_ORIGINS", "").split(",") if o.strip()
        ]

        # AI / LLM settings (Phase 2).
        # The application starts and runs non-AI commands without any of these.
        self.anthropic_api_key: str = os.environ.get("ACE_ANTHROPIC_API_KEY", "")
        self.ai_provider: str = os.environ.get("ACE_AI_PROVIDER", "fake").lower()
        self.ai_model: str = os.environ.get("ACE_AI_MODEL", "claude-sonnet-5")
        self.ai_timeout: float = float(os.environ.get("ACE_AI_TIMEOUT", "30"))
        self.ai_max_retries: int = int(os.environ.get("ACE_AI_MAX_RETRIES", "3"))
        # ACE_DRY_RUN=1 forces the fake provider regardless of ACE_AI_PROVIDER.
        self.dry_run: bool = os.environ.get("ACE_DRY_RUN", "").lower() in {"1", "true", "yes"}

        # TTS / narration settings (Phase 6 M6.2).
        # ACE_ARTIFACTS_PATH: root directory for audio artifacts (defaults to ./artifacts).
        raw_artifacts = os.environ.get("ACE_ARTIFACTS_PATH", "")
        self.artifacts_path: Path = Path(raw_artifacts) if raw_artifacts else Path("artifacts")
        self.tts_provider: str = os.environ.get("ACE_TTS_PROVIDER", "fake").lower()
        self.tts_model: str = os.environ.get("ACE_TTS_MODEL", "fake/FAKE")

        # ElevenLabs TTS credentials (Phase 6 M6.3C).
        # The API key is never stored in the DB or logs.
        # tts_live_enabled must be explicitly set to true before any live call is made.
        self.elevenlabs_api_key: str = os.environ.get("ACE_ELEVENLABS_API_KEY", "")
        self.tts_live_enabled: bool = os.environ.get("ACE_TTS_LIVE_ENABLED", "").lower() in {
            "1",
            "true",
            "yes",
        }

        # Publishing safety gate (Phase 9).
        # Must be explicitly set to true before any live provider upload is made.
        # Default is false — all publishing uses the fake provider in development and CI.
        self.publishing_live_enabled: bool = os.environ.get(
            "ACE_PUBLISHING_LIVE_ENABLED", ""
        ).lower() in {"1", "true", "yes"}

        # Release gate (Phase 19).
        # Must be explicitly set to true AND ACE_PUBLISHING_LIVE_ENABLED=true before any
        # live privacy-status update is made. Default is false.
        self.release_public_enabled: bool = os.environ.get(
            "ACE_RELEASE_PUBLIC_ENABLED", ""
        ).lower() in {"1", "true", "yes"}

        # YouTube OAuth 2.0 configuration (YouTube account connection milestone).
        # YOUTUBE_CLIENT_SECRETS_PATH: path to client_secret.json from Google Cloud Console.
        #   Never commit this file. Never store its contents in the DB.
        # ACE_YOUTUBE_REDIRECT_URI: must match a URI registered in Google Cloud Console.
        #   Local default: http://localhost:8000/api/v1/oauth/youtube/callback
        # ACE_YOUTUBE_TOKEN_DIR: directory where per-account OAuth token files are stored.
        #   Files are created as {token_dir}/{account_id}.json with 0o600 permissions.
        #   The path (external_ref) is stored in cp_credential_profiles; tokens are NOT in DB.
        self.youtube_client_secrets_path: str = os.environ.get("YOUTUBE_CLIENT_SECRETS_PATH", "")
        self.youtube_redirect_uri: str = os.environ.get(
            "ACE_YOUTUBE_REDIRECT_URI",
            "http://localhost:8000/api/v1/oauth/youtube/callback",
        )
        _default_token_dir = str(
            Path(os.environ.get("XDG_DATA_HOME", "") or (Path.home() / ".local/share"))
            / "ai-content-engine"
            / "oauth_tokens"
        )
        self.youtube_token_dir: str = os.environ.get("ACE_YOUTUBE_TOKEN_DIR", _default_token_dir)

        # Frontend origin for post-OAuth browser redirects.
        # In local dev: set to http://localhost:5173 (Vite dev server).
        #   The Google callback lands on :8000 (FastAPI); without this the browser
        #   stays on :8000 where no SPA routes exist, resulting in 404.
        # In production: leave empty — frontend and backend share the same origin so
        #   the relative redirects produced by the callback route are correct.
        self.frontend_url: str = os.environ.get("ACE_FRONTEND_URL", "").rstrip("/")


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """Reset the singleton — for use in tests only."""
    global _config
    _config = None
