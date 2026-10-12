"""Claim + evidence API tests (SRS FR-16)."""

import hashlib

# Real PNG bytes: evidence upload enforces content-signature validation, so
# fake magic bytes would (correctly) be rejected.
import time
import uuid

import pytest
from sqlalchemy import select

from app.core.audit import AuditAction
from app.models.audit import AuditLog
from app.models.claim import ClaimFile
from app.services import storage as storage_service
from tests.api.test_documents import make_png_bytes
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    from app.models.claim import Claim, ClaimFile
    from app.models.notification import Notification
    from app.models.product import Manufacturer, Product
    from app.models.purchase import Purchase, PurchaseItem
    from app.models.warranty import (
        WarrantyCoverage,
        WarrantyMapping,
        WarrantyPolicy,
        WarrantySource,
        WarrantyTimeline,
    )

    db_session.query(ClaimFile).delete()
    db_session.query(Claim).delete()
    db_session.query(Notification).delete()
    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(PurchaseItem).delete()
    db_session.query(Purchase).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _purchase(app_client, tokens, total=10.0):
    resp = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(tokens),
        json={"purchase_date": "2026-08-01", "total": total, "items": [{"product_name": "Claimable", "quantity": 1}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["purchase"]["id"]


def test_claim_lifecycle(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    pur = _purchase(app_client, registered_user)

    resp = app_client.post(
        "/api/v1/claims",
        headers=h,
        json={"purchase_id": pur, "issue_description": "Unit stopped working", "issue_category": "DOA"},
    )
    assert resp.status_code == 201, resp.text
    claim = resp.json()["data"]["claim"]
    assert claim["status"] == "DRAFT"
    assert claim["purchase_id"] == pur

    # Audit row recorded (FR-18)
    assert (
        db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.CLAIM_CREATED, AuditLog.resource_id == str(claim["id"])
            )
        )
        is not None
    )

    # List + status filter
    listing = app_client.get("/api/v1/claims", headers=h).json()["data"]
    assert listing["total"] == 1
    assert app_client.get("/api/v1/claims", headers=h, params={"status": "READY"}).json()["data"]["items"] == []
    assert app_client.get("/api/v1/claims", headers=h, params={"status": "DRAFT"}).json()["data"]["total"] == 1

    # Detail with (empty) evidence
    detail = app_client.get(f"/api/v1/claims/{claim['id']}", headers=h).json()["data"]["claim"]
    assert detail["issue_description"] == "Unit stopped working"
    assert detail["evidence"] == []


def test_claim_requires_valid_purchase_and_description(app_client, registered_user):
    h = auth_headers(registered_user)
    resp = app_client.post("/api/v1/claims", headers=h, json={"purchase_id": 999999, "issue_description": "no such purchase"})
    assert resp.status_code == 404
    pur = _purchase(app_client, registered_user)
    resp = app_client.post("/api/v1/claims", headers=h, json={"purchase_id": pur, "issue_description": "x"})
    assert resp.status_code == 422  # min_length=3
    resp = app_client.post("/api/v1/claims", headers=h, json={"purchase_id": pur})
    assert resp.status_code == 422  # missing description


EVIDENCE_BYTES = make_png_bytes(text_lines=("EVIDENCE PHOTO",), size=(160, 120))


def test_evidence_upload_list_download(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    pur = _purchase(app_client, registered_user)
    claim_id = app_client.post(
        "/api/v1/claims", headers=h, json={"purchase_id": pur, "issue_description": "Evidence test"}
    ).json()["data"]["claim"]["id"]

    resp = app_client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        headers=h,
        files={"file": ("broken-part.png", EVIDENCE_BYTES, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    ev = resp.json()["data"]["evidence"]
    assert ev["original_filename"] == "broken-part.png"
    assert ev["file_size"] == len(EVIDENCE_BYTES)
    assert ev["sha256"] == hashlib.sha256(EVIDENCE_BYTES).hexdigest()

    # Stored in the object store under the claims/ category (TSD §35)
    row = db_session.get(ClaimFile, ev["id"])
    assert row.storage_key.startswith("claims/")
    assert storage_service.get_storage().get(row.storage_key) == EVIDENCE_BYTES

    listing = app_client.get(f"/api/v1/claims/{claim_id}/evidence", headers=h).json()["data"]["items"]
    assert len(listing) == 1 and listing[0]["id"] == ev["id"]

    dl = app_client.get(f"/api/v1/claims/{claim_id}/evidence/{ev['id']}/download", headers=h)
    assert dl.status_code == 200
    assert dl.content == EVIDENCE_BYTES
    assert dl.headers["content-type"] == "image/png"
    assert dl.headers["x-content-sha256"] == ev["sha256"]

    # Audit recorded
    assert (
        db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.EVIDENCE_UPLOADED, AuditLog.resource_id == str(ev["id"])
            )
        )
        is not None
    )


def test_evidence_rejects_disallowed_and_oversized(app_client, registered_user):
    h = auth_headers(registered_user)
    pur = _purchase(app_client, registered_user)
    claim_id = app_client.post(
        "/api/v1/claims", headers=h, json={"purchase_id": pur, "issue_description": "Bad uploads"}
    ).json()["data"]["claim"]["id"]

    resp = app_client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        headers=h,
        files={"file": ("malware.exe", b"MZ...", "application/x-msdownload")},
    )
    assert resp.status_code == 400

    big = b"0" * (16 * 1024 * 1024)
    resp = app_client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        headers=h,
        files={"file": (f"big-{uuid.uuid4().hex}.png", big, "image/png")},
    )
    assert resp.status_code == 400


