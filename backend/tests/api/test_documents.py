"""API tests: document upload, status, derivatives, download, delete (TSD §18)."""

import io
import uuid

from PIL import Image

from tests.conftest import auth_headers


def make_png_bytes(text_lines=("TEST RETAIL STORE", "Date: 2026-08-01", "Total: $99.99"), size=(640, 800)):
    img = Image.new("RGB", size, "white")
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i, line in enumerate(text_lines):
        d.text((60, 80 + i * 40), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload(client, tokens, filename="receipt.png", data=None, mime="image/png"):
    if data is None:
        data = make_png_bytes()
    return client.post(
        "/api/v1/documents",
        headers=auth_headers(tokens),
        files={"file": (filename, data, mime)},
    )


def test_upload_returns_queued_and_enqueues_job(app_client, registered_user, db_session):
    resp = upload(app_client, registered_user)
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    assert doc["status"] in ("QUEUED", "PROCESSING", "COMPLETED")
    assert doc["sha256"]
    assert doc["original_filename"] == "receipt.png"

    # A DOCUMENT_PROCESSING job must have been enqueued (async pipeline).
    from app.models.job import BackgroundJob, JobStatus, JobType

    jobs = db_session.query(BackgroundJob).filter_by(resource_type="document", resource_id=str(doc["id"])).all()
    assert jobs, "no processing job enqueued"
    assert all(j.type == JobType.DOCUMENT_PROCESSING for j in jobs)
    assert all(j.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED) for j in jobs)


def test_upload_requires_auth(app_client):
    resp = app_client.post("/api/v1/documents", files={"file": ("r.png", make_png_bytes(), "image/png")})
    assert resp.status_code == 401


def test_full_processing_flow(app_client, registered_user):
    """Upload -> run the pipeline in-process -> status COMPLETED + thumbnail."""
    from app.ai.pipeline import run_document_pipeline
    from app.db.session import SessionLocal
    from app.models.document import Document

    resp = upload(app_client, registered_user)
    doc_id = resp.json()["data"]["document"]["id"]

    db = SessionLocal()
    try:
        document = db.get(Document, doc_id)
        run_document_pipeline(db, document)
        document = db.get(Document, doc_id)
        assert document.status.value == "COMPLETED"
        assert document.page_count == 1
    finally:
        db.close()

    # Status endpoint reflects completion.
    status = app_client.get(f"/api/v1/documents/{doc_id}/status", headers=auth_headers(registered_user))
    assert status.status_code == 200
    body = status.json()["data"]
    assert body["status"] == "COMPLETED"
    assert body["pipeline_version"]

    # Derivatives: at least a thumbnail.
    der = app_client.get(f"/api/v1/documents/{doc_id}/derivatives", headers=auth_headers(registered_user))
    assert der.status_code == 200
    types = [d["derivative_type"] for d in der.json()["data"]["items"]]
    assert "THUMBNAIL" in types

    # Document detail includes page geometry + quality.
    detail = app_client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers(registered_user))
    pages = detail.json()["data"]["pages"]
    assert len(pages) == 1
    assert pages[0]["width"] == 640
    assert 0.0 <= pages[0]["quality_score"] <= 1.0

    # Authorized download of the original.
    dl = app_client.get(f"/api/v1/documents/{doc_id}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_download_unauthorized(app_client, registered_user):
    resp = upload(app_client, registered_user)
    doc_id = resp.json()["data"]["document"]["id"]
    # No auth.
    assert app_client.get(f"/api/v1/documents/{doc_id}/download").status_code == 401
    # Another user.
    other = {"client": app_client, "email": f"x-{uuid.uuid4().hex[:6]}@example.com"}
    from tests.conftest import _register

    other_data = _register(app_client, other["email"])
    assert app_client.get(f"/api/v1/documents/{doc_id}/download", headers=auth_headers(other_data)).status_code == 404


def test_delete_document(app_client, registered_user):
    resp = upload(app_client, registered_user)
    doc_id = resp.json()["data"]["document"]["id"]
    deleted = app_client.delete(f"/api/v1/documents/{doc_id}", headers=auth_headers(registered_user))
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert app_client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers(registered_user)).status_code == 404


def test_delete_requires_hold_when_open_claim(app_client, registered_user, db_session):
    """TSD §37: a non-terminal claim referencing the document imposes a hold."""
    from app.models.claim import Claim, ClaimStatus
    from app.models.purchase import Purchase, PurchaseVerification

    resp = upload(app_client, registered_user)
    doc = resp.json()["data"]["document"]

    db = db_session
    purchase = Purchase(
        user_id=registered_user["user"]["id"],
        document_id=doc["id"],
        purchase_date="2026-08-01",
        verification_status=PurchaseVerification.USER_CONFIRMED,
    )
    db.add(purchase)
    db.commit()
    claim = Claim(
        user_id=registered_user["user"]["id"],
        purchase_id=purchase.id,
        status=ClaimStatus.READY,  # non-terminal
        issue_description="test",
    )
    db.add(claim)
    db.commit()

    resp2 = app_client.delete(f"/api/v1/documents/{doc['id']}", headers=auth_headers(registered_user))
    assert resp2.status_code == 200
    body = resp2.json()["data"]
    assert body["deleted"] is False
    assert body["status"] == "HOLD"
    assert body["hold"]["reason"] == "document_referenced_by_open_claim"
    assert claim.id in body["hold"]["claim_ids"]


def test_document_list_and_filter(app_client, registered_user):
    upload(app_client, registered_user, filename="alpha.png")
    upload(app_client, registered_user, filename="beta.png")
    listing = app_client.get("/api/v1/documents", headers=auth_headers(registered_user))
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert data["total"] >= 2
    assert data["page"] == 1
    # Filter by filename text.
    filtered = app_client.get("/api/v1/documents?q=alpha", headers=auth_headers(registered_user))
    names = [d["original_filename"] for d in filtered.json()["data"]["items"]]
    assert any("alpha" in n for n in names)
