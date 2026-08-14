"""Caption burn-in for FFmpeg renders.

Strategy: since this FFmpeg build lacks libass/libfreetype, captions are
composited via the `movie` source filter + time-gated `overlay` filter chain.
PIL renders each unique cue as a transparent RGBA PNG; FFmpeg overlays each
at the correct absolute timestamp using `enable='between(t,...)'`.

Two public functions:
  build_absolute_srt  — fuses per-segment cue timing with segment offsets
  burn_captions_into  — composites captions onto a finished MP4
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Font helpers (shared with visual_generator; local copy avoids circular import)
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# SRT helpers
# ---------------------------------------------------------------------------


def _ms_to_srt(ms: int) -> str:
    total_s, ms_part = divmod(ms, 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_part:03d}"


def build_absolute_srt(
    cues_json_path: Path,
    segment_offsets_ms: dict[int, int],
) -> list[dict]:
    """Return cues with absolute start_ms / end_ms from per-segment timing.

    segment_offsets_ms maps segment_id → cumulative audio offset in ms
    (i.e., how many ms of audio preceded this segment in the final timeline).
    """
    data = json.loads(cues_json_path.read_bytes())
    cues = data.get("cues", [])
    absolute: list[dict] = []
    for cue in cues:
        seg_id = cue["segment_id"]
        offset = segment_offsets_ms.get(seg_id, 0)
        absolute.append(
            {
                **cue,
                "abs_start_ms": cue["start_ms"] + offset,
                "abs_end_ms": cue["end_ms"] + offset,
            }
        )
    return absolute


# ---------------------------------------------------------------------------
# Caption image renderer
# ---------------------------------------------------------------------------


def _render_caption_image(
    text: str,
    width: int,
    output_path: Path,
    font_size: int = 52,
) -> Path:
    """Render one caption cue as a transparent RGBA PNG.

    Layout: white bold text on a semi-transparent dark rounded-ish rectangle,
    positioned to fill the given width.  Height auto-sized to content.
    """
    font = _load_font(font_size)
    margin_x = int(width * 0.06)
    max_text_width_chars = max(10, int((width - 2 * margin_x) / (font_size * 0.52)))
    wrapped = textwrap.fill(text.replace("\n", " "), width=max_text_width_chars)
    lines = wrapped.split("\n")

    line_h = font_size + 10
    pad_v = 22
    pad_h = 30
    text_block_h = len(lines) * line_h
    img_h = text_block_h + pad_v * 2 + 10
    img_w = width

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark background rectangle
    draw.rectangle(
        [margin_x - pad_h, pad_v - 10, img_w - margin_x + pad_h, img_h - pad_v + 10],
        fill=(0, 0, 0, 190),
    )

    for i, line in enumerate(lines):
        y = pad_v + i * line_h
        # Drop shadow
        draw.text((margin_x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        # White text
        draw.text((margin_x, y), line, font=font, fill=(255, 255, 255, 255))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), format="PNG")
    return output_path


# ---------------------------------------------------------------------------
# FFmpeg overlay burn-in
# ---------------------------------------------------------------------------


def burn_captions_into(
    *,
    input_mp4: Path,
    cues_json_path: Path,
    segment_offsets_ms: dict[int, int],
    output_mp4: Path,
    width: int,
    height: int,
    caption_tmp_dir: Path,
    ffmpeg_bin: str = "ffmpeg",
    font_size: int = 52,
) -> Path:
    """Burn captions from a cues JSON file into *input_mp4*, writing *output_mp4*.

    Uses FFmpeg `movie` source + time-gated `overlay` filter chain — no libass
    or libfreetype required.

    Returns output_mp4 on success.
    """
    caption_tmp_dir.mkdir(parents=True, exist_ok=True)
    absolute_cues = build_absolute_srt(cues_json_path, segment_offsets_ms)

    if not absolute_cues:
        # Nothing to burn in — copy input to output unchanged
        import shutil

        shutil.copy2(input_mp4, output_mp4)
        return output_mp4

    # Render each cue as a PNG
    png_paths: list[Path] = []
    for i, cue in enumerate(absolute_cues):
        png = caption_tmp_dir / f"cap_{i:03d}.png"
        _render_caption_image(cue["text"], width, png, font_size=font_size)
        png_paths.append(png)

    # Caption strip Y position: 82% down the frame (above bottom edge)
    caption_y = int(height * 0.82)

    # Build filter_complex: movie sources + chained overlays
    # Each movie stream is named [cap_N]; each overlay produces [v_N]
    filter_parts: list[str] = []

    for i, (png, _cue) in enumerate(zip(png_paths, absolute_cues, strict=False)):
        safe_path = str(png).replace("'", r"\'").replace(":", r"\:")
        filter_parts.append(f"movie={safe_path}[cap_{i}]")

    # Chain overlays: [0:v] → [v_0] → [v_1] → ... → [vout]
    prev = "0:v"
    for i, cue in enumerate(absolute_cues):
        start_s = cue["abs_start_ms"] / 1000.0
        end_s = cue["abs_end_ms"] / 1000.0
        out = f"v_{i}" if i < len(absolute_cues) - 1 else "vout"
        # Centre horizontally; fixed Y position near bottom
        x_expr = "(W-w)/2"
        enable = f"between(t,{start_s:.3f},{end_s:.3f})"
        filter_parts.append(
            f"[{prev}][cap_{i}]overlay=x={x_expr}:y={caption_y}:enable='{enable}'[{out}]"
        )
        prev = out

    filter_complex = ";\n".join(filter_parts)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_mp4),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "medium",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"Caption burn-in failed (exit {result.returncode}):\n"
            f"CMD: {' '.join(cmd[:6])}...\n"
            f"STDERR: {result.stderr[-2000:]}"
        )
    return output_mp4
