"""Vault infrastructure-failure tests: storage outage and database outage
must produce safe error envelopes with no partial state — never a half-
written document or a leaked stack trace."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.job import BackgroundJob
from tests.api.test_documents import make_png_bytes, upload
from tests.conftest import auth_headers


def _raw_client():
    """Client that returns the app's 500 envelope instead of re-raising."""
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


class BrokenStorage:
    """Object storage whose put() always fails (simulates an S3/local FS outage)."""

    def put(self, key, data, content_type):
        raise OSError("simulated storage outage")

    def get(self, key):
        raise OSError("simulated storage outage")

    def exists(self, key):
        return False

    def delete(self, key):
        raise OSError("simulated storage outage")

    def presigned_url(self, key, ttl):
        raise OSError("simulated storage outage")


def test_storage_failure_safe_error_no_partial_state(app_client, registered_user, db_session, monkeypatch):
    import app.services.storage as storage_module

    jobs_before = db_session.scalar(select(func.count()).select_from(BackgroundJob)) or 0
    monkeypatch.setattr(storage_module, "_storage", BrokenStorage())

    with _raw_client() as client:
        resp = client.post(
            "/api/v1/documents",
            headers=auth_headers(registered_user),
            files={"file": ("will-fail.png", make_png_bytes(), "image/png")},
        )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["request_id"]
    # Safe error: no stack trace / exception detail leaks to the client.
    assert "Traceback" not in resp.text
    assert "OSError" not in resp.text

    # No partial state: no document row, and no NEW processing job.
    assert db_session.scalar(select(Document).where(Document.user_id == registered_user["user"]["id"])) is None
    assert db_session.scalar(select(func.count()).select_from(BackgroundJob)) == jobs_before


def test_database_failure_safe_error_no_partial_state(app_client, registered_user, db_session, monkeypatch):
    def boom(self):
        raise OSError("simulated database outage")

    monkeypatch.setattr(Session, "commit", boom)

    with _raw_client() as client:
        resp = client.post(
            "/api/v1/documents",
            headers=auth_headers(registered_user),
            files={"file": ("db-fail.png", make_png_bytes(), "image/png")},
        )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["request_id"]
    assert "Traceback" not in resp.text

    # The commit that would have persisted the document failed: no row exists.
    assert db_session.scalar(select(Document).where(Document.user_id == registered_user["user"]["id"])) is None


def test_healthy_upload_still_works_after_failures(app_client, registered_user):
    """Sanity: the failures above are isolated — a subsequent upload
    (with healthy infra, after monkeypatch teardown) succeeds end-to-end."""
    resp = upload(app_client, registered_user, filename="recovery.png", data=make_png_bytes())
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    dl = app_client.get(f"/api/v1/documents/{doc['id']}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content.startswith(b"\x89PNG")
