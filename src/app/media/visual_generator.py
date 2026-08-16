"""Pillow-based visual card generator for scene backgrounds.

Produces styled gradient PNG images for scenes that have no real visual asset.
Cards are 100% local — no network access, no AI generation, no API keys.
Each section type gets a distinct colour palette and keyword overlay derived
from the scene's visual_objective / narration_text so the visual track
carries meaning rather than a plain solid-colour placeholder.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Colour palettes per section type  (top_colour, bottom_colour, text_colour)
# ---------------------------------------------------------------------------

_PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "hook": ((255, 140, 20), (255, 60, 60), (255, 255, 255)),
    "intro": ((30, 100, 200), (10, 180, 200), (255, 255, 255)),
    "body": ((10, 80, 160), (20, 160, 100), (255, 255, 255)),
    "transition": ((80, 50, 160), (30, 100, 200), (255, 255, 255)),
    "conclusion": ((80, 30, 140), (30, 80, 200), (255, 255, 255)),
    "cta": ((200, 50, 20), (255, 120, 30), (255, 255, 255)),
    "default": ((20, 60, 120), (10, 120, 160), (255, 255, 255)),
}

_FONT_CANDIDATES = [
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_CAPTION_FONT_CANDIDATES = [
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _lerp_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def generate_scene_card(
    *,
    section_type: str,
    keyword_text: str,
    width: int,
    height: int,
    output_path: Path,
) -> Path:
    """Generate a styled gradient PNG card for a scene.

    Returns output_path after writing the file.
    """
    palette = _PALETTES.get(section_type, _PALETTES["default"])
    top_c, bot_c, text_c = palette

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Vertical gradient
    for y in range(height):
        t = y / (height - 1)
        color = _lerp_color(top_c, bot_c, t)
        draw.line([(0, y), (width, y)], fill=color)

    # Keyword text — wrap to fit width
    margin = int(width * 0.08)
    usable_width = width - 2 * margin
    font_size = max(48, width // 15)
    font = _load_font(_FONT_CANDIDATES, font_size)

    # Estimate characters per line for wrapping (rough heuristic)
    avg_char_width = font_size * 0.55
    chars_per_line = max(10, int(usable_width / avg_char_width))
    wrapped = textwrap.fill(keyword_text, width=chars_per_line)

    # Semi-transparent text background box
    lines = wrapped.split("\n")
    line_height = font_size + 12
    text_block_height = len(lines) * line_height + 40
    text_y = height // 2 - text_block_height // 2

    box_x0, box_x1 = margin - 20, width - margin + 20
    box_y0, box_y1 = text_y - 20, text_y + text_block_height
    # Draw a dark translucent rectangle via alpha compositing
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.rectangle([box_x0, box_y0, box_x1, box_y1], fill=(0, 0, 0, 160))
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Draw each line centred
    for i, line in enumerate(lines):
        line_y = text_y + i * line_height
        # Shadow
        draw.text((margin + 2, line_y + 2), line, font=font, fill=(0, 0, 0))
        # Text
        draw.text((margin, line_y), line, font=font, fill=text_c)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), format="PNG")
    return output_path


def card_cache_key(section_type: str, keyword_text: str, width: int, height: int) -> str:
    """Deterministic cache key for a card — avoids regenerating identical frames."""
    payload = f"{section_type}|{keyword_text}|{width}|{height}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def prep_image_for_vertical(
    source_path: Path,
    output_path: Path,
    width: int,
    height: int,
) -> Path:
    """Scale and center-crop *source_path* to exactly *width*×*height* (portrait).

    Strategy: scale so the image fills the frame (no letterboxing), then
    center-crop any excess.  Works for landscape, square, or portrait inputs.
    Returns *output_path* after writing.
    """
    with Image.open(source_path) as img:
        img = img.convert("RGB")
        src_w, src_h = img.size

        # Scale to fill: the limiting dimension drives the scale factor
        scale = max(width / src_w, height / src_h)
        new_w = max(width, int(src_w * scale))
        new_h = max(height, int(src_h * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Center crop
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        img = img.crop((left, top, left + width, top + height))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="JPEG", quality=90)
    return output_path
