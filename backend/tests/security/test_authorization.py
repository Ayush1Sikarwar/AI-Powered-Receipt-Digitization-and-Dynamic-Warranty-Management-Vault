"""Cross-user isolation (SRS NFR-01): User A must never reach User B's
documents, purchases, fields, warranty records, notifications, claims,
evidence, or export. Every foreign access must be a 404 (no information
leak about existence) and must not mutate anything.
"""

import uuid

from sqlalchemy import select

from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.user import User
from tests.api.test_documents import make_png_bytes, upload
from tests.conftest import _register, auth_headers

# Real PNG bytes: evidence upload enforces content-signature validation.
EVIDENCE_BYTES = make_png_bytes(text_lines=("ISO EVIDENCE",), size=(120, 80))


def _emails():
    return (
        f"iso-a-{uuid.uuid4().hex[:10]}@example.com",
        f"iso-b-{uuid.uuid4().hex[:10]}@example.com",
    )


def test_documents_and_fields_isolated(app_client, db_session):
    a_email, b_email = _emails()
    a = _register(app_client, a_email)
    b = _register(app_client, b_email)
    doc_id = upload(app_client, b, filename="iso-receipt.png").json()["data"]["document"]["id"]
    h = auth_headers(a)

    for method, path, kwargs in [
        ("get", f"/api/v1/documents/{doc_id}", {}),
        ("get", f"/api/v1/documents/{doc_id}/status", {}),
        ("get", f"/api/v1/documents/{doc_id}/derivatives", {}),
        ("get", f"/api/v1/documents/{doc_id}/classification", {}),
        ("get", f"/api/v1/documents/{doc_id}/fields", {}),
        ("get", f"/api/v1/documents/{doc_id}/download?kind=original", {}),
        ("delete", f"/api/v1/documents/{doc_id}", {}),
        ("patch", f"/api/v1/documents/{doc_id}/fields", {"json": {"field_name": "retailer", "action": "confirm"}}),
    ]:
        resp = getattr(app_client, method)(path, headers=h, **kwargs)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"

    # A's document list must not contain B's document
    items = app_client.get("/api/v1/documents", headers=h).json()["data"]["items"]
    assert all(d["id"] != doc_id for d in items)

    # B's document is untouched by A's attempts
    assert app_client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers(b)).status_code == 200


def test_purchases_isolated(app_client):
    a_email, b_email = _emails()
    a = _register(app_client, a_email)
    b = _register(app_client, b_email)
    resp = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(b),
        json={"purchase_date": "2026-08-01", "total": 100.0, "items": [{"product_name": "B's widget", "quantity": 1}]},
    )
    assert resp.status_code == 201, resp.text
    pur_id = resp.json()["data"]["purchase"]["id"]
    h = auth_headers(a)

    for method, path, kwargs in [
        ("get", f"/api/v1/purchases/{pur_id}", {}),
        ("get", f"/api/v1/purchases/{pur_id}/warranty", {}),
        ("patch", f"/api/v1/purchases/{pur_id}", {"json": {"invoice_number": "hacked"}}),
        ("delete", f"/api/v1/purchases/{pur_id}", {}),
    ]:
        resp = getattr(app_client, method)(path, headers=h, **kwargs)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"

    items = app_client.get("/api/v1/purchases", headers=h).json()["data"]["items"]
    assert all(p["id"] != pur_id for p in items)

    # B still owns it, unmodified
    detail = app_client.get(f"/api/v1/purchases/{pur_id}", headers=auth_headers(b)).json()["data"]["purchase"]
    assert detail["invoice_number"] is None


def test_notifications_isolated(app_client, db_session):
    a_email, b_email = _emails()
    a = _register(app_client, a_email)
    b = _register(app_client, b_email)
    b_user = db_session.scalar(select(User).where(User.email == b_email))

    note = Notification(
        user_id=b_user.id,
        category=NotificationCategory.SYSTEM,
        title="B only notification",
        body="secret to B",
        status=NotificationStatus.SENT,
        notification_key=f"iso-note-{uuid.uuid4().hex}",
    )
    db_session.add(note)
    db_session.commit()
    db_session.refresh(note)

    h = auth_headers(a)
    data = app_client.get("/api/v1/notifications", headers=h).json()["data"]
    assert all(n["id"] != note.id for n in data["items"])

    resp = app_client.patch(f"/api/v1/notifications/{note.id}/read", headers=h)
    assert resp.status_code == 404

    # B sees and can read it
    b_data = app_client.get("/api/v1/notifications", headers=auth_headers(b)).json()["data"]
    assert any(n["id"] == note.id for n in b_data["items"])
    read = app_client.patch(f"/api/v1/notifications/{note.id}/read", headers=auth_headers(b))
    assert read.status_code == 200
    assert read.json()["data"]["status"] == "READ"

    # A's read-all must not touch B's future unread notifications
    note2 = Notification(
        user_id=b_user.id,
        category=NotificationCategory.SYSTEM,
        title="B unread",
        status=NotificationStatus.SENT,
        notification_key=f"iso-note2-{uuid.uuid4().hex}",
    )
    db_session.add(note2)
    db_session.commit()
    app_client.post("/api/v1/notifications/read-all", headers=h)
    fresh = db_session.get(Notification, note2.id)
    assert fresh.status == NotificationStatus.SENT


