"""Admin observability endpoint (PRD Phase 6: monitoring)."""

import uuid

import pytest
from sqlalchemy import select

from app.models.job import BackgroundJob, JobStatus, JobType
from app.models.user import User
from tests.conftest import _login, _register, auth_headers


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(BackgroundJob).delete()
    db_session.commit()


def _make_admin(app_client, db_session) -> dict:
    email = f"admin-{uuid.uuid4().hex[:10]}@example.com"
    _register(app_client, email)
    db_session.scalar(select(User).where(User.email == email)).role = "ADMIN"
    db_session.commit()
    return _login(app_client, email)


def test_observability_requires_admin(app_client, authed_user):
    r = app_client.get("/api/v1/admin/observability", headers=auth_headers(authed_user))
    assert r.status_code == 403


def test_observability_payload_shape(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    r = app_client.get("/api/v1/admin/observability", headers=auth_headers(admin))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["database"]["reachable"] is True
    assert d["database"]["latency_ms"] >= 0
    assert d["database"]["error"] is None
    assert isinstance(d["jobs"]["queue_depth"], int)
    assert isinstance(d["jobs"]["by_status"], dict)
    assert isinstance(d["jobs"]["by_type"], dict)
    assert isinstance(d["jobs"]["recent_failures"], list)
    assert d["storage"]["backend"] in {"local", "s3", "unknown"}
    assert d["uptime_seconds"] is None or d["uptime_seconds"] >= 0


def test_observability_reflects_queue_and_failures(db_session, app_client):
    admin = _make_admin(app_client, db_session)
    db_session.add_all(
        [
            BackgroundJob(
                type=JobType.DOCUMENT_PROCESSING,
                status=JobStatus.QUEUED,
                resource_type="document",
                resource_id=str(uuid.uuid4()),
            ),
            BackgroundJob(
                type=JobType.DOCUMENT_PROCESSING,
                status=JobStatus.FAILED,
                resource_type="document",
                resource_id=str(uuid.uuid4()),
                error_code="OCR_FAILED",
                error="simulated OCR failure",
            ),
            BackgroundJob(
                type=JobType.CLEANUP,
                status=JobStatus.COMPLETED,
                resource_type="user",
                resource_id=str(uuid.uuid4()),
            ),
        ]
    )
    db_session.commit()

    r = app_client.get("/api/v1/admin/observability", headers=auth_headers(admin))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["jobs"]["by_status"].get("QUEUED", 0) >= 1
    assert d["jobs"]["by_status"].get("FAILED", 0) >= 1
    assert d["jobs"]["by_status"].get("COMPLETED", 0) >= 1
    assert d["jobs"]["queue_depth"] >= 1  # the QUEUED job is non-terminal
    assert d["jobs"]["by_type"].get("DOCUMENT_PROCESSING", 0) >= 2
    assert any(f["error_code"] == "OCR_FAILED" and "simulated" in (f["error"] or "") for f in d["jobs"]["recent_failures"])
