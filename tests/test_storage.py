"""Tests for the object storage abstraction (M15.3).

Covers:
  - ObjectRef creation and SHA-256 verification
  - LocalFilesystemStorage: put, get, exists, delete, overwrite guard, traversal guard
  - S3CompatibleStorage: put, get, exists, delete, overwrite guard (all mocked)
  - registry.register_storage_object
  - StorageBackend protocol satisfaction
  - No real S3 or MinIO credentials required
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.storage.backends import LocalFilesystemStorage, S3CompatibleStorage
from app.storage.protocol import (
    ObjectExistsError,
    ObjectNotFoundError,
    ObjectRef,
    StorageBackend,
    StorageError,
    StorageIntegrityError,
)
from app.storage.registry import register_storage_object

# ── ObjectRef ───────────────────────────────────────────────────────────────


def test_object_ref_is_frozen():
    ref = ObjectRef(
        storage_backend="local",
        bucket="b",
        object_key="k",
        sha256="abc",
        byte_size=10,
        content_type="application/octet-stream",
    )
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        ref.sha256 = "changed"  # type: ignore[misc]


def test_object_ref_verify_passes():
    data = b"hello"
    sha = hashlib.sha256(data).hexdigest()
    ref = ObjectRef("local", "b", "k", sha, len(data), "application/octet-stream")
    ref.verify(data)  # no exception


def test_object_ref_verify_fails():
    data = b"hello"
    ref = ObjectRef("local", "b", "k", "wronghash", len(data), "application/octet-stream")
    with pytest.raises(StorageIntegrityError):
        ref.verify(data)


# ── LocalFilesystemStorage ──────────────────────────────────────────────────


@pytest.fixture
def local_storage(tmp_path: Path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(root_path=tmp_path / "artifacts", bucket="test")


def test_local_put_and_get(local_storage: LocalFilesystemStorage):
    data = b"test artifact content"
    ref = local_storage.put("ws1/narration/seg1.wav", data, "audio/wav")
    assert ref.storage_backend == "local"
    assert ref.bucket == "test"
    assert ref.byte_size == len(data)
    assert ref.sha256 == hashlib.sha256(data).hexdigest()
    assert ref.content_type == "audio/wav"
    result = local_storage.get("ws1/narration/seg1.wav")
    assert result == data


def test_local_exists_true(local_storage: LocalFilesystemStorage):
    local_storage.put("ws1/f.txt", b"x")
    assert local_storage.exists("ws1/f.txt") is True


def test_local_exists_false(local_storage: LocalFilesystemStorage):
    assert local_storage.exists("ws1/missing.txt") is False


def test_local_get_missing_raises(local_storage: LocalFilesystemStorage):
    with pytest.raises(ObjectNotFoundError):
        local_storage.get("ws1/missing.txt")


def test_local_put_no_overwrite_raises(local_storage: LocalFilesystemStorage):
    local_storage.put("ws1/f.txt", b"original")
    with pytest.raises(ObjectExistsError):
        local_storage.put("ws1/f.txt", b"new content", allow_overwrite=False)


def test_local_put_allow_overwrite(local_storage: LocalFilesystemStorage):
    local_storage.put("ws1/f.txt", b"original")
    local_storage.put("ws1/f.txt", b"updated", allow_overwrite=True)
    assert local_storage.get("ws1/f.txt") == b"updated"


def test_local_delete(local_storage: LocalFilesystemStorage):
    local_storage.put("ws1/f.txt", b"data")
    local_storage.delete("ws1/f.txt")
    assert not local_storage.exists("ws1/f.txt")


def test_local_delete_missing_raises(local_storage: LocalFilesystemStorage):
    with pytest.raises(ObjectNotFoundError):
        local_storage.delete("ws1/missing.txt")


def test_local_path_traversal_blocked(local_storage: LocalFilesystemStorage):
    with pytest.raises(StorageError):
        local_storage.put("../../etc/passwd", b"malicious")


def test_local_backend_name(local_storage: LocalFilesystemStorage):
    assert local_storage.backend_name == "local"


def test_local_satisfies_protocol(local_storage: LocalFilesystemStorage):
    assert isinstance(local_storage, StorageBackend)


def test_local_creates_nested_dirs(local_storage: LocalFilesystemStorage):
    local_storage.put("ws1/channel1/narration/run1/seg1.wav", b"audio")
    assert local_storage.exists("ws1/channel1/narration/run1/seg1.wav")


def test_local_sha256_matches_data(local_storage: LocalFilesystemStorage):
    data = b"precise content"
    ref = local_storage.put("ws1/precise.bin", data)
    ref.verify(data)  # no exception


# ── S3CompatibleStorage (all mocked) ───────────────────────────────────────


def _make_s3_storage(mock_client=None) -> S3CompatibleStorage:
    if mock_client is None:
        mock_client = MagicMock()
    return S3CompatibleStorage(bucket="ace-test", _client=mock_client)


def test_s3_put_returns_ref():
    mc = MagicMock()
    mc.head_object.side_effect = Exception("NoSuchKey")
    storage = _make_s3_storage(mc)
    data = b"s3 payload"
    ref = storage.put("ws1/video.mp4", data, "video/mp4")
    assert ref.storage_backend == "s3"
    assert ref.bucket == "ace-test"
    assert ref.sha256 == hashlib.sha256(data).hexdigest()
    assert ref.byte_size == len(data)
    assert ref.content_type == "video/mp4"
    mc.upload_fileobj.assert_called_once()


def test_s3_put_no_overwrite_raises_when_exists():
    mc = MagicMock()
    mc.head_object.return_value = {}  # object exists
    storage = _make_s3_storage(mc)
    with pytest.raises(ObjectExistsError):
        storage.put("ws1/exists.mp4", b"data", allow_overwrite=False)


def test_s3_put_allow_overwrite_skips_head_check():
    mc = MagicMock()
    storage = _make_s3_storage(mc)
    storage.put("ws1/f.mp4", b"data", allow_overwrite=True)
    mc.head_object.assert_not_called()
    mc.upload_fileobj.assert_called_once()


def test_s3_get_calls_client():
    mc = MagicMock()
    mc.get_object.return_value = {"Body": MagicMock(read=lambda: b"s3content")}
    storage = _make_s3_storage(mc)
    result = storage.get("ws1/video.mp4")
    assert result == b"s3content"


def test_s3_get_missing_raises():
    mc = MagicMock()
    mc.get_object.side_effect = Exception("NoSuchKey")
    storage = _make_s3_storage(mc)
    with pytest.raises(ObjectNotFoundError):
        storage.get("ws1/missing.mp4")


def test_s3_exists_true():
    mc = MagicMock()
    mc.head_object.return_value = {}
    storage = _make_s3_storage(mc)
    assert storage.exists("ws1/f.mp4") is True


def test_s3_exists_false():
    mc = MagicMock()
    mc.head_object.side_effect = Exception("NoSuchKey")
    storage = _make_s3_storage(mc)
    assert storage.exists("ws1/missing.mp4") is False


def test_s3_delete_calls_client():
    mc = MagicMock()
    mc.head_object.return_value = {}  # exists
    storage = _make_s3_storage(mc)
    storage.delete("ws1/f.mp4")
    mc.delete_object.assert_called_once_with(Bucket="ace-test", Key="ws1/f.mp4")


def test_s3_delete_missing_raises():
    mc = MagicMock()
    mc.head_object.side_effect = Exception("NoSuchKey")
    storage = _make_s3_storage(mc)
    with pytest.raises(ObjectNotFoundError):
        storage.delete("ws1/missing.mp4")


def test_s3_backend_name():
    storage = _make_s3_storage()
    assert storage.backend_name == "s3"


def test_s3_satisfies_protocol():
    storage = _make_s3_storage()
    assert isinstance(storage, StorageBackend)


# ── registry.register_storage_object ───────────────────────────────────────


def test_register_storage_object(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "reg.db")
    obj_id = register_storage_object(
        conn,
        workspace_id="ws-1",
        channel_id="ch-1",
        storage_backend="local",
        bucket="artifacts",
        object_key="ws-1/ch-1/narration/run1/seg1.wav",
        sha256="abc123def456",
        byte_size=8192,
        content_type="audio/wav",
        source_entity_type="narration_segment_asset",
        source_entity_id="42",
    )
    conn.commit()
    assert isinstance(obj_id, int)
    row = conn.execute(
        "SELECT * FROM obj_storage_objects WHERE id = ?", (obj_id,)
    ).fetchone()
    assert row["workspace_id"] == "ws-1"
    assert row["sha256"] == "abc123def456"
    assert row["source_entity_type"] == "narration_segment_asset"
    conn.close()


def test_register_storage_object_unique_key_constraint(tmp_path: Path):
    from app.core.database import open_db

    conn = open_db(tmp_path / "uniq.db")
    kwargs = dict(
        workspace_id="ws-1",
        channel_id=None,
        storage_backend="s3",
        bucket="ace-prod",
        object_key="ws-1/video.mp4",
        sha256="aaa",
        byte_size=1000,
        content_type="video/mp4",
        source_entity_type="render_job",
        source_entity_id="1",
    )
    register_storage_object(conn, **kwargs)
    conn.commit()
    import sqlite3 as _sqlite3

    with pytest.raises(_sqlite3.IntegrityError):
        register_storage_object(conn, **kwargs)
        conn.commit()
    conn.close()
