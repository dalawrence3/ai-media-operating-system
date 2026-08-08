"""Tests for the FFprobe render validator."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.media.errors import FFprobeNotFoundError, RenderValidationError
from app.media.validator import (
    FFprobeValidator,
    check_ffprobe_available,
    get_default_validator,
)


def _make_probe_output(
    *,
    duration: float = 30.0,
    width: int = 1080,
    height: int = 1920,
    fps: str = "30/1",
    video_codec: str = "h264",
    audio_codec: str = "aac",
    file_size: int = 5_000_000,
) -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": video_codec,
                "width": width,
                "height": height,
                "r_frame_rate": fps,
            },
            {
                "codec_type": "audio",
                "codec_name": audio_codec,
            },
        ],
        "format": {"duration": str(duration)},
    }


class TestFFprobeValidator:
    def test_validator_version(self):
        v = FFprobeValidator()
        assert v.validator_version

    def test_raises_when_ffprobe_not_found(self, tmp_path):
        v = FFprobeValidator(ffprobe_bin="no_such_binary_xyz")
        f = tmp_path / "out.mp4"
        f.touch()
        with pytest.raises(FFprobeNotFoundError):
            v.validate(f, 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    def test_raises_when_file_missing(self, _mock_which, tmp_path):
        v = FFprobeValidator()
        with pytest.raises(RenderValidationError, match="does not exist"):
            v.validate(tmp_path / "nonexistent.mp4", 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_valid_file_returns_result(self, mock_run, _mock_which, tmp_path):
        probe = _make_probe_output(duration=30.0)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(probe),
            stderr="",
        )
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x" * 1000)

        v = FFprobeValidator()
        result = v.validate(f, 30.0)

        assert result.duration_s == 30.0
        assert result.width == 1080
        assert result.height == 1920
        assert result.video_codec == "h264"
        assert result.audio_codec == "aac"
        assert result.has_video is True
        assert result.has_audio is True

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_rejects_when_no_video_stream(self, mock_run, _mock_which, tmp_path):
        probe = {
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "30.0"},
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe), stderr="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        v = FFprobeValidator()
        with pytest.raises(RenderValidationError, match="no video stream"):
            v.validate(f, 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_rejects_when_no_audio_stream(self, mock_run, _mock_which, tmp_path):
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "r_frame_rate": "30/1",
                }
            ],
            "format": {"duration": "30.0"},
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe), stderr="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        v = FFprobeValidator()
        with pytest.raises(RenderValidationError, match="no audio stream"):
            v.validate(f, 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_rejects_on_duration_deviation(self, mock_run, _mock_which, tmp_path):
        # Expected 30s, actual 20s → 33% deviation > 5% threshold
        probe = _make_probe_output(duration=20.0)
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe), stderr="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        v = FFprobeValidator()
        with pytest.raises(RenderValidationError, match="Duration deviation"):
            v.validate(f, 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_accepts_small_duration_deviation(self, mock_run, _mock_which, tmp_path):
        # Expected 30s, actual 30.1s → 0.3% deviation ≤ 5%
        probe = _make_probe_output(duration=30.1)
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe), stderr="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x" * 100)

        v = FFprobeValidator()
        result = v.validate(f, 30.0)
        assert result.duration_s == 30.1

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_raises_on_ffprobe_failure(self, mock_run, _mock_which, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        v = FFprobeValidator()
        with pytest.raises(RenderValidationError, match="ffprobe failed"):
            v.validate(f, 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_raises_on_invalid_json(self, mock_run, _mock_which, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        v = FFprobeValidator()
        with pytest.raises(RenderValidationError, match="not valid JSON"):
            v.validate(f, 30.0)

    @patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("app.media.validator.subprocess.run")
    def test_to_dict_contains_expected_keys(self, mock_run, _mock_which, tmp_path):
        probe = _make_probe_output()
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe), stderr="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x" * 100)

        v = FFprobeValidator()
        result = v.validate(f, 30.0)
        d = result.to_dict()
        assert "duration_s" in d
        assert "width" in d
        assert "height" in d
        assert "fps" in d
        assert "video_codec" in d
        assert "audio_codec" in d
        assert "validator_version" in d


class TestCheckFFprobeAvailable:
    def test_returns_true_when_found(self):
        with patch("app.media.validator.shutil.which", return_value="/usr/bin/ffprobe"):
            assert check_ffprobe_available() is True

    def test_returns_false_when_not_found(self):
        with patch("app.media.validator.shutil.which", return_value=None):
            assert check_ffprobe_available() is False


class TestGetDefaultValidator:
    def test_returns_ffprobe_validator(self):
        v = get_default_validator()
        assert isinstance(v, FFprobeValidator)
