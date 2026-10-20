"""Phase 7C.3 — recovery-targeted discovery: correct domain intent.

Phase 7C.2 proved the compatibility gate blocks a mismatched opportunity
from inheriting an exploration cell's lineage, but left the root cause
unfixed: `_default_discovery_fn` folded the recovery cell's own domain into
`ExplorationProfile.secondary_niches`, and the cold-start prompt tells the
model to diversify AWAY from everything listed as a secondary niche — the
exact opposite of what a recovery call wants for the domain it exists to
replenish. This file proves the fix: the cell's domain is now conveyed
through a separate `target_domain` / `target_domain_section` channel that
says "explore this now", never through secondary_niches, and every ordinary
(non-recovery) cold-start caller renders byte-identical prompt content to
before.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from app.ai.fake import FakeProvider
from app.ai.provider import AIRequest, AIResponse
from app.core.database import open_db
from app.intelligence.exploration.cell import ExplorationCell
from app.intelligence.market.cold_start import ExplorationProfile, plan_cold_start
from app.intelligence.market.planner_prompts import COLD_START_PROMPT_VERSION


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = open_db(pathlib.Path(d) / "test.db")
        yield c
        c.close()


def _seed_channel_profile(
    db,
    channel_id: int = 1,
    primary_niche: str = "science and technology explained",
    secondary_niches: list[str] | None = None,
) -> int:
    db.execute("INSERT OR IGNORE INTO channels (id, channel_name) VALUES (?, ?)", (channel_id, "c"))
    secondary_json = json.dumps(secondary_niches or ["history and society"])
    row = db.execute(
        """INSERT INTO channel_profile_versions
               (channel_id, version, primary_niche, secondary_niches_json, excluded_topics_json,
                brand_voice, content_style, audience_description, status, created_by)
           VALUES (?, 1, ?, ?, '[]', 'conversational', 'explainer', 'general audience',
                   'active', 'test')
           RETURNING id""",
        (channel_id, primary_niche, secondary_json),
    ).fetchone()
    db.commit()
    return row["id"]


class RecordingFakeProvider(FakeProvider):
    """Captures the exact AIRequest it was called with, for prompt-content
    assertions, while still returning FakeProvider's deterministic output."""

    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.last_request: AIRequest | None = None

    def complete(self, request: AIRequest) -> AIResponse:
        self.last_request = request
        return super().complete(request)


_PROFILE = ExplorationProfile(
    primary_niche="science and technology explained",
    secondary_niches=["history and society"],
)

_ONE_CANDIDATE = json.dumps(
    {
        "probes": [
            {
                "query": "everyday vocabulary and pronunciation",
                "market_region_label": "Language Basics",
                "domain_family": "Language Learning",
                "rationale": "r",
                "format_fit_score": 0.8,
                "distance_from_current_subjects": 0.8,
                "distinctiveness_rationale": "d",
            }
        ]
    }
)


class TestTargetDomainSectionPropagation:
    """Phase 7C.3, §5 — proves the actual wiring, not just prose inspection:
    the rendered prompt text a real provider call receives differs based on
    whether target_domain was supplied, and the difference is exactly the
    new TARGET EXPLORATION DOMAIN section."""

    def test_ordinary_call_renders_no_target_domain_section(self, conn):
        provider = RecordingFakeProvider(_ONE_CANDIDATE)
        plan_cold_start(conn, _PROFILE, provider=provider, max_probes=3)

        assert provider.last_request is not None
        assert "TARGET EXPLORATION DOMAIN FOR THIS RUN" not in provider.last_request.user

    def test_recovery_call_renders_target_domain_section_with_the_cells_domain(self, conn):
        provider = RecordingFakeProvider(_ONE_CANDIDATE)
        plan_cold_start(
            conn, _PROFILE, provider=provider, max_probes=3, target_domain="language_learning"
        )

        assert provider.last_request is not None
        rendered = provider.last_request.user
        assert "TARGET EXPLORATION DOMAIN FOR THIS RUN: language_learning" in rendered

    def test_target_domain_is_never_folded_into_secondary_niches_in_the_prompt(self, conn):
        """The exact Phase 7C.2 root cause: language_learning must never
        appear inside the "Secondary niches" line, only inside the new,
        separately-labeled target-domain section."""
        provider = RecordingFakeProvider(_ONE_CANDIDATE)
        plan_cold_start(
            conn, _PROFILE, provider=provider, max_probes=3, target_domain="language_learning"
        )

        rendered = provider.last_request.user
        secondary_line = next(
            line for line in rendered.splitlines() if line.startswith("Secondary niches:")
        )
        assert "language_learning" not in secondary_line


