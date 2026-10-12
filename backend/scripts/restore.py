"""Restore a backup set (PRD Phase 6: backup/restore).

Restores the pg_dump custom-format database dump (pg_restore --clean
--if-exists) and, if present, the object-storage tar.gz.

Safety: refuses to restore into a database that already contains data
(non-empty `users` table) unless `--force` is given.

Usage (CLI):
    python scripts/restore.py --set data/backups/backup_<ts>
                              [--db-url DSN] [--storage-target DIR] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backup import _pg_dsn  # noqa: E402


def _table_counts(db_url: str) -> dict[str, int]:
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, poolclass=None)
    try:
        with engine.connect() as conn:
            out: dict[str, int] = {}
            for table in ("users", "documents", "purchases", "products", "claims", "warranty_policies", "warranty_timelines"):
                try:
                    out[table] = int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
                except Exception:  # noqa: BLE001
                    conn.rollback()
                    out[table] = -1
            return out
    finally:
        engine.dispose()


def restore_backup(
    *,
    set_dir: str | Path,
    db_url: str,
    storage_target: str | Path | None = None,
    force: bool = False,
) -> dict:
    """Restore one backup set. Returns a result dict (aborted on refusal)."""
    set_dir = Path(set_dir)
    manifest_path = set_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.json in backup set: {set_dir}")
    manifest = json.loads(manifest_path.read_text())

    dump_path = set_dir / "database.dump"
    if not dump_path.is_file():
        raise FileNotFoundError(f"missing database.dump in {set_dir}")

    parts = _pg_dsn(db_url)
    target_db = parts["dbname"]

    # Safety: never clobber a non-empty database without explicit force.
    try:
        existing_users = _table_counts(db_url).get("users", 0)
    except Exception:  # noqa: BLE001 - brand-new empty DB: no tables yet
        existing_users = 0
    if existing_users > 0 and not force:
        return {
            "aborted": True,
            "reason": f"target database '{target_db}' already has {existing_users} users; pass --force to overwrite",
            "manifest": manifest,
        }

    pg_env = {**os.environ, "PGCONNECT_TIMEOUT": "60", **{k: v for k, v in parts.items() if k != "dbname"}}
    proc = subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--no-owner", "-d", target_db, str(dump_path)],
        env=pg_env,
        capture_output=True,
        text=True,
        check=False,
    )
    # pg_restore exits non-zero for the expected "does not exist" notices
    # during --clean on a fresh DB; verify by the schema that materialized.
    counts = _table_counts(db_url)
    if counts.get("users") == -1 or counts.get("warranty_policies") == -1:
        raise RuntimeError(f"pg_restore verification failed (missing tables): {proc.stderr.strip()[-500:]}")

    storage_restored = False
    storage_tar = set_dir / "storage.tar.gz"
    if storage_tar.is_file() and storage_target is not None:
        storage_target = Path(storage_target)
        storage_target.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["tar", "-xzf", str(storage_tar), "-C", str(storage_target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"storage restore failed: {proc.stderr.strip()[:500]}")
        storage_restored = True

    return {
        "aborted": False,
        "restored_at": datetime.now(UTC).isoformat(),
        "database": target_db,
        "storage_restored": storage_restored,
        "post_restore_counts": counts,
        "backup_manifest": manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a backup set")
    parser.add_argument("--set", required=True, help="path to the backup_<ts> set directory")
    parser.add_argument("--db-url", default=None, help="SQLAlchemy DSN of the target DB (default: $DATABASE_URL / settings)")
    parser.add_argument("--storage-target", default=None, help="object storage root to extract into (default: settings root)")
    parser.add_argument("--force", action="store_true", help="allow restoring over a non-empty database")
    args = parser.parse_args(argv)

    from app.core.config import get_settings

    settings = get_settings()
    db_url = args.db_url or os.environ.get("DATABASE_URL") or settings.database_url
    backend_dir = Path(__file__).resolve().parents[1]
    storage_target = Path(args.storage_target) if args.storage_target else (backend_dir / settings.storage_local_root)

    result = restore_backup(set_dir=args.set, db_url=db_url, storage_target=storage_target, force=args.force)
    print(json.dumps(result, indent=2))
    return 0 if not result.get("aborted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
