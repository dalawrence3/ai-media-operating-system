"""Tests for src/app/captions/storage.py."""

from __future__ import annotations

from pathlib import Path

from app.captions.storage import (
    caption_dir,
    cleanup_stale_temp_files,
    compute_export_sha256,
    json_path,
    relative_artifact_path,
    resolve_artifacts_path,
    srt_path,
    vtt_path,
    write_export_atomic,
)


class TestResolveArtifactsPath:
    def test_creates_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "artifacts"
        assert not target.exists()
        result = resolve_artifacts_path(target)
        assert result.exists()
        assert result.is_dir()

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        result = resolve_artifacts_path(tmp_path)
        assert result.is_absolute()

    def test_idempotent(self, tmp_path: Path) -> None:
        p1 = resolve_artifacts_path(tmp_path)
        p2 = resolve_artifacts_path(tmp_path)
        assert p1 == p2


class TestCaptionDir:
    def test_path_structure(self, tmp_path: Path) -> None:
        result = caption_dir(tmp_path, plan_id=3, narration_run_id=7, run_id=12)
        assert "captions" in result.parts
        assert "plan_3" in result.parts
        assert "narration_7" in result.parts
        assert "run_12" in result.parts

    def test_different_ids_give_different_paths(self, tmp_path: Path) -> None:
        p1 = caption_dir(tmp_path, plan_id=1, narration_run_id=1, run_id=1)
        p2 = caption_dir(tmp_path, plan_id=1, narration_run_id=1, run_id=2)
        assert p1 != p2


class TestExportPaths:
    def test_srt_path_ends_with_srt(self, tmp_path: Path) -> None:
        p = srt_path(tmp_path, 1, 1, 1)
        assert p.suffix == ".srt"

    def test_vtt_path_ends_with_vtt(self, tmp_path: Path) -> None:
        p = vtt_path(tmp_path, 1, 1, 1)
        assert p.suffix == ".vtt"

    def test_json_path_ends_with_json(self, tmp_path: Path) -> None:
        p = json_path(tmp_path, 1, 1, 1)
        assert p.suffix == ".json"

    def test_paths_are_under_caption_dir(self, tmp_path: Path) -> None:
        cdir = caption_dir(tmp_path, 1, 1, 1)
        assert srt_path(tmp_path, 1, 1, 1).parent == cdir
        assert vtt_path(tmp_path, 1, 1, 1).parent == cdir
        assert json_path(tmp_path, 1, 1, 1).parent == cdir


class TestRelativeArtifactPath:
    def test_strips_artifacts_root(self, tmp_path: Path) -> None:
        full = tmp_path / "captions" / "plan_1" / "run_1" / "captions.srt"
        rel = relative_artifact_path(tmp_path, full)
        assert rel == "captions/plan_1/run_1/captions.srt"

    def test_no_leading_slash(self, tmp_path: Path) -> None:
        full = tmp_path / "x" / "y.json"
        rel = relative_artifact_path(tmp_path, full)
        assert not rel.startswith("/")


class TestComputeExportSha256:
    def test_deterministic(self) -> None:
        assert compute_export_sha256("hello") == compute_export_sha256("hello")

    def test_different_content_different_hash(self) -> None:
        assert compute_export_sha256("hello") != compute_export_sha256("world")

    def test_returns_64_char_hex(self) -> None:
        h = compute_export_sha256("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_unicode_content(self) -> None:
        h = compute_export_sha256("café München")
        assert len(h) == 64


class TestWriteExportAtomic:
    def test_writes_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "sub" / "out.srt"
        write_export_atomic(dest, "1\n00:00:00,000 --> 00:00:02,000\nHello\n")
        assert dest.exists()
        assert "Hello" in dest.read_text(encoding="utf-8")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        dest = tmp_path / "a" / "b" / "c.vtt"
        write_export_atomic(dest, "WEBVTT\n")
        assert dest.exists()

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        write_export_atomic(dest, "{}")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_overwrites_existing_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.srt"
        write_export_atomic(dest, "first")
        write_export_atomic(dest, "second")
        assert dest.read_text(encoding="utf-8") == "second"

    def test_unicode_preserved(self, tmp_path: Path) -> None:
        content = "café München\n"
        dest = tmp_path / "out.txt"
        write_export_atomic(dest, content)
        assert dest.read_text(encoding="utf-8") == content


class TestCleanupStaleTempFiles:
    def test_no_captions_dir_returns_zero(self, tmp_path: Path) -> None:
        result = cleanup_stale_temp_files(tmp_path, max_age_seconds=0)
        assert result == 0

    def test_removes_old_tmp_files(self, tmp_path: Path) -> None:
        captions_dir = tmp_path / "captions"
        captions_dir.mkdir()
        old_tmp = captions_dir / "stale.srt.tmp"
        old_tmp.write_text("stale", encoding="utf-8")
        import os
        import time

        old_time = time.time() - 3600
        os.utime(old_tmp, (old_time, old_time))
        result = cleanup_stale_temp_files(tmp_path, max_age_seconds=60)
        assert result == 1
        assert not old_tmp.exists()

    def test_preserves_fresh_tmp_files(self, tmp_path: Path) -> None:
        captions_dir = tmp_path / "captions"
        captions_dir.mkdir()
        fresh_tmp = captions_dir / "fresh.srt.tmp"
        fresh_tmp.write_text("fresh", encoding="utf-8")
        result = cleanup_stale_temp_files(tmp_path, max_age_seconds=3600)
        assert result == 0
        assert fresh_tmp.exists()