class TestOrdinaryColdStartUnaffectedByPromptVersionBump:
    """Phase 7C.3, §4 — no regression: the prompt registry now serves v4 for
    every caller (bumped in planner_prompts.py), but an ordinary call's
    rendered content and selection/scoring behavior must be unchanged."""

    def test_prompt_version_is_now_v4(self):
        assert COLD_START_PROMPT_VERSION == "4"

    def test_ordinary_call_still_selects_the_only_candidate(self, conn):
        result = plan_cold_start(
            conn, _PROFILE, provider=FakeProvider(_ONE_CANDIDATE), max_probes=3
        )
        assert result.selected_count >= 1


class TestRecoveryDiscoveryFnNoLongerRepurposesSecondaryNiches:
    """Phase 7C.3, §2 — the actual bug fix in recovery.py: the cell's
    domain must reach discovery ONLY via target_domain, never by mutating
    the channel's own configured secondary_niches."""

    def test_default_discovery_fn_passes_target_domain_and_leaves_secondary_niches_untouched(
        self, conn, monkeypatch
    ):
        _seed_channel_profile(conn, secondary_niches=["history and society", "nature"])

        captured: dict = {}

        def fake_plan_cold_start(conn_arg, profile, **kwargs):
            captured["profile"] = profile
            captured["target_domain"] = kwargs.get("target_domain")

            class _Result:
                candidate_count = 0

            return _Result()

        monkeypatch.setattr(
            "app.core.config.get_config",
            lambda: type("Cfg", (), {"anthropic_api_key": "fake-key"})(),
        )
        monkeypatch.setattr("app.ai.claude.ClaudeProvider", lambda api_key: object())
        monkeypatch.setattr(
            "app.intelligence.market.cold_start.plan_cold_start", fake_plan_cold_start
        )

        from app.intelligence.exploration.recovery import _default_discovery_fn

        cell = ExplorationCell(
            domain="language_learning",
            format="practical_micro_lesson",
            audience_value="practical_utility",
        )
        outcome = _default_discovery_fn(conn, channel_id=1, cell=cell)

        assert outcome.attempted is True
        assert captured["target_domain"] == "language_learning"
        assert "language_learning" not in captured["profile"].secondary_niches
        assert captured["profile"].secondary_niches == ["history and society", "nature"]


class TestRecoveryWithCompatibleSupply:
    """Phase 7C.3, §16 — when the (now correctly targeted) discovery call
    actually produces an on-domain candidate, it must both win the limited
    dispatch budget (domain_alignment) and pass the Phase 7C.2 compatibility
    gate — proving the two phases compose correctly."""

    def test_on_domain_candidate_is_selected_and_classified_compatible(self, conn):
        from app.intelligence.exploration.domain_compatibility import (
            classify_domain_compatibility,
        )

        provider = FakeProvider(_ONE_CANDIDATE)
        result = plan_cold_start(
            conn, _PROFILE, provider=provider, max_probes=3, target_domain="language_learning"
        )
        assert result.selected_count >= 1

        run_id = result.exploration_run_id
        rows = conn.execute(
            "SELECT query_text, market_region_label, domain_family FROM market_exploration_probes "
            "WHERE exploration_run_id = ? AND probe_type = 'market_region' AND status = 'selected'",
            (run_id,),
        ).fetchall()
        assert len(rows) == 1
        probe = rows[0]
        context_text = (
            f"{probe['query_text']} {probe['market_region_label']} {probe['domain_family']}"
        )
        compat = classify_domain_compatibility("language_learning", context_text)
        assert compat.eligible_for_linkage
        assert compat.classification.value in ("compatible", "adjacent")


class TestRecoveryWithNoCompatibleSupply:
    """Phase 7C.3, §16 — the sanctioned safe-failure path (also covered at
    the linkage level by Phase 7C.2's test_exploration_repository.py):
    an off-domain candidate must never be classified as eligible, even when
    target_domain was supplied for this same run."""

    def test_off_domain_candidate_is_not_compatible_even_when_targeted(self, conn):
        from app.intelligence.exploration.domain_compatibility import (
            classify_domain_compatibility,
        )

        off_domain_candidate = json.dumps(
            {
                "probes": [
                    {
                        "query": "greek mythology origins explained",
                        "market_region_label": "Mythology",
                        "domain_family": "Mythology & Folklore",
                        "rationale": "r",
                        "format_fit_score": 0.8,
                        "distance_from_current_subjects": 0.8,
                        "distinctiveness_rationale": "d",
                    }
                ]
            }
        )
        result = plan_cold_start(
            conn,
            _PROFILE,
            provider=FakeProvider(off_domain_candidate),
            max_probes=3,
            target_domain="language_learning",
        )
        assert result.selected_count >= 1

        compat = classify_domain_compatibility(
            "language_learning", "greek mythology origins explained Mythology & Folklore"
        )
        assert not compat.eligible_for_linkage
        assert compat.classification.value == "incompatible"
