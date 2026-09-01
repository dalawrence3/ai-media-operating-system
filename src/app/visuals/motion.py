"""Still-image motion treatment (Ken Burns).

A static frame held for four seconds reads as a slideshow.  A slow, bounded
push or drift reads as intentional camera work at zero extra cost.

Movement is deliberately gentle: the zoom envelope is capped and the pan is a
fraction of the frame, so the result never induces the swimming, jittery
feeling that aggressive zoompan produces on portrait video.
"""

from __future__ import annotations

from app.visuals.constants import (
    FIT_CONTAIN,
    FIT_COVER,
    MEDIA_ILLUSTRATION,
    MIN_RETAINED_AREA_FOR_CROP,
    MOTION_NONE,
    MOTION_PAN_LEFT,
    MOTION_PAN_RIGHT,
    MOTION_ZOOM_IN,
    MOTION_ZOOM_OUT,
)

# Total zoom travel across a beat.  8% is perceptible without being distracting.
_ZOOM_TRAVEL = 0.08
# Pan travel as a fraction of frame width.
_PAN_TRAVEL = 0.06
# Oversample factor before zoompan; zoompan's integer stepping produces visible
# jitter unless it operates on a larger intermediate frame.  1.5x is enough to
# hide the stepping; 2x quadrupled encode time for no visible gain.
_SUPERSAMPLE = 1.5


def choose_fit(
    media_type: str,
    source_width: int | None,
    source_height: int | None,
    *,
    target_width: int,
    target_height: int,
) -> str:
    """Decide whether centre-cropping this source would destroy its content.

    The primary signal is the asset's own media type, which the retrieval
    layer already assigns: MEDIA_ILLUSTRATION is what the engine calls
    diagrams, charts, screenshots, infographics, and other information-dense
    stills — that is the whole reason it is a type distinct from MEDIA_PHOTO.
    Ordinary photography and video are compositionally forgiving under a
    crop (a landscape scene cropped to a vertical strip still reads as the
    same scene); a labelled diagram is not — its meaning lives in the parts
    that get cropped away.

    Within illustrations, geometry still decides whether a crop is actually
    destructive: an illustration already close to 9:16 loses little, and a
    clean full-bleed crop beats a needless blurred-fill treatment.
    """
    if media_type != MEDIA_ILLUSTRATION:
        return FIT_COVER
    if not source_width or not source_height:
        # Unknown geometry on a diagram-typed asset: stay conservative rather
        # than risk cropping through a label we cannot measure.
        return FIT_CONTAIN
    cover_scale = max(target_width / source_width, target_height / source_height)
    scaled_w = source_width * cover_scale
    scaled_h = source_height * cover_scale
    retained = (target_width / scaled_w) * (target_height / scaled_h)
    return FIT_CONTAIN if retained < MIN_RETAINED_AREA_FOR_CROP else FIT_COVER


def contain_filter(width: int, height: int, fps: int) -> str:
    """Fit the whole image into the frame over a blurred fill of itself.

    Padding with a flat colour reads as a mistake; a defocused enlargement of
    the same image reads as a deliberate treatment and keeps attention on the
    legible content in the centre.
    """
    return (
        "split[__bg][__fg];"
        f"[__bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=32,eq=brightness=-0.22[__bgb];"
        f"[__fg]scale={width}:{height}:force_original_aspect_ratio=decrease[__fgs];"
        "[__bgb][__fgs]overlay=(W-w)/2:(H-h)/2,"
        f"setsar=1,fps={fps},format=yuv420p"
    )


def motion_filter(
    motion: str,
    *,
    width: int,
    height: int,
    frames: int,
    fps: int,
    fit: str = "cover",
) -> str:
    """Return the FFmpeg video filter chain for a still under *motion*.

    Takes an exact output frame count rather than a duration: zoompan emits
    ``d`` frames per *input* frame, so the caller must also bound the output
    (``-frames:v``) for the clip to land on the intended length.
    """
    frames = max(1, int(frames))

    if fit == FIT_CONTAIN:
        # A contained image is shown for its detail; drifting it around would
        # slide the subject off-centre and expose the fill.
        return contain_filter(width, height, fps)
    over_w = int(width * _SUPERSAMPLE) // 2 * 2
    over_h = int(height * _SUPERSAMPLE) // 2 * 2
    fill = f"scale={over_w}:{over_h}:force_original_aspect_ratio=increase,crop={over_w}:{over_h}"
    tail = f"setsar=1,fps={fps},format=yuv420p"

    if motion == MOTION_NONE or frames <= 1:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},{tail}"
        )

    step = _ZOOM_TRAVEL / frames

    if motion == MOTION_ZOOM_IN:
        zoom = f"min(1+{step:.8f}*on,{1 + _ZOOM_TRAVEL:.4f})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion == MOTION_ZOOM_OUT:
        zoom = f"max({1 + _ZOOM_TRAVEL:.4f}-{step:.8f}*on,1.0)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion in (MOTION_PAN_LEFT, MOTION_PAN_RIGHT):
        # Hold a constant slight zoom so there is margin to pan into.
        zoom = f"{1 + _ZOOM_TRAVEL:.4f}"
        travel = f"(iw*{_PAN_TRAVEL:.4f})"
        progress = f"(on/{frames})"
        offset = (
            f"{travel}*{progress}" if motion == MOTION_PAN_RIGHT else f"{travel}*(1-{progress})"
        )
        x = f"iw/2-(iw/zoom/2)+{offset}-{travel}/2"
        y = "ih/2-(ih/zoom/2)"
    else:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},{tail}"
        )

    zoompan = f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps}"
    return f"{fill},{zoompan},{tail}"


def motion_for(index: int, *, enabled: bool = True) -> str:
    """Rotate through motions so consecutive stills do not move identically."""
    if not enabled:
        return MOTION_NONE
    cycle = (MOTION_ZOOM_IN, MOTION_PAN_RIGHT, MOTION_ZOOM_OUT, MOTION_PAN_LEFT)
    return cycle[index % len(cycle)]
