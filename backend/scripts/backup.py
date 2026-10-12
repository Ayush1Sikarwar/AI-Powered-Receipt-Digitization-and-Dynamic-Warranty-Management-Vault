"""Database + object-storage backup (PRD Phase 6: backup/restore).

Creates a timestamped backup *set* under an output directory:

    backup_<ts>/
        database.dump      pg_dump custom-format (-Fc) dump of the database
        storage.tar.gz     tar.gz of the object-storage tree
        manifest.json      db name, per-table counts, storage stats, sha256

Usage (CLI):
    python scripts/backup.py [--db-url DSN] [--storage DIR] [--out DIR]
                             [--keep N]

    --keep N prunes the oldest backup sets beyond N (0 = never prune).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKUP_SET_RE = re.compile(r"^backup_\d{8}_\d{6}(_\w+)?$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pg_dsn(database_url: str) -> dict:
    """Parse a SQLAlchemy DSN into pg_* connection parts (env-safe: no
    password on the command line)."""
    from urllib.parse import urlparse

    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    p = urlparse(dsn)
    if not p.hostname or not p.path:
        raise ValueError(f"unparseable DSN: {database_url}")
    return {
        "PGHOST": p.hostname,
        "PGPORT": str(p.port or 5432),
        "PGUSER": p.username or "postgres",
        "PGPASSWORD": p.password or "",
        "dbname": p.path.lstrip("/"),
    }


def _table_counts(database_url: str) -> dict[str, int]:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, poolclass=None)
    try:
        with engine.connect() as conn:
            out: dict[str, int] = {}
            for table in (
                "users",
                "documents",
                "extracted_fields",
                "purchases",
                "purchase_items",
                "products",
                "claims",
                "warranty_policies",
                "warranty_timelines",
                "background_jobs",
            ):
                try:
                    out[table] = int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
                except Exception:  # noqa: BLE001 - optional table on older schemas
                    conn.rollback()  # avoid a poisoned transaction for later tables
                    out[table] = -1
            return out
    finally:
        engine.dispose()


def _storage_stats(storage_dir: Path) -> dict:
    files = [p for p in storage_dir.rglob("*") if p.is_file()] if storage_dir.is_dir() else []
    return {
        "root": str(storage_dir),
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
    }


def create_backup(
    *,
    db_url: str,
    storage_dir: str | Path,
    out_dir: str | Path,
    name: str | None = None,
    keep: int = 0,
) -> dict:
    """Create one backup set. Returns the manifest dict."""
    storage_dir = Path(storage_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    set_name = f"backup_{stamp}{('_' + name) if name else ''}"
    set_dir = out_dir / set_name
    set_dir.mkdir(parents=False, exist_ok=False)

    dump_path = set_dir / "database.dump"
    parts = _pg_dsn(db_url)
    db_name = parts["dbname"]
    pg_env = {**os.environ, "PGCONNECT_TIMEOUT": "30", **{k: v for k, v in parts.items() if k != "dbname"}}
    proc = subprocess.run(
        ["pg_dump", "-Fc", "--no-owner", "-d", db_name, "-f", str(dump_path)],
        env=pg_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not dump_path.is_file() or dump_path.stat().st_size == 0:
        raise RuntimeError(f"pg_dump failed: {proc.stderr.strip()[:500]}")

    storage_tar: Path | None = None
    if storage_dir.is_dir():
        storage_tar = set_dir / "storage.tar.gz"
        proc = subprocess.run(
            ["tar", "-czf", str(storage_tar), "-C", str(storage_dir), "."],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"storage tar failed: {proc.stderr.strip()[:500]}")

    manifest = {
        "set": set_name,
        "created_at": datetime.now(UTC).isoformat(),
        "database": db_name,
        "table_counts": _table_counts(db_url),
        "storage": _storage_stats(storage_dir),
        "files": {
            "database.dump": {"bytes": dump_path.stat().st_size, "sha256": _sha256(dump_path)},
            **({"storage.tar.gz": {"bytes": storage_tar.stat().st_size, "sha256": _sha256(storage_tar)}} if storage_tar else {}),
        },
    }
    (set_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    pruned = prune_backup_sets(out_dir, keep) if keep > 0 else []
    manifest["pruned"] = pruned
    return manifest


def prune_backup_sets(out_dir: str | Path, keep: int) -> list[str]:
    """Delete the oldest backup sets beyond `keep` (newest kept)."""
    out_dir = Path(out_dir)
    sets = sorted(p.name for p in out_dir.glob("backup_*") if p.is_dir() and BACKUP_SET_RE.match(p.name))
    if len(sets) <= keep:
        return []
    removed = []
    for old in sets[: len(sets) - keep]:
        shutil.rmtree(out_dir / old, ignore_errors=True)
        removed.append(old)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a database + storage backup set")
    parser.add_argument("--db-url", default=None, help="SQLAlchemy DSN (default: $DATABASE_URL / settings)")
    parser.add_argument("--storage", default=None, help="object storage root (default: settings.storage_local_root)")
    parser.add_argument("--out", default=None, help="backup output dir (default: <backend>/data/backups)")
    parser.add_argument("--name", default=None, help="optional suffix for the set name")
    parser.add_argument("--keep", type=int, default=0, help="retain only the N newest sets (0 = keep all)")
    args = parser.parse_args(argv)

    from app.core.config import get_settings

    settings = get_settings()
    db_url = args.db_url or os.environ.get("DATABASE_URL") or settings.database_url
    backend_dir = Path(__file__).resolve().parents[1]
    storage = Path(args.storage) if args.storage else (backend_dir / settings.storage_local_root)
    out = Path(args.out) if args.out else (backend_dir / "data" / "backups")

    manifest = create_backup(db_url=db_url, storage_dir=storage, out_dir=out, name=args.name, keep=args.keep)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
