"""Phase 7C.4, §6 — confirms the existing live-production safeguard is
unchanged by the retry-idempotency fix: ElevenLabs unavailable must never
silently produce FakeTTS output in a live-configured run. FakeTTS is
selected ONLY by an explicit, upfront config choice (ACE_TTS_LIVE_ENABLED
false, or a missing API key with the live flag on -- which blocks instead
of falling back), never as a runtime fallback after a provider error.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.application.stage_executors import NarrationExecutor
from app.narration.fake import FakeTTSProvider


def _cfg(*, tts_live_enabled: bool, elevenlabs_api_key: str = "") -> SimpleNamespace:
    return SimpleNamespace(tts_live_enabled=tts_live_enabled, elevenlabs_api_key=elevenlabs_api_key)


class TestNoSilentFakeTTSFallback:
    def test_live_disabled_selects_fake_explicitly(self) -> None:
        provider = NarrationExecutor._build_provider({}, _cfg(tts_live_enabled=False))
        assert isinstance(provider, FakeTTSProvider)

    def test_live_enabled_without_api_key_blocks_rather_than_falls_back(self) -> None:
        result = NarrationExecutor._build_provider(
            {}, _cfg(tts_live_enabled=True, elevenlabs_api_key="")
        )
        # A blocked/misconfigured live request returns an error string, not
        # a FakeTTSProvider instance -- silent fallback would be exactly a
        # FakeTTSProvider here, and it must not be.
        assert isinstance(result, str)
        assert not isinstance(result, FakeTTSProvider)

    def test_live_enabled_with_api_key_selects_real_provider_not_fake(self, monkeypatch) -> None:
        from app.core.config import reset_config
        from app.narration.providers.elevenlabs import ElevenLabsTTSProvider

        monkeypatch.setenv("ACE_TTS_LIVE_ENABLED", "true")
        monkeypatch.setenv("ACE_ELEVENLABS_API_KEY", "el-test-key")
        reset_config()
        try:
            provider = NarrationExecutor._build_provider(
                {}, _cfg(tts_live_enabled=True, elevenlabs_api_key="el-test-key")
            )
            assert isinstance(provider, ElevenLabsTTSProvider)
            assert not isinstance(provider, FakeTTSProvider)
        finally:
            reset_config()

    def test_provider_choice_is_made_once_upfront_never_swapped_on_runtime_failure(self) -> None:
        """Architectural guarantee, not just a config check: narrate_plan()
        receives one already-selected provider object and never re-selects
        or falls back to a different one internally when that provider's
        complete-call fails -- the failure simply fails the run (see
        test_narrate_plan_tts_failure_marks_run_failed in
        test_narration_orchestrator.py)."""
        import inspect

        from app.narration import orchestrator

        source = inspect.getsource(orchestrator)
        assert "FakeTTSProvider" not in source
        assert "ElevenLabsTTSProvider" not in source
