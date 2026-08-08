"""Concrete storage backend implementations.

LocalFilesystemStorage — dev/test. Files stored under a configurable root.
S3CompatibleStorage    — production. Uses boto3; works with AWS S3 and MinIO.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.storage.protocol import (
    ObjectExistsError,
    ObjectNotFoundError,
    ObjectRef,
    StorageError,
)


class LocalFilesystemStorage:
    """Content-addressed local filesystem storage.

    object_key is treated as a relative path under root_path.
    Atomic writes use .tmp → os.replace().
    """

    def __init__(self, root_path: Path | str, bucket: str = "artifacts") -> None:
        self._root = Path(root_path)
        self._bucket = bucket

    @property
    def backend_name(self) -> str:
        return "local"

    @property
    def bucket(self) -> str:
        return self._bucket

    def _full_path(self, object_key: str) -> Path:
        # Prevent path traversal: resolve relative to root.
        candidate = (self._root / object_key).resolve()
        if not str(candidate).startswith(str(self._root.resolve())):
            raise StorageError(f"Path traversal detected in object_key: {object_key!r}")
        return candidate

    def put(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        *,
        allow_overwrite: bool = False,
    ) -> ObjectRef:
        path = self._full_path(object_key)
        if not allow_overwrite and path.exists():
            raise ObjectExistsError(f"Object already exists: {object_key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        sha256 = hashlib.sha256(data).hexdigest()
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return ObjectRef(
            storage_backend="local",
            bucket=self._bucket,
            object_key=object_key,
            sha256=sha256,
            byte_size=len(data),
            content_type=content_type,
        )

    def get(self, object_key: str) -> bytes:
        path = self._full_path(object_key)
        if not path.exists():
            raise ObjectNotFoundError(f"Object not found: {object_key}")
        return path.read_bytes()

    def exists(self, object_key: str) -> bool:
        return self._full_path(object_key).exists()

    def delete(self, object_key: str) -> None:
        path = self._full_path(object_key)
        if not path.exists():
            raise ObjectNotFoundError(f"Object not found: {object_key}")
        path.unlink()


class S3CompatibleStorage:
    """S3-compatible object storage backend.

    Works with AWS S3 and MinIO (when endpoint_url is set).
    No real credentials are needed in tests — inject a mock boto3 client.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str = "",
        region_name: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        _client=None,  # injectable for tests
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region_name
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = _client  # None → lazy init on first use

    @property
    def backend_name(self) -> str:
        return "s3"

    @property
    def bucket(self) -> str:
        return self._bucket

    def _get_client(self):
        if self._client is None:
            import boto3  # lazy import so tests without boto3 don't break

            kwargs: dict = {"region_name": self._region}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._access_key and self._secret_key:
                kwargs["aws_access_key_id"] = self._access_key
                kwargs["aws_secret_access_key"] = self._secret_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def put(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        *,
        allow_overwrite: bool = False,
    ) -> ObjectRef:
        client = self._get_client()
        if not allow_overwrite:
            try:
                client.head_object(Bucket=self._bucket, Key=object_key)
                raise ObjectExistsError(f"Object already exists: {object_key}")
            except ObjectExistsError:
                raise
            except Exception:
                pass  # object does not exist — proceed

        sha256 = hashlib.sha256(data).hexdigest()
        import io

        client.upload_fileobj(
            io.BytesIO(data),
            self._bucket,
            object_key,
            ExtraArgs={
                "ContentType": content_type,
                "Metadata": {"sha256": sha256},
            },
        )
        return ObjectRef(
            storage_backend="s3",
            bucket=self._bucket,
            object_key=object_key,
            sha256=sha256,
            byte_size=len(data),
            content_type=content_type,
        )

    def get(self, object_key: str) -> bytes:
        client = self._get_client()
        try:
            resp = client.get_object(Bucket=self._bucket, Key=object_key)
            data = resp["Body"].read()
        except Exception as exc:
            exc_str = str(exc)
            if "NoSuchKey" in exc_str or "404" in exc_str or "Not Found" in exc_str:
                raise ObjectNotFoundError(f"Object not found: {object_key}") from exc
            raise StorageError(f"S3 get failed for {object_key}: {exc}") from exc
        return data

    def exists(self, object_key: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except Exception:
            return False

    def delete(self, object_key: str) -> None:
        if not self.exists(object_key):
            raise ObjectNotFoundError(f"Object not found: {object_key}")
        client = self._get_client()
        client.delete_object(Bucket=self._bucket, Key=object_key)
