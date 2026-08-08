"""Phase 6 M6.2: Narration orchestrator.

narrate_plan() is the public entry point.  It:
  1. Resolves the voice profile and verifies the plan is approved.
  2. Finds or creates a narration run (idempotent via input_hash).
  3. Iterates over every production segment, skipping already-synthesized ones.
  4. Calls the TTS provider, validates audio, writes it atomically to disk.
  5. Finalises each segment asset in the DB.
  6. Marks the run completed.

TTS provider calls are made OUTSIDE any DB transaction.
record_tts_call() is called after the provider call OUTSIDE any SAVEPOINT.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.narration.constants import (
    NARRATION_ALGORITHM_VERSION,
    NARRATION_DEFAULT_OUTPUT_FORMAT,
    NARRATION_DEFAULT_SAMPLE_RATE_HZ,
    NARRATION_SCHEMA_VERSION,
    NARRATION_STALE_TEMP_AGE_S,
)
from app.narration.errors import (
    IllegalSegmentTransitionError,
    SynthesisError,
)
from app.narration.hashing import (
    compute_narration_segment_input_hash,
    compute_narration_text_hash,
    compute_settings_hash,
)
from app.narration.models import (
    NarrationRunDraft,
    NarrationRunResult,
    NarrationSegmentAsset,
    NarrationSegmentAssetDraft,
)
from app.narration.pricing import TTSPricingRegistry, get_default_registry
from app.narration.protocol import TTSProvider, TTSRequest
from app.narration.repository import (
    complete_narration_run,
    create_narration_run,
    create_narration_segment_asset,
    fail_narration_run,
    finalize_narration_segment_asset,
    find_narration_run_by_input_hash,
    get_active_segment_asset,
    record_tts_call,
    require_narration_run,
    require_narration_segment_asset,
    require_voice_profile,
)
from app.narration.storage import (
    cleanup_stale_temp_files,
    compute_audio_sha256,
    final_audio_path,
    relative_artifact_path,
    resolve_artifacts_path,
    validate_wav_bytes,
    write_audio_atomic,
)


def narrate_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: int,
    plan_input_hash: str,
    voice_profile_id: int,
    artifacts_path: Path,
    provider: TTSProvider,
    dry_run: bool = False,
    pricing: TTSPricingRegistry | None = None,
    experiment_id: str | None = None,
    notes: str | None = None,
) -> NarrationRunResult:
    """Synthesise all narration segments for a production plan.

    Idempotent: if a completed/approved run with the same input_hash already
    exists its result is returned without re-synthesis.  If a 'running' run
    exists (from a crashed prior attempt), it is reused and synthesis
    continues from where it left off.
    """
    pr = pricing or get_default_registry()
    artifacts_root = resolve_artifacts_path(artifacts_path)
    cleanup_stale_temp_files(artifacts_root, NARRATION_STALE_TEMP_AGE_S)

    vp = require_voice_profile(conn, voice_profile_id)
    settings_hash = compute_settings_hash(vp.settings_json)

    draft = NarrationRunDraft(
        plan_id=plan_id,
        plan_input_hash=plan_input_hash,
        voice_profile_id=vp.id,
        voice_profile_version=vp.version,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json=vp.settings_json,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        experiment_id=experiment_id,
        notes=notes,
    )

    from app.narration.hashing import compute_narration_run_input_hash

    input_hash = compute_narration_run_input_hash(
        plan_id=plan_id,
        plan_input_hash=plan_input_hash,
        voice_profile_id=vp.id,
        voice_profile_version=vp.version,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json_hash=settings_hash,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        narration_schema_version=NARRATION_SCHEMA_VERSION,
        narration_algorithm_version=NARRATION_ALGORITHM_VERSION,
    )

    existing = find_narration_run_by_input_hash(conn, plan_id, input_hash)
    if existing:
        from app.narration.repository import get_segment_assets_for_run

        assets = [a for a in get_segment_assets_for_run(conn, existing.id)
                  if a.status == "synthesized" and a.superseded_at is None]
        return NarrationRunResult(run_id=existing.id, assets=assets)

    run = create_narration_run(conn, draft)
    conn.commit()

    segments = conn.execute(
        "SELECT id, narration_text FROM production_segments "
        "WHERE plan_id = ? ORDER BY segment_index",
        (plan_id,),
    ).fetchall()

    result = NarrationRunResult(run_id=run.id)
    try:
        for seg in segments:
            asset = _narrate_segment(
                conn,
                run_id=run.id,
                segment_id=seg[0],
                narration_text=seg[1],
                vp=vp,
                settings_hash=settings_hash,
                artifacts_root=artifacts_root,
                plan_id=plan_id,
                provider=provider,
                pr=pr,
                dry_run=dry_run,
            )
            if asset is not None:
                result.assets.append(asset)
            else:
                result.skipped_segment_ids.append(seg[0])
        run = complete_narration_run(conn, run.id)
        conn.commit()
    except Exception as exc:
        fail_narration_run(conn, run.id, error_message=str(exc))
        conn.commit()
        raise

    return result


def _narrate_segment(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    segment_id: int,
    narration_text: str,
    vp,
    settings_hash: str,
    artifacts_root: Path,
    plan_id: int,
    provider: TTSProvider,
    pr: TTSPricingRegistry,
    dry_run: bool,
):

    existing = get_active_segment_asset(conn, run_id, segment_id)
    if existing and existing.status == "synthesized":
        return existing

    narration_text_hash = compute_narration_text_hash(narration_text)
    segment_input_hash = compute_narration_segment_input_hash(
        plan_id=plan_id,
        plan_input_hash=conn.execute(
            "SELECT plan_input_hash FROM narration_runs WHERE id = ?", (run_id,)
        ).fetchone()[0],
        segment_id=segment_id,
        narration_text_hash=narration_text_hash,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        voice_profile_id=vp.id,
        voice_profile_version=vp.version,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json_hash=settings_hash,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        narration_schema_version=NARRATION_SCHEMA_VERSION,
        narration_algorithm_version=NARRATION_ALGORITHM_VERSION,
    )

    asset_draft = NarrationSegmentAssetDraft(
        run_id=run_id,
        segment_id=segment_id,
        narration_text_hash=narration_text_hash,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        voice_profile_id=vp.id,
        voice_profile_version=vp.version,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json_hash=settings_hash,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        input_hash=segment_input_hash,
    )

    if dry_run:
        return None

    asset = create_narration_segment_asset(conn, asset_draft)
    conn.commit()

    request = TTSRequest(
        text=narration_text,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json=vp.settings_json,
    )

    t0 = time.monotonic()
    tts_success = False
    tts_response = None
    tts_error: str | None = None

    try:
        tts_response = provider.synthesize(request)
        tts_success = True
    except SynthesisError as exc:
        tts_error = str(exc)

    latency_ms = int((time.monotonic() - t0) * 1000)

    cost_usd = 0.0
    chars_billed = tts_response.characters_billed if tts_response else 0
    if tts_success and tts_response:
        try:
            cost_usd = pr.estimate_cost(provider.default_model, chars_billed)
        except Exception:
            cost_usd = 0.0

    record_tts_call(
        conn,
        run_id=run_id,
        segment_id=segment_id,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        input_characters=len(narration_text),
        characters_billed=chars_billed,
        output_format=NARRATION_DEFAULT_OUTPUT_FORMAT,
        sample_rate_hz=NARRATION_DEFAULT_SAMPLE_RATE_HZ,
        duration_seconds=tts_response.duration_seconds if tts_response else None,
        cost_usd=cost_usd,
        success=tts_success,
        error_message=tts_error,
        latency_ms=latency_ms,
        request_id=tts_response.request_id if tts_response else None,
        provider_metadata_json=tts_response.provider_metadata_json if tts_response else None,
    )

    if not tts_success:
        raise SynthesisError(f"TTS failed for segment {segment_id}: {tts_error}")

    audio_data = tts_response.audio_bytes
    audio_meta = validate_wav_bytes(audio_data)
    audio_sha = compute_audio_sha256(audio_data)

    dest = final_audio_path(
        artifacts_root, plan_id, run_id, segment_id, NARRATION_DEFAULT_OUTPUT_FORMAT
    )
    write_audio_atomic(dest, audio_data)
    rel_path = relative_artifact_path(artifacts_root, dest)

    asset = finalize_narration_segment_asset(
        conn,
        asset.id,
        audio_path=rel_path,
        audio_sha256=audio_sha,
        duration_seconds=audio_meta.duration_seconds,
        characters_billed=chars_billed,
        cost_usd=cost_usd,
    )
    conn.commit()
    return asset


def regenerate_segment(
    conn: sqlite3.Connection,
    *,
    source_asset_id: int,
    artifacts_path: Path,
    provider: TTSProvider,
    actor: str | None = None,
    pricing: TTSPricingRegistry | None = None,
) -> NarrationSegmentAsset:
    """Re-synthesise a single rejected segment asset.

    The rejected source asset must have status='rejected' and not be superseded.
    On success: a new synthesized asset is created; the source is superseded;
    a segment_regenerated review event is inserted.
    On failure: the source asset remains unsuperseded; no incomplete
    replacement is left active.
    """
    pr = pricing or get_default_registry()
    artifacts_root = resolve_artifacts_path(artifacts_path)

    source = require_narration_segment_asset(conn, source_asset_id)
    if source.status != "rejected":
        raise IllegalSegmentTransitionError(
            f"Cannot regenerate asset {source_asset_id}: "
            f"status is '{source.status}' (must be 'rejected')"
        )
    if source.superseded_at is not None:
        raise IllegalSegmentTransitionError(
            f"Cannot regenerate asset {source_asset_id}: already superseded"
        )

    run = require_narration_run(conn, source.run_id)
    vp = require_voice_profile(conn, source.voice_profile_id)
    settings_hash = compute_settings_hash(vp.settings_json)

    row = conn.execute(
        "SELECT narration_text FROM production_segments WHERE id = ?",
        (source.segment_id,),
    ).fetchone()
    narration_text = row[0]
    narration_text_hash = compute_narration_text_hash(narration_text)

    segment_input_hash = compute_narration_segment_input_hash(
        plan_id=run.plan_id,
        plan_input_hash=run.plan_input_hash,
        segment_id=source.segment_id,
        narration_text_hash=narration_text_hash,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        voice_profile_id=vp.id,
        voice_profile_version=vp.version,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json_hash=settings_hash,
        output_format=run.output_format,
        sample_rate_hz=run.sample_rate_hz,
        narration_schema_version=NARRATION_SCHEMA_VERSION,
        narration_algorithm_version=NARRATION_ALGORITHM_VERSION,
    )

    asset_draft = NarrationSegmentAssetDraft(
        run_id=run.id,
        segment_id=source.segment_id,
        narration_text_hash=narration_text_hash,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        voice_profile_id=vp.id,
        voice_profile_version=vp.version,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json_hash=settings_hash,
        output_format=run.output_format,
        sample_rate_hz=run.sample_rate_hz,
        input_hash=segment_input_hash,
    )

    new_asset = create_narration_segment_asset(conn, asset_draft)
    conn.commit()

    request = TTSRequest(
        text=narration_text,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        language=vp.language,
        speaking_rate=vp.speaking_rate,
        output_format=run.output_format,
        sample_rate_hz=run.sample_rate_hz,
        style=vp.style,
        stability=vp.stability,
        similarity_boost=vp.similarity_boost,
        settings_json=vp.settings_json,
    )

    t0 = time.monotonic()
    tts_success = False
    tts_response = None
    tts_error: str | None = None

    try:
        tts_response = provider.synthesize(request)
        tts_success = True
    except SynthesisError as exc:
        tts_error = str(exc)

    latency_ms = int((time.monotonic() - t0) * 1000)
    chars_billed = tts_response.characters_billed if tts_response else 0
    cost_usd = 0.0
    if tts_success and tts_response:
        try:
            cost_usd = pr.estimate_cost(provider.default_model, chars_billed)
        except Exception:
            cost_usd = 0.0

    record_tts_call(
        conn,
        run_id=run.id,
        segment_id=source.segment_id,
        provider=provider.provider_name,
        model=provider.default_model,
        voice_id=vp.voice_id,
        input_characters=len(narration_text),
        characters_billed=chars_billed,
        output_format=run.output_format,
        sample_rate_hz=run.sample_rate_hz,
        duration_seconds=tts_response.duration_seconds if tts_response else None,
        cost_usd=cost_usd,
        success=tts_success,
        error_message=tts_error,
        latency_ms=latency_ms,
        request_id=tts_response.request_id if tts_response else None,
        provider_metadata_json=tts_response.provider_metadata_json if tts_response else None,
    )

    if not tts_success:
        # Remove the pending asset so future regeneration attempts are not blocked.
        conn.execute(
            "DELETE FROM narration_segment_assets WHERE id = ? AND status = 'pending'",
            (new_asset.id,),
        )
        conn.commit()
        raise SynthesisError(
            f"TTS failed for segment {source.segment_id} (regeneration): {tts_error}"
        )

    audio_data = tts_response.audio_bytes
    audio_meta = validate_wav_bytes(audio_data)
    audio_sha = compute_audio_sha256(audio_data)

    dest = final_audio_path(
        artifacts_root, run.plan_id, run.id, source.segment_id, run.output_format
    )
    write_audio_atomic(dest, audio_data)
    rel_path = relative_artifact_path(artifacts_root, dest)

    try:
        replacement = finalize_narration_segment_asset(
            conn,
            new_asset.id,
            audio_path=rel_path,
            audio_sha256=audio_sha,
            duration_seconds=audio_meta.duration_seconds,
            characters_billed=chars_billed,
            cost_usd=cost_usd,
            actor=actor,
        )
    except Exception:
        conn.execute(
            "DELETE FROM narration_segment_assets WHERE id = ? AND status = 'pending'",
            (new_asset.id,),
        )
        conn.commit()
        raise

    conn.commit()
    return replacement
