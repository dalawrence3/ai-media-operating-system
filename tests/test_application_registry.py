"""Tests for the typed extension registry."""

from __future__ import annotations

import pytest

from app.application.registry import (
    ExtensionEntry,
    ExtensionRegistry,
    get_default_registry,
    reset_default_registry,
)


def _noop_handler(*args: object, **kwargs: object) -> None:
    pass


class TestExtensionEntry:
    def test_entry_creation(self):
        entry = ExtensionEntry(
            key="test.handler",
            capability="pipeline_review",
            version="1.0.0",
            handler=_noop_handler,
        )
        assert entry.key == "test.handler"
        assert entry.enabled is True

    def test_entry_disabled_by_default_when_set(self):
        entry = ExtensionEntry(
            key="test.handler",
            capability="something",
            version="1.0.0",
            handler=_noop_handler,
            enabled=False,
        )
        assert entry.enabled is False


class TestExtensionRegistry:
    def test_register_and_get(self):
        reg = ExtensionRegistry()
        reg.register(
            key="test.ext", capability="review", version="1.0.0", handler=_noop_handler
        )
        entry = reg.get("test.ext")
        assert entry is not None
        assert entry.key == "test.ext"

    def test_get_unknown_returns_none(self):
        from app.application.errors import ExtensionNotFoundError
        reg = ExtensionRegistry()
        with pytest.raises(ExtensionNotFoundError):
            reg.get("nonexistent")

    def test_has(self):
        reg = ExtensionRegistry()
        reg.register(key="k1", capability="c", version="1.0", handler=_noop_handler)
        assert reg.has("k1")
        assert not reg.has("k2")

    def test_duplicate_register_raises(self):
        from app.application.errors import ExtensionAlreadyRegisteredError
        reg = ExtensionRegistry()
        reg.register(key="dup", capability="c", version="1.0", handler=_noop_handler)
        with pytest.raises(ExtensionAlreadyRegisteredError):
            reg.register(key="dup", capability="c", version="1.0", handler=_noop_handler)

    def test_enable_disable(self):
        reg = ExtensionRegistry()
        reg.register(key="tog", capability="c", version="1.0", handler=_noop_handler)
        reg.disable("tog")
        assert not reg.get("tog").enabled
        reg.enable("tog")
        assert reg.get("tog").enabled

    def test_disable_unknown_raises(self):
        from app.application.errors import ExtensionNotFoundError
        reg = ExtensionRegistry()
        with pytest.raises(ExtensionNotFoundError):
            reg.disable("nonexistent")

    def test_list_all(self):
        reg = ExtensionRegistry()
        reg.register(key="a", capability="x", version="1.0", handler=_noop_handler)
        reg.register(key="b", capability="y", version="1.0", handler=_noop_handler)
        entries = reg.list_entries()
        keys = {e.key for e in entries}
        assert {"a", "b"} <= keys

    def test_list_by_capability(self):
        reg = ExtensionRegistry()
        reg.register(key="r1", capability="review", version="1.0", handler=_noop_handler)
        reg.register(key="o1", capability="other", version="1.0", handler=_noop_handler)
        review = reg.list_entries(capability="review")
        assert all(e.capability == "review" for e in review)
        assert len(review) == 1

    def test_unregister(self):
        reg = ExtensionRegistry()
        reg.register(key="rem", capability="c", version="1.0", handler=_noop_handler)
        reg.unregister("rem")
        assert not reg.has("rem")

    def test_clear(self):
        reg = ExtensionRegistry()
        reg.register(key="x", capability="c", version="1.0", handler=_noop_handler)
        reg.clear()
        assert len(reg.list_entries()) == 0


class TestDefaultRegistry:
    def setup_method(self):
        reset_default_registry()

    def teardown_method(self):
        reset_default_registry()

    def test_get_default_registry_returns_same_instance(self):
        r1 = get_default_registry()
        r2 = get_default_registry()
        assert r1 is r2

    def test_reset_gives_fresh_registry(self):
        r1 = get_default_registry()
        r1.register(key="tmp", capability="c", version="1.0", handler=_noop_handler)
        reset_default_registry()
        r2 = get_default_registry()
        assert not r2.has("tmp")