def test_evidence_download_requires_claim_ownership(app_client, registered_user, db_session):
    from tests.conftest import _register

    h = auth_headers(registered_user)
    pur = _purchase(app_client, registered_user)
    claim_id = app_client.post(
        "/api/v1/claims", headers=h, json={"purchase_id": pur, "issue_description": "Ownership test"}
    ).json()["data"]["claim"]["id"]
    ev_id = app_client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        headers=h,
        files={"file": ("mine.png", EVIDENCE_BYTES, "image/png")},
    ).json()["data"]["evidence"]["id"]

    other = _register(app_client, f"other-{uuid.uuid4().hex[:8]}@example.com")
    # File exists but belongs to someone else's claim
    resp = app_client.get(
        f"/api/v1/claims/{claim_id}/evidence/{ev_id}/download", headers=auth_headers(other)
    )
    assert resp.status_code == 404
    # Even with a valid own claim, a foreign file id 404s
    other_pur = _purchase(app_client, other)
    other_claim = app_client.post(
        "/api/v1/claims", headers=auth_headers(other), json={"purchase_id": other_pur, "issue_description": "My claim"}
    ).json()["data"]["claim"]["id"]
    resp = app_client.get(
        f"/api/v1/claims/{other_claim}/evidence/{ev_id}/download", headers=auth_headers(other)
    )
    assert resp.status_code == 404


# --- FR-16: pre-filled claim package --------------------------------------


def _warranty_purchase(app_client, tokens, db_session, purchase_date="2026-08-30"):
    """A purchase whose item maps to a seeded warranty policy + timeline."""
    from tests.api.test_warranty import _create_purchase, _seed

    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        tokens["access_token"],
        {
            "purchase_date": purchase_date,
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    return p, s


def test_package_before_generation_is_409(app_client, registered_user):
    h = auth_headers(registered_user)
    pur = _purchase(app_client, registered_user)
    claim = app_client.post(
        "/api/v1/claims", headers=h, json={"purchase_id": pur, "issue_description": "No package yet"}
    ).json()["data"]["claim"]
    assert app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).status_code == 409
    assert app_client.get(f"/api/v1/claims/{claim['id']}/package/download", headers=h).status_code == 409


