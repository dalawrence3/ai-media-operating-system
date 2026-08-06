"""Visual Intelligence — Asset Strategy.

Determines which assets to plan for each scene based on section type,
narration content, and evidence linkage. Produces deterministic
PlannedAssetDraft recommendations with no external provider calls.

Future Phase 8 modules (stock providers, AI image generation, licensing
verification) will augment these drafts but will not change this module's
deterministic planning contract.
"""

from __future__ import annotations

from textwrap import shorten

from app.scenes.constants import (
    ASSET_AI_GENERATED_BROLL,
    ASSET_AI_GENERATED_IMAGE,
    DEFAULT_ASSET_PREFERENCES,
    LICENSE_UNKNOWN,
    PRIORITY_OPTIONAL,
    PRIORITY_PREFERRED,
    PRIORITY_REQUIRED,
    SECTION_ASSET_PREFERENCES,
    VERIFICATION_UNVERIFIED,
)
from app.scenes.models import PlannedAssetDraft


def plan_assets(
    section_type: str,
    narration_text: str,
    claim_ids: list[int],
    scene_index: int,
) -> list[PlannedAssetDraft]:
    """Return 1–3 deterministic asset recommendations for a scene.

    Every output is reproducible from the same inputs.
    No external calls. No randomness.
    """
    preferences = SECTION_ASSET_PREFERENCES.get(section_type, DEFAULT_ASSET_PREFERENCES)
    snippet = shorten(narration_text, width=60, placeholder="...")

    assets: list[PlannedAssetDraft] = []

    for i, category in enumerate(preferences[:3]):
        if i == 0:
            priority = PRIORITY_REQUIRED
        elif i == 1:
            priority = PRIORITY_PREFERRED
        else:
            priority = PRIORITY_OPTIONAL
        is_ai = category in (ASSET_AI_GENERATED_IMAGE, ASSET_AI_GENERATED_BROLL)
        ai_prompt: str | None = None
        if is_ai:
            ai_prompt = f"Cinematic {category.replace('_', ' ')} for: {snippet}"

        assets.append(
            PlannedAssetDraft(
                scene_index=scene_index,
                asset_index=i,
                category=category,
                priority=priority,
                description=(
                    f"{category.replace('_', ' ').title()} for scene {scene_index}: {snippet}"
                ),
                search_query=snippet if not is_ai else None,
                provider=None,
                source_url=None,
                license_status=LICENSE_UNKNOWN,
                license_name=None,
                attribution_required=False,
                attribution_text=None,
                commercial_safe=False,
                verification_status=VERIFICATION_UNVERIFIED,
                usage_rights={},
                ai_generation_requested=is_ai,
                ai_generation_prompt=ai_prompt,
                ai_generation_model=None,
                claim_ids=claim_ids[:],
                evidence_ids=[],
            )
        )

    return assets
