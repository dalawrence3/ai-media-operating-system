"""Phase 6 M6.2: Audio artifact storage utilities."""

from __future__ import annotations

import hashlib
import io
import os
import time
import wave
from pathlib import Path

from app.narration.errors import AudioValidationError, NarrationStorageError
from app.narration.models import AudioMetadata


def resolve_artifacts_path(artifacts_path: Path) -> Path:
    """Return the resolved, absolute artifacts root, creating it if needed."""
    p = artifacts_path.resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise NarrationStorageError(f"Cannot create artifacts directory {p}: {exc}") from exc
    return p


def narration_dir(artifacts_path: Path, plan_id: int, run_id: int) -> Path:
    """Return the directory path for a narration run's audio files."""
    return artifacts_path / "narration" / f"plan_{plan_id}" / f"run_{run_id}"


def temp_audio_path(artifacts_path: Path, plan_id: int, run_id: int, segment_id: int) -> Path:
    """Return the temp path for an in-progress segment synthesis."""
    return narration_dir(artifacts_path, plan_id, run_id) / f"segment_{segment_id}.tmp"


def final_audio_path(
    artifacts_path: Path,
    plan_id: int,
    run_id: int,
    segment_id: int,
    output_format: str,
) -> Path:
    """Return the final path for a successfully synthesized segment."""
    return narration_dir(artifacts_path, plan_id, run_id) / f"segment_{segment_id}.{output_format}"


def relative_artifact_path(artifacts_path: Path, audio_path: Path) -> str:
    """Return the path relative to the artifacts root for DB storage."""
    return str(audio_path.relative_to(artifacts_path.resolve()))


def compute_audio_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_wav_bytes(data: bytes) -> AudioMetadata:
    """Parse and validate WAV bytes; return AudioMetadata on success."""
    try:
        buf = io.BytesIO(data)
        with wave.open(buf, "rb") as wf:
            num_channels = wf.getnchannels()
            sample_rate_hz = wf.getframerate()
            sample_width = wf.getsampwidth()
            num_frames = wf.getnframes()
    except Exception as exc:
        raise AudioValidationError(f"Invalid WAV data: {exc}") from exc

    if num_frames == 0:
        raise AudioValidationError("WAV file contains zero frames")
    if sample_rate_hz <= 0:
        raise AudioValidationError(f"Invalid sample rate: {sample_rate_hz}")

    duration_seconds = num_frames / sample_rate_hz
    return AudioMetadata(
        duration_seconds=duration_seconds,
        sample_rate_hz=sample_rate_hz,
        num_channels=num_channels,
        num_frames=num_frames,
        sample_width=sample_width,
    )


def append_silence(data: bytes, *, duration_ms: int) -> bytes:
    """Phase 7B, §9 — append `duration_ms` of true silence (all-zero PCM
    frames, matching the SAME channel count / sample width / frame rate as
    `data`) to the end of a WAV clip.

    This is how an interactive_recall_quiz "prompt" segment's intentional
    think-pause is represented: NOT as empty narration text (the LLM schema
    forbids that, and the caption pipeline's `_validate_handoff` rejects it
    — see content/schemas.py's think_duration_ms docstring) but as extra
    silent duration appended to the segment's own real, spoken audio. Every
    downstream consumer (caption timing, beat planning, audio/video
    assembly) already works entirely off MEASURED audio duration, so this
    is the only place new logic is needed — nothing downstream needs to
    know a pause happened at all.

    `duration_ms <= 0` returns `data` unchanged (no-op) — the ordinary path
    for every non-quiz, non-"prompt" segment.
    """
    if duration_ms <= 0:
        return data
    buf_in = io.BytesIO(data)
    with wave.open(buf_in, "rb") as wf:
        num_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    silence_frame_count = int(frame_rate * (duration_ms / 1000.0))
    silence = b"\x00" * (silence_frame_count * num_channels * sample_width)

    buf_out = io.BytesIO()
    with wave.open(buf_out, "wb") as wf_out:
        wf_out.setnchannels(num_channels)
        wf_out.setsampwidth(sample_width)
        wf_out.setframerate(frame_rate)
        wf_out.writeframes(frames + silence)
    return buf_out.getvalue()


def write_audio_atomic(dest: Path, data: bytes) -> None:
    """Write audio bytes to dest atomically via a .tmp file on the same filesystem."""
    tmp = dest.with_suffix(".tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError as exc:
        raise NarrationStorageError(f"Failed to write audio to {dest}: {exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_stale_temp_files(artifacts_path: Path, max_age_seconds: float) -> int:
    """Remove .tmp files older than max_age_seconds. Returns count deleted."""
    now = time.time()
    deleted = 0
    for tmp in artifacts_path.rglob("*.tmp"):
        try:
            if now - tmp.stat().st_mtime > max_age_seconds:
                tmp.unlink(missing_ok=True)
                deleted += 1
        except OSError:
            pass
    return deleted


def check_duration_deviation(
    expected_seconds: float,
    actual_seconds: float,
    threshold: float,
) -> bool:
    """Return True if the duration deviation exceeds threshold (fraction of expected)."""
    if expected_seconds <= 0:
        return False
    return abs(actual_seconds - expected_seconds) / expected_seconds > threshold