def test_claim_package_prefilled_warranty_evidence(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)

    claim = app_client.post(
        "/api/v1/claims",
        headers=h,
        json={
            "purchase_id": p["id"],
            "issue_description": "Compressor noise, unit not cooling",
            "issue_category": "DEFECT",
            "contact_name": "Claim Tester",
            "contact_email": "claim@example.com",
        },
    ).json()["data"]["claim"]
    assert claim["status"] == "DRAFT"

    # one evidence file
    ev = app_client.post(
        f"/api/v1/claims/{claim['id']}/evidence",
        headers=h,
        files={"file": ("noise.png", EVIDENCE_BYTES, "image/png")},
    ).json()["data"]["evidence"]

    # generate
    resp = app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)
    assert resp.status_code == 200, resp.text
    out = resp.json()["data"]["claim"]
    assert out["status"] == "READY"
    assert out["package_generated_at"] is not None

    pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]
    # purchase + item rows pre-filled
    assert pkg["purchase"]["purchase_date"] == "2026-08-30"
    assert pkg["purchase"]["warranty_region"] == "IN"
    assert pkg["items"][0]["product_name"] == "SPLIT AC 1.5T"
    # warranty policy + coverages + computed timelines + source provenance
    w = pkg["warranty"][0]
    assert w["policy"]["policy_name"].startswith("QTech Sample AC Warranty")
    assert w["policy"]["source"]["source_title"].startswith("QTech warranty terms")
    assert w["policy"]["source"]["trust_level"] == 2
    comps = {t["component"]: t for t in w["timelines"]}
    assert set(comps) == {"FULL_UNIT", "COMPRESSOR"}
    assert comps["FULL_UNIT"]["expiry_date"] == "2027-08-30"
    assert comps["COMPRESSOR"]["expiry_date"] == "2031-08-30"
    assert w["match_method"] == "CATALOG_MATCH"
    # evidence pre-filled with hash
    assert len(pkg["evidence"]) == 1
    assert pkg["evidence"][0]["original_filename"] == ev["original_filename"]
    assert pkg["evidence"][0]["sha256"] == ev["sha256"]
    assert pkg["issue_category"] == "DEFECT"

    # audit trail
    assert (
        db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.CLAIM_PACKAGE_GENERATED,
                AuditLog.resource_id == str(claim["id"]),
            )
        )
        is not None
    )

    # CLAIM_READY notification, idempotent on regeneration
    from app.models.notification import Notification, NotificationCategory, NotificationStatus

    n = db_session.scalar(select(Notification).where(Notification.category == NotificationCategory.CLAIM_READY))
    assert n is not None
    assert n.claim_id == claim["id"]
    assert n.status == NotificationStatus.SCHEDULED
    first_generated_at = out["package_generated_at"]

    # regenerate: new timestamp, still exactly one CLAIM_READY notification
    time.sleep(1.1)
    resp2 = app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)
    assert resp2.status_code == 200
    assert resp2.json()["data"]["claim"]["package_generated_at"] > first_generated_at
    db_session.expire_all()
    n_list = db_session.scalars(select(Notification).where(Notification.category == NotificationCategory.CLAIM_READY)).all()
    assert len(n_list) == 1

    # download
    dl = app_client.get(f"/api/v1/claims/{claim['id']}/package/download", headers=h)
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/json")
    body = dl.json()
    assert body["claim_id"] == claim["id"]
    assert body["claim_uuid"] == claim["uuid"]


def test_claim_package_foreign_purchase_invisible(app_client, registered_user, db_session):
    """A claim on someone else's purchase is invisible (404) end-to-end."""
    from tests.conftest import _register

    h = auth_headers(registered_user)
    pur = _purchase(app_client, registered_user)
    claim = app_client.post(
        "/api/v1/claims", headers=h, json={"purchase_id": pur, "issue_description": "Cross-user test"}
    ).json()["data"]["claim"]

    other = _register(app_client, f"xuser-{uuid.uuid4().hex[:8]}@example.com")
    for path in ("", "/package", "/package/download"):
        r = app_client.get(f"/api/v1/claims/{claim['id']}{path}", headers=auth_headers(other))
        assert r.status_code == 404
    assert app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=auth_headers(other)).status_code == 404
    assert claim["id"] > 0
