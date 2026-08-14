"""Phase 8 render backend.

RenderBackend is a Protocol — the engine never checks ``if backend == 'ffmpeg'``.
FFmpegRenderBackend is the concrete implementation for Phase 8.

Render strategy:
  1. Per-scene: generate a video clip at the correct duration.
     - Visual asset present → scale/pad image to target resolution.
     - No asset → solid colour placeholder with text overlay.
  2. Concatenate audio from narration segment WAV files.
  3. Mux video concat + full audio → output MP4.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from app.media.constants import (
    BACKEND_FFMPEG,
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CRF,
    DEFAULT_FFMPEG_PRESET,
    DEFAULT_VIDEO_CODEC,
    FFMPEG_BACKEND_VERSION,
    PLACEHOLDER_BG_COLOR,
)
from app.media.errors import (
    AssetHashMismatchError,
    FFmpegNotFoundError,
    RenderBackendError,
    UnresolvedRequiredAssetError,
)
from app.media.models import RenderJobResult, RenderManifestDraft


class RenderBackend(Protocol):
    """Provider-neutral rendering interface."""

    @property
    def backend_name(self) -> str: ...

    @property
    def backend_version(self) -> str: ...

    def render(
        self,
        draft: RenderManifestDraft,
        output_path: Path,
        tmp_dir: Path,
        *,
        video_codec: str = DEFAULT_VIDEO_CODEC,
        audio_codec: str = DEFAULT_AUDIO_CODEC,
        crf: int = DEFAULT_CRF,
        preset: str = DEFAULT_FFMPEG_PRESET,
        audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
        allow_placeholders: bool = False,
    ) -> RenderJobResult: ...


class FFmpegRenderBackend:
    """FFmpeg-backed render implementation.

    Replaces by swapping this class for another RenderBackend implementor
    without touching the pipeline.
    """

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        self._ffmpeg = ffmpeg_bin

    @property
    def backend_name(self) -> str:
        return BACKEND_FFMPEG

    @property
    def backend_version(self) -> str:
        return FFMPEG_BACKEND_VERSION

    # ── Public ──────────────────────────────────────────────────────────────

    def render(
        self,
        draft: RenderManifestDraft,
        output_path: Path,
        tmp_dir: Path,
        *,
        video_codec: str = DEFAULT_VIDEO_CODEC,
        audio_codec: str = DEFAULT_AUDIO_CODEC,
        crf: int = DEFAULT_CRF,
        preset: str = DEFAULT_FFMPEG_PRESET,
        audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
        allow_placeholders: bool = False,
    ) -> RenderJobResult:
        self._require_ffmpeg()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()

        clip_paths = self._generate_scene_clips(
            draft, tmp_dir, video_codec, allow_placeholders=allow_placeholders
        )
        audio_path = self._assemble_audio(draft, tmp_dir)
        final_cmd = self._mux(
            clip_paths=clip_paths,
            audio_path=audio_path,
            output_path=output_path,
            video_codec=video_codec,
            audio_codec=audio_codec,
            crf=crf,
            preset=preset,
            audio_bitrate=audio_bitrate,
            draft=draft,
        )

        render_time_s = time.monotonic() - start
        file_size_bytes = output_path.stat().st_size
        sha256 = self._sha256(output_path)

        return RenderJobResult(
            output_path=str(output_path),
            output_sha256=sha256,
            duration_s=draft.total_duration_ms / 1000.0,
            file_size_bytes=file_size_bytes,
            render_time_s=render_time_s,
            ffmpeg_cmd=final_cmd,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _require_ffmpeg(self) -> None:
        if not shutil.which(self._ffmpeg):
            raise FFmpegNotFoundError(
                f"'{self._ffmpeg}' not found on PATH. Install FFmpeg to render videos."
            )

    def _generate_scene_clips(
        self,
        draft: RenderManifestDraft,
        tmp_dir: Path,
        video_codec: str,
        *,
        allow_placeholders: bool,
    ) -> list[Path]:
        clips: list[Path] = []
        for scene in draft.scenes:
            clip_path = tmp_dir / f"scene_{scene.scene_index:03d}.mp4"
            duration_s = scene.duration_ms / 1000.0
            needs_placeholder = not (scene.primary_asset and scene.primary_asset.local_path)

            if needs_placeholder and not allow_placeholders:
                raise UnresolvedRequiredAssetError(
                    f"Scene {scene.scene_index} has no resolved visual asset. "
                    "Pass allow_placeholders=True (dev only) to use placeholder slides."
                )

            if not needs_placeholder:
                asset_path = Path(scene.primary_asset.local_path)  # type: ignore[arg-type]
                expected_sha = scene.primary_asset.local_sha256  # type: ignore[union-attr]
                if expected_sha is not None:
                    actual_sha = self._sha256(asset_path)
                    if actual_sha != expected_sha:
                        raise AssetHashMismatchError(
                            f"Scene {scene.scene_index} asset hash mismatch: "
                            f"expected {expected_sha!r}, got {actual_sha!r}. "
                            "Asset may have been modified or corrupted."
                        )
                _VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

                if asset_path.suffix.lower() in _VIDEO_EXTS:
                    cmd = self._video_clip_cmd(
                        asset_path=asset_path,
                        output=clip_path,
                        duration_s=duration_s,
                        width=draft.width,
                        height=draft.height,
                        fps=draft.fps,
                        video_codec=video_codec,
                    )
                else:
                    cmd = self._image_clip_cmd(
                        asset_path=asset_path,
                        output=clip_path,
                        duration_s=duration_s,
                        width=draft.width,
                        height=draft.height,
                        fps=draft.fps,
                        video_codec=video_codec,
                    )
            else:
                cmd = self._placeholder_clip_cmd(
                    output=clip_path,
                    duration_s=duration_s,
                    width=draft.width,
                    height=draft.height,
                    fps=draft.fps,
                    video_codec=video_codec,
                    label=scene.visual_objective[:60],
                )

            self._run(cmd)
            clips.append(clip_path)
        return clips

    def _assemble_audio(self, draft: RenderManifestDraft, tmp_dir: Path) -> Path:
        audio_out = tmp_dir / "narration_full.wav"
        audio_paths = [s.audio_path for s in draft.scenes if s.audio_path]

        if not audio_paths:
            # Generate silent audio matching total duration
            duration_s = draft.total_duration_ms / 1000.0
            cmd = [
                self._ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-t",
                f"{duration_s:.3f}",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-c:a",
                "pcm_s16le",
                str(audio_out),
            ]
            self._run(cmd)
            return audio_out

        concat_list = tmp_dir / "audio_concat.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in audio_paths), encoding="utf-8")
        cmd = [
            self._ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:a",
            "pcm_s16le",
            str(audio_out),
        ]
        self._run(cmd)
        return audio_out

    def _mux(
        self,
        *,
        clip_paths: list[Path],
        audio_path: Path,
        output_path: Path,
        video_codec: str,
        audio_codec: str,
        crf: int,
        preset: str,
        audio_bitrate: str,
        draft: RenderManifestDraft,
    ) -> list[str]:
        concat_list = audio_path.parent / "video_concat.txt"
        lines = []
        for i, (clip, scene) in enumerate(zip(clip_paths, draft.scenes, strict=False)):
            lines.append(f"file '{clip}'")
            lines.append(f"duration {scene.duration_ms / 1000.0:.3f}")
            if i == len(clip_paths) - 1:
                # FFmpeg concat demuxer: repeat last entry without duration
                lines.append(f"file '{clip}'")
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            self._ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            video_codec,
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_path),
        ]
        self._run(cmd)
        return cmd

    def _image_clip_cmd(
        self,
        *,
        asset_path: Path,
        output: Path,
        duration_s: float,
        width: int,
        height: int,
        fps: int,
        video_codec: str,
    ) -> list[str]:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps},format=yuv420p"
        )
        return [
            self._ffmpeg,
            "-y",
            "-loop",
            "1",
            "-t",
            f"{duration_s:.3f}",
            "-i",
            str(asset_path),
            "-vf",
            vf,
            "-c:v",
            video_codec,
            "-an",
            str(output),
        ]

    def _video_clip_cmd(
        self,
        *,
        asset_path: Path,
        output: Path,
        duration_s: float,
        width: int,
        height: int,
        fps: int,
        video_codec: str,
    ) -> list[str]:
        # Fill frame (scale so short dim >= target), center-crop to exact size.
        # Works for landscape (16:9) → portrait (9:16) crops.
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"setsar=1,fps={fps},format=yuv420p"
        )
        return [
            self._ffmpeg,
            "-y",
            "-stream_loop",
            "-1",  # loop if clip shorter than duration (prevents freeze)
            "-i",
            str(asset_path),
            "-t",
            f"{duration_s:.3f}",
            "-vf",
            vf,
            "-c:v",
            video_codec,
            "-an",
            str(output),
        ]

    def _placeholder_clip_cmd(
        self,
        *,
        output: Path,
        duration_s: float,
        width: int,
        height: int,
        fps: int,
        video_codec: str,
        label: str,
    ) -> list[str]:
        # Plain solid-colour placeholder — no drawtext (requires libfreetype).
        vf = f"color=color={PLACEHOLDER_BG_COLOR}:size={width}x{height}:rate={fps},format=yuv420p"
        return [
            self._ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-t",
            f"{duration_s:.3f}",
            "-i",
            vf,
            "-c:v",
            video_codec,
            "-an",
            str(output),
        ]

    def _run(self, cmd: list[str], timeout: int = 600) -> None:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RenderBackendError(
                f"FFmpeg command failed (exit {result.returncode}):\n"
                f"CMD: {' '.join(cmd)}\n"
                f"STDERR: {result.stderr[-2000:]}"
            )

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


def get_default_backend() -> FFmpegRenderBackend:
    return FFmpegRenderBackend()


def check_ffmpeg_available(ffmpeg_bin: str = "ffmpeg") -> bool:
    return shutil.which(ffmpeg_bin) is not None
