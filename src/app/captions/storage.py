"""Phase 6 M6.3A: Caption artifact storage utilities."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from app.captions.errors import CaptionStorageError


def resolve_artifacts_path(artifacts_path: Path) -> Path:
    """Return the resolved, absolute artifacts root, creating it if needed."""
    p = artifacts_path.resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CaptionStorageError(f"Cannot create artifacts directory {p}: {exc}") from exc
    return p


def caption_dir(artifacts_path: Path, plan_id: int, narration_run_id: int, run_id: int) -> Path:
    """Return the directory path for a caption run's export files."""
    return (
        artifacts_path
        / "captions"
        / f"plan_{plan_id}"
        / f"narration_{narration_run_id}"
        / f"run_{run_id}"
    )


def srt_path(artifacts_path: Path, plan_id: int, narration_run_id: int, run_id: int) -> Path:
    return caption_dir(artifacts_path, plan_id, narration_run_id, run_id) / "captions.srt"


def vtt_path(artifacts_path: Path, plan_id: int, narration_run_id: int, run_id: int) -> Path:
    return caption_dir(artifacts_path, plan_id, narration_run_id, run_id) / "captions.vtt"


def json_path(artifacts_path: Path, plan_id: int, narration_run_id: int, run_id: int) -> Path:
    return caption_dir(artifacts_path, plan_id, narration_run_id, run_id) / "captions.json"


def relative_artifact_path(artifacts_path: Path, file_path: Path) -> str:
    """Return path relative to the artifacts root for DB storage."""
    return str(file_path.relative_to(artifacts_path.resolve()))


def compute_export_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_export_atomic(dest: Path, content: str) -> None:
    """Write text content to dest atomically via a .tmp file."""
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, dest)
    except OSError as exc:
        raise CaptionStorageError(f"Failed to write caption export to {dest}: {exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_stale_temp_files(artifacts_path: Path, max_age_seconds: float) -> int:
    """Remove .tmp files older than max_age_seconds under the captions directory."""
    now = time.time()
    deleted = 0
    captions_root = artifacts_path / "captions"
    if not captions_root.exists():
        return 0
    for tmp in captions_root.rglob("*.tmp"):
        try:
            if now - tmp.stat().st_mtime > max_age_seconds:
                tmp.unlink(missing_ok=True)
                deleted += 1
        except OSError:
            pass
    return deleted
