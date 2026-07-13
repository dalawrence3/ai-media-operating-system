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
