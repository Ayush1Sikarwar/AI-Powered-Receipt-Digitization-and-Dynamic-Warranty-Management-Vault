"""Backup & restore round-trip (PRD Phase 6: backup/restore).

Backs up the live *test* database, restores it into a scratch database,
and verifies table counts + storage objects match the backup manifest.
"""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.models.user import UserStatus
from scripts.backup import create_backup, prune_backup_sets
from scripts.restore import restore_backup

SCRATCH_DB = "warranty_vault_restore_test"


@pytest.fixture()
def scratch_db():
    """A throwaway database for restore verification."""
    settings = get_settings()
    url = settings.database_url
    # Derive the scratch DSN by swapping the database name.
    prefix, db = url.rsplit("/", 1)
    scratch_url = f"{prefix}/{SCRATCH_DB}"
    admin = create_engine(url, poolclass=None, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    admin.dispose()
    yield scratch_url
    admin = create_engine(url, poolclass=None, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
    admin.dispose()


@pytest.fixture()
def sample_storage(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    (root / "original").mkdir(parents=True)
    (root / "thumbnails").mkdir()
    (root / "original" / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "original" / "b.png").write_bytes(b"\x89PNG fake")
    (root / "thumbnails" / "a.jpg").write_bytes(b"\xff\xd8\xff fake")
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_creates_complete_set(sample_storage, tmp_path):
    db_url = get_settings().database_url
    out = tmp_path / "backups"
    manifest = create_backup(db_url=db_url, storage_dir=sample_storage, out_dir=out)

    set_dir = out / manifest["set"]
    assert (set_dir / "database.dump").stat().st_size > 0
    assert (set_dir / "storage.tar.gz").is_file()
    m = json.loads((set_dir / "manifest.json").read_text())
    # Manifest hashes must match the actual files.
    assert m["files"]["database.dump"]["sha256"] == _sha256(set_dir / "database.dump")
    assert m["files"]["storage.tar.gz"]["sha256"] == _sha256(set_dir / "storage.tar.gz")
    assert m["storage"]["file_count"] == 3
    assert m["table_counts"]["users"] >= 0
    assert m["database"] == db_url.rsplit("/", 1)[-1]


def test_restore_roundtrip_matches_backup(sample_storage, tmp_path, scratch_db):
    settings = get_settings()
    db_url = settings.database_url
    out = tmp_path / "backups"
    manifest = create_backup(db_url=db_url, storage_dir=sample_storage, out_dir=out)
    set_dir = out / manifest["set"]

    result = restore_backup(set_dir=set_dir, db_url=scratch_db, storage_target=tmp_path / "restored_storage")
    assert result["aborted"] is False
    assert result["storage_restored"] is True

    # Every count verified by the restore must match the backup manifest.
    for table, count in result["post_restore_counts"].items():
        if count >= 0:
            assert manifest["table_counts"].get(table) == count, f"count mismatch on {table}"

    # Storage objects restored.
    files = [p for p in (tmp_path / "restored_storage").rglob("*") if p.is_file()]
    assert len(files) == 3
    assert (tmp_path / "restored_storage" / "original" / "a.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_restore_refuses_non_empty_database_without_force(db_session, sample_storage, tmp_path, scratch_db):
    # Ensure the source DB actually has data (test DB may be schema-only).
    import uuid as _uuid

    from app.models.user import User

    user = User(
        email=f"backup-{_uuid.uuid4().hex[:10]}@example.com",
        password_hash="argon2id-dummy",
        display_name="Backup Fixture",
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    db_session.commit()
    try:
        db_url = get_settings().database_url
        out = tmp_path / "backups"
        manifest = create_backup(db_url=db_url, storage_dir=sample_storage, out_dir=out)
        set_dir = out / manifest["set"]
        assert manifest["table_counts"]["users"] >= 1

        # First restore into the empty scratch DB.
        first = restore_backup(set_dir=set_dir, db_url=scratch_db)
        assert first["aborted"] is False
        assert first["post_restore_counts"]["users"] >= 1

        # Second restore (DB now has data) must abort without --force.
        second = restore_backup(set_dir=set_dir, db_url=scratch_db)
        assert second["aborted"] is True
        assert "--force" in second["reason"]

        # ...and proceed with force.
        third = restore_backup(set_dir=set_dir, db_url=scratch_db, force=True)
        assert third["aborted"] is False
    finally:
        db_session.delete(user)
        db_session.commit()


def test_prune_keeps_newest_sets(tmp_path):
    out = tmp_path / "backups"
    for n in ("1", "2", "3", "4", "5"):
        (out / f"backup_2026010{n}_000000").mkdir(parents=True)
    # Non-backup dirs must be ignored.
    (out / "not_a_backup").mkdir()

    removed = prune_backup_sets(out, keep=3)
    # 5 valid sets, keep 3 newest -> the 2 oldest are pruned.
    assert removed == ["backup_20260101_000000", "backup_20260102_000000"]
    remaining = sorted(p.name for p in out.iterdir())
    assert remaining == [
        "backup_20260103_000000",
        "backup_20260104_000000",
        "backup_20260105_000000",
        "not_a_backup",
    ]