def test_claims_and_evidence_isolated(app_client, db_session):
    a_email, b_email = _emails()
    a = _register(app_client, a_email)
    b = _register(app_client, b_email)
    resp = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(b),
        json={"purchase_date": "2026-08-01", "total": 50.0, "items": [{"product_name": "B's product", "quantity": 1}]},
    )
    b_pur = resp.json()["data"]["purchase"]["id"]

    claim_resp = app_client.post(
        "/api/v1/claims",
        headers=auth_headers(b),
        json={"purchase_id": b_pur, "issue_description": "Broken on arrival, needs replacement"},
    )
    assert claim_resp.status_code == 201, claim_resp.text
    claim_id = claim_resp.json()["data"]["claim"]["id"]

    evidence_resp = app_client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        headers=auth_headers(b),
        files={"file": ("photo.png", EVIDENCE_BYTES, "image/png")},
    )
    assert evidence_resp.status_code == 201, evidence_resp.text
    file_id = evidence_resp.json()["data"]["evidence"]["id"]

    h = auth_headers(a)
    for method, path, kwargs in [
        ("get", f"/api/v1/claims/{claim_id}", {}),
        ("get", f"/api/v1/claims/{claim_id}/evidence", {}),
        ("get", f"/api/v1/claims/{claim_id}/evidence/{file_id}/download", {}),
        ("post", f"/api/v1/claims/{claim_id}/evidence", {"files": {"file": ("a.png", b"nope", "image/png")}}),
    ]:
        resp = getattr(app_client, method)(path, headers=h, **kwargs)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"

    # A cannot open a claim on B's purchase
    resp = app_client.post(
        "/api/v1/claims",
        headers=h,
        json={"purchase_id": b_pur, "issue_description": "Squat on B's purchase"},
    )
    assert resp.status_code == 404

    # A's claim list is empty; B's contains the claim
    assert app_client.get("/api/v1/claims", headers=h).json()["data"]["items"] == []
    assert len(app_client.get("/api/v1/claims", headers=auth_headers(b)).json()["data"]["items"]) == 1

    # B can still download their evidence
    dl = app_client.get(f"/api/v1/claims/{claim_id}/evidence/{file_id}/download", headers=auth_headers(b))
    assert dl.status_code == 200
    assert dl.content == EVIDENCE_BYTES


def test_export_isolated(app_client):
    a_email, b_email = _emails()
    a = _register(app_client, a_email)
    b = _register(app_client, b_email)
    upload(app_client, b, filename="secret-receipt.png")
    app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(b),
        json={"purchase_date": "2026-08-01", "total": 77.7, "items": [{"product_name": "B only product", "quantity": 1}]},
    )

    export = app_client.get("/api/v1/account/export", headers=auth_headers(a)).json()["data"]
    blob = str(export)
    assert b_email not in blob
    assert "B only product" not in blob
    assert "secret-receipt.png" not in blob
    # And A's own export does contain A's profile
    assert export["user"]["email"] == a_email


def test_products_catalog_is_not_user_writable(app_client, registered_user):
    """Products/retailers/manufacturers are a shared catalog (SDD), not
    user-owned resources: no user-facing write endpoints exist, and catalog
    reads never expose other users' private data."""
    h = auth_headers(registered_user)
    for method, path in [
        ("post", "/api/v1/products"),
        ("put", "/api/v1/products/1"),
        ("delete", "/api/v1/products/1"),
        ("post", "/api/v1/retailers"),
        ("post", "/api/v1/manufacturers"),
    ]:
        kwargs = {"headers": h}
        if method in ("post", "put"):
            kwargs["json"] = {}
        resp = getattr(app_client, method)(path, **kwargs)
        assert resp.status_code in (404, 405), f"{method} {path} -> {resp.status_code}"


def test_unauthenticated_access_denied(app_client):
    for path, method in [
        ("/api/v1/documents", "get"),
        ("/api/v1/purchases", "get"),
        ("/api/v1/claims", "get"),
        ("/api/v1/notifications", "get"),
        ("/api/v1/account/export", "get"),
    ]:
        resp = getattr(app_client, method)(path)
        assert resp.status_code == 401, f"unauthenticated {method} {path} -> {resp.status_code}"
    resp = app_client.get("/api/v1/documents", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
