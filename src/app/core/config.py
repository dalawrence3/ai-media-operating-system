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
        self.tts_live_enabled: bool = os.environ.get(
            "ACE_TTS_LIVE_ENABLED", ""
        ).lower() in {"1", "true", "yes"}


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
