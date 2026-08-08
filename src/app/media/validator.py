"""Phase 8 render validator.

RenderValidator is a Protocol — the engine never checks which validator is active.
FFprobeValidator is the concrete implementation for Phase 8.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol

from app.media.constants import (
    FFPROBE_VALIDATOR_VERSION,
    MAX_DURATION_DEVIATION_FRACTION,
)
from app.media.errors import FFprobeNotFoundError, RenderValidationError


class ValidationResult:
    """Parsed output from a successful FFprobe run."""

    __slots__ = (
        "duration_s",
        "width",
        "height",
        "fps",
        "video_codec",
        "audio_codec",
        "file_size_bytes",
        "has_video",
        "has_audio",
        "raw",
    )

    def __init__(
        self,
        *,
        duration_s: float,
        width: int | None,
        height: int | None,
        fps: float | None,
        video_codec: str | None,
        audio_codec: str | None,
        file_size_bytes: int,
        has_video: bool,
        has_audio: bool,
        raw: dict[str, Any],
    ) -> None:
        self.duration_s = duration_s
        self.width = width
        self.height = height
        self.fps = fps
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.file_size_bytes = file_size_bytes
        self.has_video = has_video
        self.has_audio = has_audio
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_s": self.duration_s,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "file_size_bytes": self.file_size_bytes,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "validator_version": FFPROBE_VALIDATOR_VERSION,
        }


class RenderValidator(Protocol):
    """Provider-neutral video validation interface."""

    @property
    def validator_version(self) -> str: ...

    def validate(self, output_path: Path, expected_duration_s: float) -> ValidationResult: ...


class FFprobeValidator:
    """FFprobe-backed video validation.

    Checks: file exists, has video stream, has audio stream, duration within
    MAX_DURATION_DEVIATION_FRACTION of expected duration.
    """

    def __init__(self, ffprobe_bin: str = "ffprobe") -> None:
        self._ffprobe = ffprobe_bin

    @property
    def validator_version(self) -> str:
        return FFPROBE_VALIDATOR_VERSION

    def validate(self, output_path: Path, expected_duration_s: float) -> ValidationResult:
        self._require_ffprobe()

        if not output_path.exists():
            raise RenderValidationError(f"Output file does not exist: {output_path}")

        probe = self._probe(output_path)
        result = self._parse(probe, output_path)
        self._check(result, expected_duration_s)
        return result

    # ── Private ──────────────────────────────────────────────────────────────

    def _require_ffprobe(self) -> None:
        if not shutil.which(self._ffprobe):
            raise FFprobeNotFoundError(
                f"'{self._ffprobe}' not found on PATH. Install FFmpeg (includes ffprobe)."
            )

    def _probe(self, path: Path) -> dict[str, Any]:
        cmd = [
            self._ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RenderValidationError(
                f"ffprobe failed (exit {result.returncode}): {result.stderr[-1000:]}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RenderValidationError(f"ffprobe output is not valid JSON: {exc}") from exc

    @staticmethod
    def _parse(probe: dict[str, Any], path: Path) -> ValidationResult:
        streams = probe.get("streams", [])
        fmt = probe.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        duration_s = float(fmt.get("duration") or 0.0)
        file_size_bytes = path.stat().st_size

        width = int(video_stream["width"]) if video_stream and "width" in video_stream else None
        height = int(video_stream["height"]) if video_stream and "height" in video_stream else None
        video_codec = video_stream.get("codec_name") if video_stream else None
        audio_codec = audio_stream.get("codec_name") if audio_stream else None

        fps: float | None = None
        if video_stream:
            raw_fps = video_stream.get("r_frame_rate", "")
            if "/" in raw_fps:
                num, den = raw_fps.split("/")
                if int(den) != 0:
                    fps = float(num) / float(den)

        return ValidationResult(
            duration_s=duration_s,
            width=width,
            height=height,
            fps=fps,
            video_codec=video_codec,
            audio_codec=audio_codec,
            file_size_bytes=file_size_bytes,
            has_video=video_stream is not None,
            has_audio=audio_stream is not None,
            raw=probe,
        )

    @staticmethod
    def _check(result: ValidationResult, expected_duration_s: float) -> None:
        if not result.has_video:
            raise RenderValidationError("Output has no video stream.")
        if not result.has_audio:
            raise RenderValidationError("Output has no audio stream.")
        if expected_duration_s > 0:
            deviation = abs(result.duration_s - expected_duration_s) / expected_duration_s
            if deviation > MAX_DURATION_DEVIATION_FRACTION:
                raise RenderValidationError(
                    f"Duration deviation {deviation:.1%} exceeds "
                    f"{MAX_DURATION_DEVIATION_FRACTION:.0%} threshold. "
                    f"Expected {expected_duration_s:.2f}s, got {result.duration_s:.2f}s."
                )


def get_default_validator() -> FFprobeValidator:
    return FFprobeValidator()


def check_ffprobe_available(ffprobe_bin: str = "ffprobe") -> bool:
    return shutil.which(ffprobe_bin) is not None
