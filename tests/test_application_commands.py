"""Tests for application commands, stage constants, and prerequisite graph."""

from __future__ import annotations

import pytest

from app.application.commands import (
    ALL_COMMAND_TYPES,
    COMMAND_CONTRACT_VERSION,
    PIPELINE_STAGES,
    REVIEW_REQUIRED_STAGES,
    SCHEDULE_TYPES,
    STAGE_PREREQUISITES,
    CreateScheduleCommand,
    PausePipelineCommand,
    RecoverPipelineCommand,
    StartPipelineCommand,
)


class TestPipelineStages:
    def test_stages_are_ordered_list(self):
        assert isinstance(PIPELINE_STAGES, list)
        assert len(PIPELINE_STAGES) == 10

    def test_research_is_first(self):
        assert PIPELINE_STAGES[0] == "research"

    def test_learning_is_last(self):
        assert PIPELINE_STAGES[-1] == "learning"

    def test_no_duplicates(self):
        assert len(PIPELINE_STAGES) == len(set(PIPELINE_STAGES))

    def test_canonical_order(self):
        expected = [
            "research", "script_generation", "production_plan",
            "narration", "captions", "visual_intelligence",
            "rendering", "publishing", "analytics", "learning",
        ]
        assert PIPELINE_STAGES == expected


class TestReviewRequiredStages:
    def test_is_frozenset(self):
        assert isinstance(REVIEW_REQUIRED_STAGES, frozenset)

    def test_rendering_requires_review(self):
        assert "rendering" in REVIEW_REQUIRED_STAGES

    def test_script_generation_requires_review(self):
        assert "script_generation" in REVIEW_REQUIRED_STAGES

    def test_research_does_not_require_review(self):
        assert "research" not in REVIEW_REQUIRED_STAGES

    def test_publishing_does_not_require_review(self):
        assert "publishing" not in REVIEW_REQUIRED_STAGES


class TestStagePrerequisites:
    def test_research_has_no_prerequisites(self):
        assert STAGE_PREREQUISITES["research"] == []

    def test_script_generation_needs_research(self):
        assert "research" in STAGE_PREREQUISITES["script_generation"]

    def test_rendering_needs_three_stages(self):
        prereqs = STAGE_PREREQUISITES["rendering"]
        assert "narration" in prereqs
        assert "captions" in prereqs
        assert "visual_intelligence" in prereqs

    def test_learning_needs_analytics(self):
        assert "analytics" in STAGE_PREREQUISITES["learning"]

    def test_all_stages_have_prerequisites_entry(self):
        for stage in PIPELINE_STAGES:
            assert stage in STAGE_PREREQUISITES


class TestScheduleTypes:
    def test_four_schedule_types(self):
        assert len(SCHEDULE_TYPES) == 4

    def test_cron_is_valid(self):
        assert "cron" in SCHEDULE_TYPES

    def test_interval_is_valid(self):
        assert "interval" in SCHEDULE_TYPES

    def test_once_is_valid(self):
        assert "once" in SCHEDULE_TYPES

    def test_after_event_is_valid(self):
        assert "after_event" in SCHEDULE_TYPES


class TestCommandContractVersion:
    def test_version_format(self):
        parts = COMMAND_CONTRACT_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_major_is_13(self):
        major = int(COMMAND_CONTRACT_VERSION.split(".")[0])
        assert major == 13


class TestStartPipelineCommand:
    def test_frozen(self):
        cmd = StartPipelineCommand(
            workspace_id="ws1", channel_id="ch1", topic_id=1,
            actor="test", idempotency_key="key1",
        )
        with pytest.raises((AttributeError, TypeError)):
            cmd.actor = "other"  # type: ignore[misc]

    def test_default_stages(self):
        cmd = StartPipelineCommand(
            workspace_id="ws", channel_id="ch", topic_id=1,
            actor="a", idempotency_key="k",
        )
        assert cmd.start_stage == "research"
        assert cmd.end_stage == "learning"

    def test_carries_contract_version(self):
        cmd = StartPipelineCommand(
            workspace_id="ws", channel_id="ch", topic_id=1,
            actor="a", idempotency_key="k",
        )
        assert cmd.contract_version == COMMAND_CONTRACT_VERSION

    def test_all_optional_fields_default_none(self):
        cmd = StartPipelineCommand(
            workspace_id="ws", channel_id="ch", topic_id=1,
            actor="a", idempotency_key="k",
        )
        assert cmd.platform_account_id is None
        assert cmd.experiment_id is None
        assert cmd.policy_overrides is None
        assert cmd.correlation_id is None


class TestPausePipelineCommand:
    def test_basic_construction(self):
        cmd = PausePipelineCommand(
            pipeline_id="p1", workspace_id="ws1", actor="admin"
        )
        assert cmd.pipeline_id == "p1"
        assert cmd.reason == ""


class TestRecoverPipelineCommand:
    def test_from_stage_defaults_none(self):
        cmd = RecoverPipelineCommand(
            pipeline_id="p1", workspace_id="ws1", actor="admin"
        )
        assert cmd.from_stage is None

    def test_from_stage_can_be_set(self):
        cmd = RecoverPipelineCommand(
            pipeline_id="p1", workspace_id="ws1", actor="admin",
            from_stage="narration",
        )
        assert cmd.from_stage == "narration"


class TestCreateScheduleCommand:
    def test_basic_construction(self):
        cmd = CreateScheduleCommand(
            workspace_id="ws1", name="Daily",
            operation_type="publish",
            schedule_type="interval",
            schedule_config={"interval_seconds": 3600},
            actor="admin",
        )
        assert cmd.channel_id is None
        assert cmd.timezone == "UTC"


class TestAllCommandTypes:
    def test_is_frozenset(self):
        assert isinstance(ALL_COMMAND_TYPES, frozenset)

    def test_start_pipeline_in_set(self):
        assert StartPipelineCommand in ALL_COMMAND_TYPES

    def test_recover_pipeline_in_set(self):
        assert RecoverPipelineCommand in ALL_COMMAND_TYPES

    def test_minimum_count(self):
        assert len(ALL_COMMAND_TYPES) >= 14
