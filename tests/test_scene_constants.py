"""Tests for Phase 7 scene-planning constants."""

from app.scenes.constants import (
    ASSET_CATEGORIES,
    ASSET_PRIORITIES,
    CAMERA_MOVEMENTS,
    DEFAULT_ASSET_PREFERENCES,
    DEFAULT_CAMERA_MOVEMENT,
    DEFAULT_SHOT_TYPE,
    LICENSE_STATUSES,
    MANIFEST_SCHEMA_VERSION,
    PLANNER_VERSION,
    REVIEW_EVENT_TYPES,
    SECTION_ASSET_PREFERENCES,
    SECTION_CAMERA_MAP,
    SECTION_SHOT_TYPE_MAP,
    SHOT_TYPES,
    TRANSITIONS,
    VERIFICATION_STATUSES,
)


def test_manifest_schema_version_is_string():
    assert isinstance(MANIFEST_SCHEMA_VERSION, str)
    assert MANIFEST_SCHEMA_VERSION


def test_planner_version_is_string():
    assert isinstance(PLANNER_VERSION, str)
    assert PLANNER_VERSION


def test_shot_types_non_empty_frozenset():
    assert len(SHOT_TYPES) >= 5


def test_camera_movements_non_empty_frozenset():
    assert len(CAMERA_MOVEMENTS) >= 6


def test_transitions_non_empty_frozenset():
    assert len(TRANSITIONS) >= 5


def test_asset_categories_non_empty():
    assert len(ASSET_CATEGORIES) >= 10


def test_license_statuses_non_empty():
    assert len(LICENSE_STATUSES) >= 6


def test_asset_priorities_non_empty():
    assert len(ASSET_PRIORITIES) >= 3


def test_verification_statuses_non_empty():
    assert len(VERIFICATION_STATUSES) >= 3


def test_review_event_types():
    assert "approved" in REVIEW_EVENT_TYPES
    assert "rejected" in REVIEW_EVENT_TYPES
    assert "scene_rejected" in REVIEW_EVENT_TYPES


def test_section_shot_type_map_covers_known_sections():
    for section in ("hook", "intro", "body", "evidence", "outro", "cta"):
        shot = SECTION_SHOT_TYPE_MAP.get(section)
        assert shot in SHOT_TYPES, f"Section '{section}' maps to unknown shot '{shot}'"


def test_default_shot_type_in_shot_types():
    assert DEFAULT_SHOT_TYPE in SHOT_TYPES


def test_section_camera_map_covers_known_sections():
    for section in ("hook", "intro", "body", "evidence", "outro", "cta"):
        cam = SECTION_CAMERA_MAP.get(section)
        assert cam in CAMERA_MOVEMENTS, f"Section '{section}' maps to unknown camera '{cam}'"


def test_default_camera_movement_in_movements():
    assert DEFAULT_CAMERA_MOVEMENT in CAMERA_MOVEMENTS


def test_section_asset_preferences_all_valid_categories():
    for section, cats in SECTION_ASSET_PREFERENCES.items():
        for cat in cats:
            assert cat in ASSET_CATEGORIES, (
                f"Section '{section}' has invalid asset category '{cat}'"
            )


def test_default_asset_preferences_non_empty():
    assert len(DEFAULT_ASSET_PREFERENCES) >= 1
    for cat in DEFAULT_ASSET_PREFERENCES:
        assert cat in ASSET_CATEGORIES
