"""Shared test fixtures.

Runs against the dedicated `warranty_vault_test` database. The schema is
rebuilt from Alembic migrations for every test session (migrations are
verified as part of the test run).

Environment variables use ``setdefault`` so that CI (or any environment
that pre-sets DATABASE_URL) takes precedence over local dev defaults.
"""

import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Set defaults for local development; CI overrides via environment.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://warranty:warranty_dev_pw@127.0.0.1:5433/warranty_vault_test")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AUTH_SECRET", "test-secret-not-for-production-use-only-min-32-bytes-long!")
os.environ.setdefault("EMAIL_PROVIDER", "log")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", "/tmp/warranty-vault-test-storage")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The effective DATABASE_URL is set above (CI overrides via environment).
DATABASE_URL = os.environ["DATABASE_URL"]


def _rebuild_test_schema() -> None:
    """DROP & recreate schema, then apply all Alembic migrations."""
    from alembic.config import Config

    from alembic import command

    # Use the effective DATABASE_URL (respects CI environment).
    engine = create_engine(DATABASE_URL, poolclass=None)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    backend_dir = os.path.join(os.path.dirname(__file__), "..")
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _schema():
    _rebuild_test_schema()
    yield


@pytest.fixture(scope="session")
def app_client():
    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Reset rate-limit state before each test (deterministic suites)."""
    from app.core.rate_limit import rate_limiter

    rate_limiter.reset()
    yield


@pytest.fixture()
def db_session():
    from app.db.session import engine

    session = Session(bind=engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


def _register(client: TestClient, email: str, password: str = "Passw0rd!123", name: str = "Test User", country: str | None = "IN") -> dict:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": name,
            "country": country,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _login(client: TestClient, email: str, password: str = "Passw0rd!123") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def auth_headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture()
def registered_user(app_client):
    """A fresh registered user with tokens."""
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    data = _register(app_client, email)
    return {"client": app_client, "email": email, **data}


@pytest.fixture()
def authed_user(registered_user):
    return {
        "client": registered_user["client"],
        "email": registered_user["email"],
        "access_token": registered_user["access_token"],
        "refresh_token": registered_user["refresh_token"],
        "user": registered_user["user"],
    }
