"""Shared harness: a dedicated throwaway Postgres database for AI evaluation.

Evaluations must not pollute the dev database, so each eval run:
1. creates `warranty_vault_eval` (if absent) / drops + recreates it,
2. applies the Alembic migrations to it,
3. points the app at it via DATABASE_URL (set BEFORE app import),
4. yields a SQLAlchemy Session.

Usage (in an eval script, before any `app.*` import):
    os.environ["DATABASE_URL"] = evaldb.ensure_database()
"""

import os
import sys

EVAL_DB = "warranty_vault_eval"
PG = "postgresql+psycopg://warranty:warranty_dev_pw@127.0.0.1:5432"
# Plain libpq URL for raw psycopg admin connections (no SQLAlchemy driver tag).
PG_RAW = PG.replace("postgresql+psycopg", "postgresql")


def _psql(sql: str) -> None:
    import psycopg

    with psycopg.connect(f"{PG_RAW}/postgres", autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)


def ensure_database() -> str:
    """Create/recreate the eval database + schema; returns DATABASE_URL."""
    _psql(f"DROP DATABASE IF EXISTS {EVAL_DB} WITH (FORCE);")
    _psql(f"CREATE DATABASE {EVAL_DB};")
    url = f"{PG}/{EVAL_DB}"
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("EMAIL_PROVIDER", "log")
    os.environ.setdefault("STORAGE_BACKEND", "local")
    os.environ.setdefault("STORAGE_LOCAL_ROOT", "/tmp/warranty-vault-eval-storage")

    backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, backend_dir)

    from alembic.config import Config

    from alembic import command

    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    command.upgrade(cfg, "head")
    return url


def session_factory():
    """Import app session machinery AFTER DATABASE_URL is set."""
    from app.db.session import SessionLocal

    return SessionLocal
