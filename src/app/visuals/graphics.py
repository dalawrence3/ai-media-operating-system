"""Tier 4 — programmatic explanatory visuals.

Locally rendered with Pillow at zero marginal API cost.  These exist because
stock footage is structurally incapable of showing a comparison, a process, a
numeric claim, or a definition: for those beats a clean typographic graphic is
not a fallback, it is the better product.

Renderers are chosen by beat intent and composed from a small shared kit
(panel, chip, arrow, rule, caption) rather than a bespoke animation framework.
Everything is deterministic: the same beat always renders the same file.
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.visuals.constants import (
    INTENT_COMPARISON,
    INTENT_CTA,
    INTENT_DIAGRAM,
    INTENT_EMPHASIS,
    INTENT_NUMBER,
    INTENT_PROCESS,
    INTENT_QUOTE,
    INTENT_TIMELINE,
)
from app.visuals.models import VisualBeat

GRAPHICS_VERSION = "1.0"

_NUMBER_RE = re.compile(r"(?:[<>~±]\s*)?\d+(?:[.,]\d+)?\s*(?:%|percent|x|×)?")

# ── Palette ──────────────────────────────────────────────────────────────────
# One restrained scheme per intent family.  Deep, low-luminance backgrounds keep
# burned-in captions legible and read as intentional design rather than as the
# solid-colour placeholder slide they replace.

Colour = tuple[int, int, int]


@dataclass(frozen=True)
class Palette:
    top: Colour
    bottom: Colour
    ink: Colour
    accent: Colour
    muted: Colour
    panel: Colour


_PALETTES: dict[str, Palette] = {
    INTENT_COMPARISON: Palette(
        (14, 26, 48), (10, 44, 58), (245, 248, 252), (86, 196, 214), (150, 170, 190), (22, 40, 64)
    ),
    INTENT_PROCESS: Palette(
        (16, 24, 46), (30, 26, 62), (245, 246, 252), (126, 158, 255), (152, 162, 196), (26, 34, 62)
    ),
    INTENT_DIAGRAM: Palette(
        (12, 30, 40), (16, 48, 52), (240, 248, 248), (94, 214, 186), (144, 176, 178), (20, 44, 54)
    ),
    INTENT_NUMBER: Palette(
        (36, 18, 40), (58, 22, 40), (252, 244, 248), (255, 138, 122), (196, 158, 172), (52, 26, 48)
    ),
    INTENT_TIMELINE: Palette(
        (18, 28, 36), (34, 40, 30), (246, 248, 240), (214, 190, 96), (166, 172, 150), (28, 38, 42)
    ),
    INTENT_QUOTE: Palette(
        (24, 22, 34), (40, 32, 44), (248, 246, 250), (196, 168, 240), (168, 160, 184), (34, 30, 46)
    ),
    INTENT_EMPHASIS: Palette(
        (44, 20, 24), (66, 26, 22), (252, 246, 242), (255, 168, 96), (200, 168, 150), (58, 28, 30)
    ),
    INTENT_CTA: Palette(
        (20, 22, 44), (44, 24, 58), (250, 248, 252), (255, 196, 108), (176, 172, 196), (32, 30, 56)
    ),
}
_DEFAULT_PALETTE = Palette(
    (16, 26, 44), (18, 44, 62), (244, 247, 250), (110, 186, 224), (150, 168, 188), (24, 38, 58)
)

_BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_REGULAR_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _font(candidates: list[str], size: int) -> _Font:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _palette(intent: str) -> Palette:
    return _PALETTES.get(intent, _DEFAULT_PALETTE)


def _lerp(a: Colour, b: Colour, t: float) -> Colour:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _background(width: int, height: int, palette: Palette) -> Image.Image:
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line(
            [(0, y), (width, y)], fill=_lerp(palette.top, palette.bottom, y / max(height - 1, 1))
        )
    return image


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: _Font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: _Font, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if _text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    font: _Font,
    top: int,
    width: int,
    margin: int,
    fill: Colour,
    line_gap: int = 12,
    centre: bool = True,
) -> int:
    y = top
    for line in lines:
        text_width = _text_width(draw, line, font)
        x = (width - text_width) // 2 if centre else margin
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=fill)
        box = draw.textbbox((0, 0), line, font=font)
        y += (box[3] - box[1]) + line_gap
    return y


def _panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    palette: Palette,
    *,
    alpha: int = 205,
    radius: int = 28,
    outline: Colour | None = None,
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=(*palette.panel, alpha),
        outline=(*(outline or palette.accent), 220),
        width=3,
    )
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _headline_terms(beat: VisualBeat, limit: int = 3) -> list[str]:
    """Distinct display terms for this beat, longest form of each concept first.

    Entities lead because they are the most specific.  A term already contained
    in a kept term ("homology" under "Homology Directed Repair") is the same
    concept in shorter clothes and would read as a duplicate on screen.
    """
    kept: list[str] = []
    for value in beat.entities + beat.keywords:
        cleaned = value.strip().strip(".,;:")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if any(lowered in k.lower() or k.lower() in lowered for k in kept):
            continue
        kept.append(cleaned)
        if len(kept) >= limit:
            break
    return kept


def _side_heading(beat: VisualBeat, side_text: str, limit: int = 26) -> str:
    """Pick the most specific beat term that actually occurs in *side_text*."""
    lowered = side_text.lower()
    for term in _headline_terms(beat, 6):
        if term.lower() in lowered:
            return _label(term, limit)
    return _label(side_text.split(",")[0], limit)


def _label(text: str, limit: int = 34) -> str:
    cleaned = " ".join((text or "").split())
    return textwrap.shorten(cleaned, width=limit, placeholder="…") if cleaned else ""


def _split_contrast(text: str) -> tuple[str, str] | None:
    """Split a comparison beat into its two sides on a contrast connective."""
    lowered = f" {text.lower()} "
    for marker in (
        " versus ",
        " vs. ",
        " vs ",
        " rather than ",
        " instead of ",
        " whereas ",
        " unlike ",
        " but ",
        " while ",
        " or ",
    ):
        index = lowered.find(marker)
        if index > 0:
            left = text[:index].strip(" ,.;—")
            right = text[index + len(marker) - 1 :].strip(" ,.;—")
            # Both sides must carry real content; a stub like "gene ." is a
            # sentence remnant, not one half of a contrast.
            if len(left.split()) >= 3 and len(right.split()) >= 3:
                return left, right
    return None


def _steps(text: str, limit: int = 4) -> list[str]:
    parts = [
        p.strip(" ,;—")
        for p in re.split(r"[.;]|,\s+(?:and|then|so|which)\b", text or "")
        if p and p.strip()
    ]
    return [p for p in parts if len(p) > 3][:limit]


# ── Renderers ────────────────────────────────────────────────────────────────


def _render_number(image: Image.Image, beat: VisualBeat, palette: Palette) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.09)

    match = _NUMBER_RE.search(beat.narration_text or "")
    figure = (match.group(0).strip() if match else "").upper() or _label(
        _headline_terms(beat, 1)[0] if _headline_terms(beat, 1) else "", 8
    )

    size = int(width * 0.34) if len(figure) <= 4 else int(width * 0.22)
    figure_font = _font(_BOLD_FONTS, size)
    caption_font = _font(_REGULAR_FONTS, int(width * 0.052))

    caption_lines = _wrap(
        draw, " ".join((beat.narration_text or "").split())[:150], caption_font, width - 2 * margin
    )[:4]
    figure_width = _text_width(draw, figure, figure_font)
    figure_box = draw.textbbox((0, 0), figure, font=figure_font)
    figure_height = figure_box[3] - figure_box[1]

    block_top = height // 2 - (figure_height + 60 + len(caption_lines) * int(width * 0.07)) // 2
    _panel(
        image,
        (
            margin - 30,
            block_top - 70,
            width - margin + 30,
            block_top + figure_height + 90 + len(caption_lines) * int(width * 0.07),
        ),
        palette,
    )
    draw = ImageDraw.Draw(image)

    draw.text(
        ((width - figure_width) // 2 + 3, block_top + 4), figure, font=figure_font, fill=(0, 0, 0)
    )
    draw.text(
        ((width - figure_width) // 2, block_top), figure, font=figure_font, fill=palette.accent
    )

    rule_y = block_top + figure_height + 44
    draw.line([(margin + 40, rule_y), (width - margin - 40, rule_y)], fill=palette.accent, width=5)
    _draw_block(
        draw,
        caption_lines,
        font=caption_font,
        top=rule_y + 40,
        width=width,
        margin=margin,
        fill=palette.ink,
    )


def _render_comparison(image: Image.Image, beat: VisualBeat, palette: Palette) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.08)

    sides = _split_contrast(beat.narration_text or "")
    if sides is None:
        # The intent cue fired on a connective that is not actually contrasting
        # two things ("...a gene. But if you want..."). Inventing a two-sided
        # panel here would pair unrelated terms; a statement card is honest.
        _render_statement(image, beat, palette, quoted=False)
        return
    left_text, right_text = sides

    title_font = _font(_BOLD_FONTS, int(width * 0.072))
    body_font = _font(_REGULAR_FONTS, int(width * 0.046))

    panel_height = int(height * 0.26)
    gap = int(height * 0.055)
    top = height // 2 - panel_height - gap // 2

    for index, (text, tone) in enumerate(
        ((left_text, palette.accent), (right_text, palette.muted))
    ):
        box_top = top + index * (panel_height + gap)
        _panel(
            image, (margin, box_top, width - margin, box_top + panel_height), palette, outline=tone
        )
        draw = ImageDraw.Draw(image)
        heading = _side_heading(beat, text)
        head_lines = _wrap(draw, heading, title_font, width - 2 * margin - 80)[:2]
        y = _draw_block(
            draw,
            head_lines,
            font=title_font,
            top=box_top + 40,
            width=width,
            margin=margin,
            fill=tone,
        )
        # Only show supporting detail when it says more than the heading does.
        remainder = _label(text, 110)
        if remainder.rstrip("…").lower() != heading.rstrip("…").lower():
            detail = _wrap(draw, remainder, body_font, width - 2 * margin - 80)[:3]
            _draw_block(
                draw,
                detail,
                font=body_font,
                top=y + 8,
                width=width,
                margin=margin,
                fill=palette.ink,
            )

    # Contrast marker between the two panels.
    marker_font = _font(_BOLD_FONTS, int(width * 0.06))
    marker = "VS"
    marker_width = _text_width(draw, marker, marker_font)
    marker_y = top + panel_height + gap // 2 - int(width * 0.035)
    draw.ellipse(
        [
            (width - marker_width) // 2 - 26,
            marker_y - 18,
            (width + marker_width) // 2 + 26,
            marker_y + int(width * 0.075),
        ],
        fill=palette.top,
        outline=palette.accent,
        width=4,
    )
    draw.text(
        ((width - marker_width) // 2, marker_y), marker, font=marker_font, fill=palette.accent
    )


def _render_process(image: Image.Image, beat: VisualBeat, palette: Palette) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.09)

    steps = _steps(beat.narration_text or "") or _headline_terms(beat, 3)
    if not steps:
        steps = [_label(beat.narration_text, 40)]

    step_font = _font(_BOLD_FONTS, int(width * 0.048))
    index_font = _font(_BOLD_FONTS, int(width * 0.044))

    block_height = int(height * 0.13)
    gap = int(height * 0.045)
    total = len(steps) * block_height + (len(steps) - 1) * gap
    top = height // 2 - total // 2

    for index, step in enumerate(steps):
        box_top = top + index * (block_height + gap)
        _panel(image, (margin, box_top, width - margin, box_top + block_height), palette)
        draw = ImageDraw.Draw(image)

        badge = int(block_height * 0.44)
        cx, cy = margin + 56, box_top + block_height // 2
        draw.ellipse(
            [cx - badge // 2, cy - badge // 2, cx + badge // 2, cy + badge // 2],
            fill=palette.accent,
        )
        number = str(index + 1)
        number_width = _text_width(draw, number, index_font)
        draw.text(
            (cx - number_width // 2, cy - badge // 2 + badge // 6),
            number,
            font=index_font,
            fill=palette.top,
        )

        text_left = margin + 56 + badge
        lines = _wrap(draw, _label(step, 90), step_font, width - text_left - margin - 30)[:2]
        _draw_block(
            draw,
            lines,
            font=step_font,
            top=cy - len(lines) * int(width * 0.031),
            width=width,
            margin=text_left,
            fill=palette.ink,
            centre=False,
        )

        if index < len(steps) - 1:
            arrow_y = box_top + block_height + gap // 2
            draw.line(
                [(width // 2, arrow_y - gap // 3), (width // 2, arrow_y + gap // 4)],
                fill=palette.accent,
                width=5,
            )
            draw.polygon(
                [
                    (width // 2 - 14, arrow_y + gap // 4),
                    (width // 2 + 14, arrow_y + gap // 4),
                    (width // 2, arrow_y + gap // 2),
                ],
                fill=palette.accent,
            )


def _render_timeline(image: Image.Image, beat: VisualBeat, palette: Palette) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.12)

    marks = _headline_terms(beat, 4) or _steps(beat.narration_text or "", 4)
    if not marks:
        marks = [_label(beat.narration_text, 40)]

    label_font = _font(_BOLD_FONTS, int(width * 0.046))
    axis_x = margin
    top = int(height * 0.30)
    bottom = int(height * 0.74)
    draw.line([(axis_x, top), (axis_x, bottom)], fill=palette.accent, width=6)

    step = (bottom - top) // max(len(marks), 1)
    for index, mark in enumerate(marks):
        y = top + step * index + step // 2
        draw.ellipse([axis_x - 16, y - 16, axis_x + 16, y + 16], fill=palette.accent)
        lines = _wrap(draw, _label(mark, 60), label_font, width - axis_x - margin - 40)[:2]
        _draw_block(
            draw,
            lines,
            font=label_font,
            top=y - int(width * 0.028),
            width=width,
            margin=axis_x + 48,
            fill=palette.ink,
            centre=False,
        )


def _render_diagram(image: Image.Image, beat: VisualBeat, palette: Palette) -> None:
    """A labelled part-of relationship: a subject and its named components."""
    width, height = image.size
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.1)

    terms = _headline_terms(beat, 4)
    subject = terms[0] if terms else _label(beat.narration_text, 24)
    parts = terms[1:] or _steps(beat.narration_text or "", 3)

    subject_font = _font(_BOLD_FONTS, int(width * 0.075))
    part_font = _font(_BOLD_FONTS, int(width * 0.045))

    cx, cy = width // 2, int(height * 0.36)
    radius = int(width * 0.20)
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=palette.accent,
        width=6,
        fill=palette.panel,
    )
    subject_lines = _wrap(draw, _label(subject, 22), subject_font, radius * 2 - 40)[:2]
    _draw_block(
        draw,
        subject_lines,
        font=subject_font,
        top=cy - len(subject_lines) * int(width * 0.045),
        width=width,
        margin=margin,
        fill=palette.ink,
    )

    chip_top = cy + radius + int(height * 0.06)
    chip_height = int(height * 0.085)
    for index, part in enumerate(parts[:3]):
        y = chip_top + index * (chip_height + int(height * 0.028))
        draw.line([(cx, y - int(height * 0.028)), (cx, y)], fill=palette.accent, width=4)
        _panel(image, (margin, y, width - margin, y + chip_height), palette)
        draw = ImageDraw.Draw(image)
        lines = _wrap(draw, _label(part, 44), part_font, width - 2 * margin - 60)[:1]
        _draw_block(
            draw,
            lines,
            font=part_font,
            top=y + chip_height // 2 - int(width * 0.028),
            width=width,
            margin=margin,
            fill=palette.ink,
        )


def _render_statement(
    image: Image.Image, beat: VisualBeat, palette: Palette, *, quoted: bool
) -> None:
    """Kinetic-typography style card for quotes, emphasis, concepts, and CTAs."""
    width, height = image.size
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.1)

    text = " ".join((beat.narration_text or "").split())
    if quoted:
        match = re.search(r"[\"“”](.+?)[\"“”]", text)
        if match:
            text = match.group(1)

    terms = _headline_terms(beat, 2)
    kicker = terms[0].upper() if terms else beat.visual_intent.upper()

    kicker_font = _font(_BOLD_FONTS, int(width * 0.042))
    body_font = _font(_BOLD_FONTS, int(width * 0.078) if len(text) < 90 else int(width * 0.058))

    body_lines = _wrap(draw, _label(text, 190), body_font, width - 2 * margin)[:6]
    body_box = draw.textbbox((0, 0), "Ag", font=body_font)
    line_height = (body_box[3] - body_box[1]) + 16
    block_height = len(body_lines) * line_height

    top = height // 2 - block_height // 2 - int(height * 0.03)
    _panel(
        image,
        (
            margin - 34,
            top - int(height * 0.09),
            width - margin + 34,
            top + block_height + int(height * 0.05),
        ),
        palette,
    )
    draw = ImageDraw.Draw(image)

    kicker_width = _text_width(draw, kicker, kicker_font)
    draw.text(
        ((width - kicker_width) // 2, top - int(height * 0.062)),
        kicker,
        font=kicker_font,
        fill=palette.accent,
    )
    draw.line(
        [
            (margin + 30, top - int(height * 0.028)),
            (width - margin - 30, top - int(height * 0.028)),
        ],
        fill=palette.accent,
        width=3,
    )

    if quoted:
        mark_font = _font(_BOLD_FONTS, int(width * 0.16))
        draw.text(
            (margin - 6, top - int(height * 0.055)),
            "“",
            font=mark_font,
            fill=(*palette.accent, 90)[:3],
        )

    _draw_block(
        draw,
        body_lines,
        font=body_font,
        top=top,
        width=width,
        margin=margin,
        fill=palette.ink,
        line_gap=16,
    )


_RENDERERS = {
    INTENT_NUMBER: _render_number,
    INTENT_COMPARISON: _render_comparison,
    INTENT_PROCESS: _render_process,
    INTENT_TIMELINE: _render_timeline,
    INTENT_DIAGRAM: _render_diagram,
}


# ── Public API ───────────────────────────────────────────────────────────────


def graphic_cache_key(beat: VisualBeat, width: int, height: int) -> str:
    """Deterministic identity for a beat's generated graphic."""
    payload = "|".join(
        [
            GRAPHICS_VERSION,
            beat.visual_intent,
            beat.narration_text,
            ",".join(beat.keywords),
            ",".join(beat.entities),
            str(width),
            str(height),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def render_beat_graphic(
    beat: VisualBeat,
    output_path: Path,
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Render an explanatory graphic for *beat*.  Always succeeds.

    This is the guaranteed terminal step of the visual fallback chain: it
    requires no network, no key, and no external service, so a beat can never
    end up unresolved because a provider was down.
    """
    palette = _palette(beat.visual_intent)
    image = _background(width, height, palette)

    renderer = _RENDERERS.get(beat.visual_intent)
    if renderer is not None:
        renderer(image, beat, palette)
    else:
        _render_statement(image, beat, palette, quoted=beat.visual_intent == INTENT_QUOTE)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(output_path), format="PNG")
    return output_path
