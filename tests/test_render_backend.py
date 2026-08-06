"""Tests for the FFmpeg render backend."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from app.media.backend import (
    FFmpegRenderBackend,
    check_ffmpeg_available,
    get_default_backend,
)
from app.media.constants import BACKEND_FFMPEG, FFMPEG_BACKEND_VERSION
from app.media.errors import FFmpegNotFoundError, RenderBackendError
from app.media.models import RenderManifestDraft, RenderSceneDraft


def _make_draft(scenes: list[RenderSceneDraft] | None = None) -> RenderManifestDraft:
    if scenes is None:
        scenes = [
            RenderSceneDraft(
                scene_index=0,
                scene_id=1,
                segment_id=10,
                narration_asset_id=None,
                audio_path="/audio/seg_0.wav",
                audio_sha256="sha0",
                start_ms=0,
                end_ms=3000,
                duration_ms=3000,
                shot_type="medium",
                camera_movement="static",
                visual_objective="Test scene",
                caption_cue_ids=[],
                primary_asset=None,
            )
        ]
    return RenderManifestDraft(
        scene_manifest_id=1,
        narration_run_id=2,
        caption_run_id=3,
        topic_id=1,
        plan_id=1,
        script_id=1,
        experiment_id=None,
        input_hash="abc",
        render_schema_version="Render-v1",
        compositor_version="compositor-1.0.0",
        total_scene_count=len(scenes),
        total_duration_ms=sum(s.duration_ms for s in scenes),
        width=1080,
        height=1920,
        fps=30,
        caption_burn_in=False,
        scenes=scenes,
    )


class TestFFmpegRenderBackend:
    def test_backend_name(self):
        b = FFmpegRenderBackend()
        assert b.backend_name == BACKEND_FFMPEG

    def test_backend_version(self):
        b = FFmpegRenderBackend()
        assert b.backend_version == FFMPEG_BACKEND_VERSION

    def test_raises_when_ffmpeg_not_found(self, tmp_path):
        b = FFmpegRenderBackend(ffmpeg_bin="no_such_binary_xyz")
        draft = _make_draft()
        with pytest.raises(FFmpegNotFoundError):
            b.render(draft, tmp_path / "out.mp4", tmp_path / "tmp")

    @patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.media.backend.subprocess.run")
    def test_render_calls_ffmpeg(self, mock_run, mock_which, tmp_path):
        # Simulate FFmpeg succeeding
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        output_path = tmp_path / "out.mp4"
        # Write a real file so stat() works naturally
        output_path.write_bytes(b"x" * 1024)

        b = FFmpegRenderBackend()
        draft = _make_draft()

        with patch.object(b, "_sha256", return_value="deadbeef"):
            result = b.render(draft, output_path, tmp_path / "tmp")

        assert mock_run.called
        assert result.output_sha256 == "deadbeef"
        assert result.file_size_bytes == 1024

    @patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.media.backend.subprocess.run")
    def test_render_raises_on_ffmpeg_failure(self, mock_run, mock_which, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stderr="error output")

        b = FFmpegRenderBackend()
        draft = _make_draft()

        with pytest.raises(RenderBackendError, match="FFmpeg command failed"):
            b.render(draft, tmp_path / "out.mp4", tmp_path / "tmp")

    @patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.media.backend.subprocess.run")
    def test_placeholder_clip_used_when_no_asset(self, mock_run, mock_which, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        output_path = tmp_path / "out.mp4"
        output_path.write_bytes(b"x" * 100)

        b = FFmpegRenderBackend()
        draft = _make_draft()
        assert draft.scenes[0].primary_asset is None

        with patch.object(b, "_sha256", return_value="xx"):
            b.render(draft, output_path, tmp_path / "tmp")

        # Check that lavfi was used (placeholder clip)
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("lavfi" in c or "color=" in c for c in calls)

    def test_image_clip_cmd_structure(self, tmp_path):
        b = FFmpegRenderBackend()
        cmd = b._image_clip_cmd(
            asset_path=Path("/tmp/img.jpg"),
            output=tmp_path / "clip.mp4",
            duration_s=3.0,
            width=1080,
            height=1920,
            fps=30,
            video_codec="libx264",
        )
        assert "ffmpeg" in cmd[0]
        assert "-loop" in cmd
        assert "1" in cmd
        assert "-t" in cmd
        assert "3.000" in cmd
        assert "-i" in cmd

    def test_placeholder_clip_cmd_structure(self, tmp_path):
        b = FFmpegRenderBackend()
        cmd = b._placeholder_clip_cmd(
            output=tmp_path / "clip.mp4",
            duration_s=5.0,
            width=1080,
            height=1920,
            fps=30,
            video_codec="libx264",
            label="Test scene objective",
        )
        assert "ffmpeg" in cmd[0]
        assert "-f" in cmd
        assert "lavfi" in cmd


class TestCheckFFmpegAvailable:
    def test_returns_true_when_found(self):
        with patch("app.media.backend.shutil.which", return_value="/usr/bin/ffmpeg"):
            assert check_ffmpeg_available() is True

    def test_returns_false_when_not_found(self):
        with patch("app.media.backend.shutil.which", return_value=None):
            assert check_ffmpeg_available() is False


class TestGetDefaultBackend:
    def test_returns_ffmpeg_backend(self):
        b = get_default_backend()
        assert isinstance(b, FFmpegRenderBackend)
        assert b.backend_name == BACKEND_FFMPEG
