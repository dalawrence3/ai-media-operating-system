"""Storage backend factory and obj_storage_objects registry integration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.storage.backends import LocalFilesystemStorage, S3CompatibleStorage
from app.storage.protocol import ObjectRef, StorageBackend


def get_storage_backend(config=None) -> StorageBackend:
    """Return the configured storage backend.

    Falls back to LocalFilesystemStorage when config is absent or backend is 'local'.
    """
    if config is None:
        from app.core.config import get_config

        config = get_config()

    backend = getattr(config, "object_storage_backend", "local")
    if backend == "s3":
        return S3CompatibleStorage(
            bucket=config.object_storage_bucket,
            endpoint_url=getattr(config, "object_storage_endpoint", ""),
            region_name=getattr(config, "object_storage_region", "us-east-1"),
            access_key=getattr(config, "object_storage_access_key", ""),
            secret_key=getattr(config, "object_storage_secret_key", ""),
        )

    # local backend
    artifacts_path = getattr(config, "artifacts_path", Path("artifacts"))
    return LocalFilesystemStorage(root_path=artifacts_path, bucket="artifacts")


def _scoped_key(workspace_id: str, channel_id: str | None, relative_key: str) -> str:
    """Build a workspace-scoped object key that prevents cross-workspace collisions."""
    parts = [workspace_id]
    if channel_id:
        parts.append(channel_id)
    parts.append(relative_key.lstrip("/"))
    return "/".join(parts)


def register_storage_object(
    conn: Any,
    *,
    workspace_id: str,
    channel_id: str | None,
    storage_backend: str,
    bucket: str,
    object_key: str,
    sha256: str,
    byte_size: int,
    content_type: str,
    source_entity_type: str,
    source_entity_id: str,
) -> int:
    """Insert a row into obj_storage_objects and return its id.

    This is the canonical registry entry — it records provenance and integrity
    metadata for every stored artifact. The binary data lives in the backend.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        """
        INSERT INTO obj_storage_objects
            (workspace_id, channel_id, storage_backend, bucket, object_key,
             sha256, byte_size, content_type,
             source_entity_type, source_entity_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            channel_id,
            storage_backend,
            bucket,
            object_key,
            sha256,
            byte_size,
            content_type,
            source_entity_type,
            source_entity_id,
            now,
        ),
    )
    return cur.lastrowid


def ref_to_registry_params(
    ref: ObjectRef,
    workspace_id: str,
    channel_id: str | None,
    source_entity_type: str,
    source_entity_id: str,
) -> dict:
    """Convert an ObjectRef into the kwargs for register_storage_object."""
    return {
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "storage_backend": ref.storage_backend,
        "bucket": ref.bucket,
        "object_key": ref.object_key,
        "sha256": ref.sha256,
        "byte_size": ref.byte_size,
        "content_type": ref.content_type,
        "source_entity_type": source_entity_type,
        "source_entity_id": source_entity_id,
    }
