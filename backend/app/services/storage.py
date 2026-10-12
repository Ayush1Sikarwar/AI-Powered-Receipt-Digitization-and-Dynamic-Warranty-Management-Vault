"""Object storage provider (TSD §35, SDD Storage).

The storage layer separates:
    original/   processed/   enhanced/   thumbnails/   claims/

Storage keys NEVER embed user-controlled paths (filenames, user ids in the
path, etc.). Keys are generated from a UUID plus a safe extension, and are
scoped by owner id for isolation. Files are only reachable through
authorized application logic (or short-lived signed URLs in S3 mode).

Two backends:
- LocalFSObjectStorage: filesystem layout under STORAGE_LOCAL_ROOT (dev)
- S3ObjectStorage: S3-compatible bucket (prod)

Both expose the same interface so the backend is swappable.
"""

import logging
import os
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Top-level categories (TSD §35).
CATEGORIES = ("original", "processed", "enhanced", "thumbnails", "claims")
_SAFE_EXT = re.compile(r"^[a-z0-9]{1,8}$")


def safe_extension(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return ext if _SAFE_EXT.match(ext) else "bin"


def storage_key(category: str, owner_id: int | str, object_uuid: str, ext: str) -> str:
    """Build a storage key that never contains user-controlled path input."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown storage category: {category}")
    # owner bucketing aids isolation & lifecycle; object_uuid is the true identity.
    return f"{category}/{str(owner_id)}/{object_uuid}.{ext}"


class ObjectStorage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def presigned_url(self, key: str, ttl: int) -> str:
        """Short-lived URL for authorized download (S3). Local returns a marker."""


class LocalFSObjectStorage(ObjectStorage):
    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Reject path traversal even though keys are generated internally.
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root.resolve())):
            raise ValueError("storage key escapes root")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)  # atomic; original is never half-written

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def presigned_url(self, key: str, ttl: int) -> str:
        # Local backend has no public URL; the API streams via authorized logic.
        return f"/local/{key}"


class S3ObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        import boto3

        self._s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        self._bucket = settings.s3_bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._s3.exceptions.ClientError:
            return False

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def presigned_url(self, key: str, ttl: int) -> str:
        return self._s3.generate_presigned_url("get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=ttl)


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "s3":
            _storage = S3ObjectStorage()
        else:
            _storage = LocalFSObjectStorage(settings.storage_local_root)
    return _storage


def storage_status() -> dict:
    return {"backend": settings.storage_backend, "root": settings.storage_local_root if settings.storage_backend == "local" else settings.s3_bucket}


def delete_tree(root: Path, pattern: str) -> None:
    """Helper for tests to clear a storage subtree."""
    for p in root.glob(pattern):
        if p.is_file():
            p.unlink()
        else:
            shutil.rmtree(p, ignore_errors=True)
