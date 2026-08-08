"""Tests for Phase 6 M6.2 narration storage utilities."""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path

import pytest

from app.narration.errors import AudioValidationError
from app.narration.models import AudioMetadata
from app.narration.storage import (
    check_duration_deviation,
    cleanup_stale_temp_files,
    compute_audio_sha256,
    final_audio_path,
    narration_dir,
    relative_artifact_path,
    resolve_artifacts_path,
    temp_audio_path,
    validate_wav_bytes,
    write_audio_atomic,
)


def _wav_bytes(*, duration: float = 1.0, sample_rate: int = 22050) -> bytes:
    num_frames = int(duration * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_frames}h", *([0] * num_frames)))
    return buf.getvalue()


# ── resolve_artifacts_path ────────────────────────────────────────────────────


def test_resolve_creates_directory(tmp_path: Path) -> None:
    p = tmp_path / "new" / "artifacts"
    result = resolve_artifacts_path(p)
    assert result.is_dir()


def test_resolve_returns_absolute(tmp_path: Path) -> None:
    result = resolve_artifacts_path(tmp_path / "art")
    assert result.is_absolute()


def test_resolve_idempotent(tmp_path: Path) -> None:
    art = tmp_path / "art"
    r1 = resolve_artifacts_path(art)
    r2 = resolve_artifacts_path(art)
    assert r1 == r2


# ── path helpers ──────────────────────────────────────────────────────────────


def test_narration_dir_structure(tmp_path: Path) -> None:
    d = narration_dir(tmp_path, plan_id=3, run_id=7)
    assert "plan_3" in str(d)
    assert "run_7" in str(d)


def test_temp_audio_path_suffix(tmp_path: Path) -> None:
    p = temp_audio_path(tmp_path, plan_id=1, run_id=2, segment_id=5)
    assert p.suffix == ".tmp"
    assert "segment_5" in p.name


def test_final_audio_path_suffix(tmp_path: Path) -> None:
    p = final_audio_path(tmp_path, plan_id=1, run_id=2, segment_id=5, output_format="wav")
    assert p.suffix == ".wav"
    assert "segment_5" in p.name


def test_relative_artifact_path(tmp_path: Path) -> None:
    art = resolve_artifacts_path(tmp_path / "art")
    audio = art / "narration" / "plan_1" / "run_1" / "segment_1.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"x")
    rel = relative_artifact_path(art, audio)
    assert not rel.startswith("/")
    assert "segment_1.wav" in rel


# ── compute_audio_sha256 ──────────────────────────────────────────────────────


def test_sha256_is_hex_64_chars() -> None:
    h = compute_audio_sha256(b"test")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_sha256_deterministic() -> None:
    assert compute_audio_sha256(b"abc") == compute_audio_sha256(b"abc")


def test_sha256_sensitive_to_content() -> None:
    assert compute_audio_sha256(b"a") != compute_audio_sha256(b"b")


# ── validate_wav_bytes ────────────────────────────────────────────────────────


def test_validate_returns_audio_metadata() -> None:
    meta = validate_wav_bytes(_wav_bytes())
    assert isinstance(meta, AudioMetadata)


def test_validate_correct_duration() -> None:
    meta = validate_wav_bytes(_wav_bytes(duration=2.0, sample_rate=22050))
    assert abs(meta.duration_seconds - 2.0) < 0.01


def test_validate_correct_sample_rate() -> None:
    meta = validate_wav_bytes(_wav_bytes(sample_rate=44100))
    assert meta.sample_rate_hz == 44100


def test_validate_invalid_bytes_raises() -> None:
    with pytest.raises(AudioValidationError):
        validate_wav_bytes(b"not a wav file at all")


def test_validate_zero_frames_raises() -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"")
    with pytest.raises(AudioValidationError, match="zero frames"):
        validate_wav_bytes(buf.getvalue())


# ── write_audio_atomic ────────────────────────────────────────────────────────


def test_write_audio_atomic_creates_file(tmp_path: Path) -> None:
    dest = tmp_path / "sub" / "out.wav"
    data = _wav_bytes()
    write_audio_atomic(dest, data)
    assert dest.read_bytes() == data


def test_write_audio_atomic_no_tmp_left(tmp_path: Path) -> None:
    dest = tmp_path / "out.wav"
    write_audio_atomic(dest, _wav_bytes())
    assert not dest.with_suffix(".tmp").exists()


def test_write_audio_atomic_overwrites(tmp_path: Path) -> None:
    dest = tmp_path / "out.wav"
    write_audio_atomic(dest, b"first")
    write_audio_atomic(dest, b"second")
    assert dest.read_bytes() == b"second"


# ── cleanup_stale_temp_files ──────────────────────────────────────────────────


def test_cleanup_removes_stale_tmp(tmp_path: Path) -> None:
    tmp = tmp_path / "old.tmp"
    tmp.write_bytes(b"x")
    import os

    os.utime(tmp, (0, 0))
    deleted = cleanup_stale_temp_files(tmp_path, max_age_seconds=1)
    assert deleted == 1
    assert not tmp.exists()


def test_cleanup_preserves_fresh_tmp(tmp_path: Path) -> None:
    tmp = tmp_path / "fresh.tmp"
    tmp.write_bytes(b"x")
    deleted = cleanup_stale_temp_files(tmp_path, max_age_seconds=86400)
    assert deleted == 0
    assert tmp.exists()


def test_cleanup_returns_count(tmp_path: Path) -> None:
    import os

    for i in range(3):
        t = tmp_path / f"stale_{i}.tmp"
        t.write_bytes(b"x")
        os.utime(t, (0, 0))
    assert cleanup_stale_temp_files(tmp_path, max_age_seconds=1) == 3


# ── check_duration_deviation ──────────────────────────────────────────────────


def test_no_deviation_is_false() -> None:
    assert check_duration_deviation(2.0, 2.0, threshold=0.5) is False


def test_large_deviation_is_true() -> None:
    assert check_duration_deviation(2.0, 5.0, threshold=0.5) is True


def test_threshold_boundary() -> None:
    # Exactly at threshold — should be False (not strictly greater)
    assert check_duration_deviation(2.0, 3.0, threshold=0.5) is False


def test_zero_expected_is_false() -> None:
    assert check_duration_deviation(0.0, 5.0, threshold=0.5) is False
