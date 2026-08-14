"""Dev-only routes — available only when ACE_ENV=development.

Never register this router in staging or production.
Used exclusively by E2E tests to reset deterministic seed fixtures.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.learning.constants import STATUS_PENDING
from app.narration.models import VoiceProfileCreate
from app.narration.repository import create_voice_profile, list_voice_profiles

router = APIRouter(prefix="/dev", tags=["dev"])

# Sentinel voice_id used by FakeTTSProvider; harmless placeholder for dev.
_DEV_VOICE_PROFILE_NAME = "dev-fake-voice"


@router.post("/ensure-voice-profile", status_code=200)
def ensure_dev_voice_profile(conn: Any = Depends(get_db)) -> dict[str, Any]:
    """Idempotently ensure a dev voice profile exists and return its ID.

    Creates a single fake/dev profile if the table is empty.
    Safe to call repeatedly — returns the existing profile on subsequent calls.
    Use the returned voice_profile_id in ExecutePipelineStageCommand.effective_config
    when manually triggering the narration stage.
    """
    profiles = list_voice_profiles(conn)
    if profiles:
        return {"voice_profile_id": profiles[0].id, "created": False}

    vpc = VoiceProfileCreate(
        channel_id=None,
        provider="fake",
        model="fake-tts-v1",
        voice_id="dev-voice-placeholder",
        name=_DEV_VOICE_PROFILE_NAME,
        language="en-US",
        speaking_rate=1.0,
        style=None,
        stability=None,
        similarity_boost=None,
        settings_json="{}",
        version=1,
        is_default=True,
    )
    profile = create_voice_profile(conn, vpc)
    conn.commit()
    return {"voice_profile_id": profile.id, "created": True}


@router.post("/reset-learning-fixtures", status_code=200)
def reset_learning_fixtures(conn: Any = Depends(get_db)) -> dict[str, int]:
    """Reset seeded learning recommendations back to pending status.

    Targets only rows with input_hash matching 'seed-dev-rec-*' so real
    user data is never touched. Safe to call repeatedly between E2E runs.
    """
    cur = conn.execute(
        """UPDATE optimization_recommendations
              SET status = ?, superseded_at = NULL, superseded_by_id = NULL
            WHERE input_hash LIKE 'seed-dev-rec-%'""",
        (STATUS_PENDING,),
    )
    conn.commit()
    return {"reset": cur.rowcount}
