"""Object storage abstraction — provider-neutral artifact persistence.

Backends:
  LocalFilesystemStorage  — dev/test; files on disk relative to artifacts_path
  S3CompatibleStorage     — production; S3-compatible endpoint (AWS S3 or MinIO)

Usage:
  storage = get_storage_backend(config)
  ref = await storage.put(workspace_id, channel_id, entity_type, entity_id,
                          object_key, data, content_type)
  data = await storage.get(ref.object_key)
"""

from app.storage.backends import LocalFilesystemStorage, S3CompatibleStorage
from app.storage.protocol import ObjectRef, StorageBackend, StorageError
from app.storage.registry import get_storage_backend, register_storage_object

__all__ = [
    "LocalFilesystemStorage",
    "S3CompatibleStorage",
    "ObjectRef",
    "StorageBackend",
    "StorageError",
    "get_storage_backend",
    "register_storage_object",
]
