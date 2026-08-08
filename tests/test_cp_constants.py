"""Tests for Phase 12 control plane constants."""

from __future__ import annotations

from app.control_plane.constants import (
    ALL_EVENT_TYPES,
    ASSIGNMENT_STATUSES,
    AUTOMATION_AUTONOMOUS,
    AUTOMATION_LEVELS,
    AUTOMATION_MANUAL,
    AUTOMATION_SUPERVISED,
    BUDGET_ACTIONS,
    BUDGET_PERIODS,
    BUDGET_SCOPES,
    BUDGET_WARNING_THRESHOLD,
    CREDENTIAL_STATUSES,
    CREDENTIAL_TYPES,
    EVENT_PROCESSING_STATUSES,
    EXPERIMENT_IMMUTABLE_STATUSES,
    EXPERIMENT_STATUSES,
    HEALTH_ENTITY_TYPES,
    HEALTH_STATUSES,
    KNOWN_PLATFORMS,
    MAX_DELIVERY_ATTEMPTS,
    PLATFORM_ACCOUNT_STATUSES,
    PROVIDER_DOMAINS,
    PROVIDER_STATUSES,
    REVIEW_ITEM_TYPES,
    REVIEW_STATUSES,
    VARIANT_TYPE_CONTROL,
    VARIANT_TYPE_TREATMENT,
    VARIANT_TYPES,
    WORKFLOW_ACTION_TYPES,
    WORKFLOW_CONDITION_OPERATORS,
    WORKFLOW_RUN_STATUSES,
    WORKFLOW_STATUSES,
)


class TestAutomationConstants:
    def test_all_levels_present(self):
        assert AUTOMATION_MANUAL in AUTOMATION_LEVELS
        assert AUTOMATION_SUPERVISED in AUTOMATION_LEVELS
        assert AUTOMATION_AUTONOMOUS in AUTOMATION_LEVELS
        assert len(AUTOMATION_LEVELS) == 3

    def test_levels_are_frozenset(self):
        assert isinstance(AUTOMATION_LEVELS, frozenset)


class TestPlatformConstants:
    def test_known_platforms(self):
        assert "youtube" in KNOWN_PLATFORMS
        assert "tiktok" in KNOWN_PLATFORMS
        assert "instagram" in KNOWN_PLATFORMS

    def test_account_statuses_complete(self):
        required = {
            "connected",
            "disconnected",
            "credential_invalid",
            "credential_expiring",
            "quota_limited",
            "paused",
        }
        assert required <= PLATFORM_ACCOUNT_STATUSES


class TestCredentialConstants:
    def test_types(self):
        assert "oauth2" in CREDENTIAL_TYPES
        assert "api_key" in CREDENTIAL_TYPES
        assert "service_account" in CREDENTIAL_TYPES

    def test_statuses(self):
        assert "active" in CREDENTIAL_STATUSES
        assert "expired" in CREDENTIAL_STATUSES
        assert "revoked" in CREDENTIAL_STATUSES


class TestEventConstants:
    def test_all_event_types_nonempty(self):
        assert len(ALL_EVENT_TYPES) >= 30

    def test_processing_statuses(self):
        required = {"pending", "processing", "completed", "failed", "dead_lettered"}
        assert required == EVENT_PROCESSING_STATUSES

    def test_max_delivery_attempts(self):
        assert MAX_DELIVERY_ATTEMPTS >= 1


class TestWorkflowConstants:
    def test_condition_operators(self):
        required = {
            "equals",
            "not_equals",
            "greater_than",
            "less_than",
            "in",
            "not_in",
            "exists",
            "boolean",
        }
        assert required == WORKFLOW_CONDITION_OPERATORS

    def test_action_types(self):
        assert "pause_account" in WORKFLOW_ACTION_TYPES
        assert "notify" in WORKFLOW_ACTION_TYPES
        assert "queue_review" in WORKFLOW_ACTION_TYPES

    def test_workflow_statuses(self):
        assert "draft" in WORKFLOW_STATUSES
        assert "active" in WORKFLOW_STATUSES

    def test_workflow_run_statuses(self):
        assert "running" in WORKFLOW_RUN_STATUSES
        assert "completed" in WORKFLOW_RUN_STATUSES
        assert "failed" in WORKFLOW_RUN_STATUSES


class TestExperimentConstants:
    def test_immutable_statuses_subset_of_statuses(self):
        assert EXPERIMENT_IMMUTABLE_STATUSES <= EXPERIMENT_STATUSES

    def test_variant_types(self):
        assert VARIANT_TYPE_CONTROL in VARIANT_TYPES
        assert VARIANT_TYPE_TREATMENT in VARIANT_TYPES

    def test_assignment_statuses(self):
        assert "active" in ASSIGNMENT_STATUSES
        assert "excluded" in ASSIGNMENT_STATUSES


class TestBudgetConstants:
    def test_scopes(self):
        assert "workspace" in BUDGET_SCOPES
        assert "channel" in BUDGET_SCOPES
        assert "platform_account" in BUDGET_SCOPES

    def test_periods(self):
        assert "daily" in BUDGET_PERIODS
        assert "weekly" in BUDGET_PERIODS
        assert "monthly" in BUDGET_PERIODS

    def test_actions(self):
        assert "warn" in BUDGET_ACTIONS
        assert "pause" in BUDGET_ACTIONS
        assert "block" in BUDGET_ACTIONS

    def test_warning_threshold_range(self):
        assert 0 < BUDGET_WARNING_THRESHOLD < 1


class TestHealthConstants:
    def test_entity_types(self):
        assert "workspace" in HEALTH_ENTITY_TYPES
        assert "channel" in HEALTH_ENTITY_TYPES
        assert "platform_account" in HEALTH_ENTITY_TYPES
        assert "provider" in HEALTH_ENTITY_TYPES

    def test_statuses(self):
        assert "healthy" in HEALTH_STATUSES
        assert "degraded" in HEALTH_STATUSES
        assert "unavailable" in HEALTH_STATUSES


class TestProviderConstants:
    def test_domains(self):
        required = {"ai", "tts", "publishing", "analytics", "asset", "storage", "notification"}
        assert required == PROVIDER_DOMAINS

    def test_statuses(self):
        assert "active" in PROVIDER_STATUSES
        assert "degraded" in PROVIDER_STATUSES
        assert "inactive" in PROVIDER_STATUSES


class TestReviewConstants:
    def test_item_types_nonempty(self):
        assert len(REVIEW_ITEM_TYPES) >= 3

    def test_review_statuses(self):
        assert "open" in REVIEW_STATUSES
        assert "resolved" in REVIEW_STATUSES
