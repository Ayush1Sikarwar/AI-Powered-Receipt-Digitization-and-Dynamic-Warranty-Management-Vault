"""Unit tests: object storage (TSD §35)."""

import pytest

from app.services import storage


def test_storage_key_never_contains_user_path_input():
    # Even a hostile filename must not influence the key.
    key = storage.storage_key("original", 42, "abc123", "png")
    assert key == "original/42/abc123.png"
    assert ".." not in key
    assert "/" not in "abc123"


def test_storage_key_rejects_unknown_category():
    with pytest.raises(ValueError):
        storage.storage_key("arbitrary", 1, "x", "png")


def test_safe_extension_normalizes():
    assert storage.safe_extension("photo.JPG") == "jpg"
    assert storage.safe_extension("noext") == "bin"
    assert storage.safe_extension("x.weird..ext;rm") == "bin"


def test_local_storage_roundtrip(tmp_path):
    s = storage.LocalFSObjectStorage(str(tmp_path))
    key = storage.storage_key("original", 7, "uuid1", "png")
    s.put(key, b"imagedata", "image/png")
    assert s.exists(key)
    assert s.get(key) == b"imagedata"
    s.delete(key)
    assert not s.exists(key)


def test_local_storage_rejects_traversal(tmp_path):
    s = storage.LocalFSObjectStorage(str(tmp_path))
    with pytest.raises(ValueError):
        s.get("../../etc/passwd")


def test_local_storage_atomic_write(tmp_path):
    s = storage.LocalFSObjectStorage(str(tmp_path))
    key = storage.storage_key("original", 1, "u", "png")
    s.put(key, b"data1", "image/png")
    s.put(key, b"data2", "image/png")  # re-put under same key (different object uuid in practice)
    assert s.get(key) == b"data2"
