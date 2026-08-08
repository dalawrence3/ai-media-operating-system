"""Tests for Phase 6 M6.3B ProviderLifecycle and ProviderLifecycleState."""

from __future__ import annotations

from app.narration.fake import FakeTTSProvider
from app.narration.lifecycle import ProviderLifecycle, ProviderLifecycleState

# ── ProviderLifecycleState enum ───────────────────────────────────────────────


def test_all_states_defined() -> None:
    states = {s.value for s in ProviderLifecycleState}
    assert states == {"created", "initializing", "ready", "degraded", "shutdown"}


def test_states_have_string_values() -> None:
    for state in ProviderLifecycleState:
        assert isinstance(state.value, str)


# ── FakeTTSProvider lifecycle ─────────────────────────────────────────────────


def test_fake_satisfies_lifecycle_protocol() -> None:
    assert isinstance(FakeTTSProvider(), ProviderLifecycle)


def test_initial_state_is_created() -> None:
    p = FakeTTSProvider()
    assert p.lifecycle_state == ProviderLifecycleState.CREATED


def test_initialize_transitions_to_ready() -> None:
    p = FakeTTSProvider()
    p.initialize()
    assert p.lifecycle_state == ProviderLifecycleState.READY


def test_shutdown_transitions_to_shutdown() -> None:
    p = FakeTTSProvider()
    p.initialize()
    p.shutdown()
    assert p.lifecycle_state == ProviderLifecycleState.SHUTDOWN


def test_shutdown_without_initialize_transitions() -> None:
    p = FakeTTSProvider()
    p.shutdown()
    assert p.lifecycle_state == ProviderLifecycleState.SHUTDOWN


def test_initialize_is_idempotent() -> None:
    p = FakeTTSProvider()
    p.initialize()
    p.initialize()
    assert p.lifecycle_state == ProviderLifecycleState.READY


# ── Lifecycle does not gate synthesis ─────────────────────────────────────────


def test_synthesis_works_before_initialize() -> None:
    from app.narration.fake import FAKE_MODEL_NAME, FAKE_PROVIDER_NAME, FAKE_VOICE_ID
    from app.narration.protocol import TTSRequest

    p = FakeTTSProvider()
    assert p.lifecycle_state == ProviderLifecycleState.CREATED
    resp = p.synthesize(
        TTSRequest(
            text="Test",
            provider=FAKE_PROVIDER_NAME,
            model=FAKE_MODEL_NAME,
            voice_id=FAKE_VOICE_ID,
            language="en-US",
            speaking_rate=1.0,
            output_format="wav",
            sample_rate_hz=22050,
        )
    )
    assert resp.audio_bytes


def test_synthesis_works_after_initialize() -> None:
    from app.narration.fake import FAKE_MODEL_NAME, FAKE_PROVIDER_NAME, FAKE_VOICE_ID
    from app.narration.protocol import TTSRequest

    p = FakeTTSProvider()
    p.initialize()
    resp = p.synthesize(
        TTSRequest(
            text="Hello",
            provider=FAKE_PROVIDER_NAME,
            model=FAKE_MODEL_NAME,
            voice_id=FAKE_VOICE_ID,
            language="en-US",
            speaking_rate=1.0,
            output_format="wav",
            sample_rate_hz=22050,
        )
    )
    assert resp.audio_bytes


# ── Each instance has independent state ───────────────────────────────────────


def test_lifecycle_state_is_per_instance() -> None:
    p1 = FakeTTSProvider()
    p2 = FakeTTSProvider()
    p1.initialize()
    assert p1.lifecycle_state == ProviderLifecycleState.READY
    assert p2.lifecycle_state == ProviderLifecycleState.CREATED
