"""StorageBackend protocol and shared types."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class StorageError(Exception):
    """Base class for storage errors."""


class ObjectNotFoundError(StorageError):
    """Raised when a requested object key does not exist in the backend."""


class ObjectExistsError(StorageError):
    """Raised when an object key already exists and overwrite is not allowed."""


class StorageIntegrityError(StorageError):
    """Raised when SHA-256 verification fails on upload or download."""


@dataclass(frozen=True)
class ObjectRef:
    """Canonical reference to a stored object.

    All fields are immutable after creation.
    object_key is scoped by workspace to prevent cross-workspace collisions.
    sha256 is the hex-encoded SHA-256 of the raw bytes — verified on upload.
    """

    storage_backend: str  # "local" or "s3"
    bucket: str
    object_key: str       # workspace-scoped: workspace_id/channel_id/…/filename
    sha256: str           # hex-encoded SHA-256 of raw bytes
    byte_size: int
    content_type: str

    def verify(self, data: bytes) -> None:
        """Raise StorageIntegrityError if sha256 does not match data."""
        actual = hashlib.sha256(data).hexdigest()
        if actual != self.sha256:
            raise StorageIntegrityError(
                f"SHA-256 mismatch for {self.object_key}: "
                f"expected {self.sha256}, got {actual}"
            )


@runtime_checkable
class StorageBackend(Protocol):
    """Provider-neutral object storage contract.

    Implementations must be safe to call from worker processes.
    No credentials or session state may be stored in the backend instance
    unless they are read from environment/config on construction.
    """

    @property
    def backend_name(self) -> str:
        """Identifier string: 'local' or 's3'."""
        ...

    @property
    def bucket(self) -> str:
        """Bucket / container name."""
        ...

    def put(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        *,
        allow_overwrite: bool = False,
    ) -> ObjectRef:
        """Store data at object_key and return an ObjectRef.

        - SHA-256 is computed from data and stored in the ref.
        - If allow_overwrite=False and the key already exists, raises ObjectExistsError.
        - Atomic where possible (write to temp + rename / S3 put is atomic).
        """
        ...

    def get(self, object_key: str) -> bytes:
        """Retrieve data by object_key.

        Raises ObjectNotFoundError if the key does not exist.
        """
        ...

    def exists(self, object_key: str) -> bool:
        """Return True if object_key exists in the backend."""
        ...

    def delete(self, object_key: str) -> None:
        """Soft-delete or remove object_key.

        Implementations should not silently swallow ObjectNotFoundError.
        """
        ...
