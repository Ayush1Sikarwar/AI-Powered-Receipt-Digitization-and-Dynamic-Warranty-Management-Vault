"""MVP claims workflow tests (SRS FR-16, TSD §23/§33/§57).

Covers: package provenance states (verified / AI extracted / user confirmed /
external source / inferred / computed), stored-document evidence selection
(receipt + warranty card), the claim-ready report, the portable evidence
bundle (ZIP), claim edits (no status field), the CLAIM_READY MVP boundary,
and the portable account export bundle.
"""

import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from app.core.audit import AuditAction
from app.models.audit import AuditLog
from app.models.claim import Claim, ClaimDocument, ClaimFile
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.extraction import ExtractedField, VerificationStatus
from app.models.notification import Notification
from app.models.purchase import Purchase, PurchaseItem, PurchaseVerification
from app.models.warranty import (
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantySource,
    WarrantyTimeline,
)
from app.services import storage as storage_service
from tests.api.test_documents import make_png_bytes
from tests.conftest import _register, auth_headers

RECEIPT_BYTES = make_png_bytes(text_lines=("RECEIPT", "QTech AC 1.5T", "TOTAL 2899"), size=(200, 140))
WARRANTY_CARD_BYTES = make_png_bytes(text_lines=("WARRANTY CARD", "LIMITED WARRANTY"), size=(200, 100))
PHOTO_BYTES = make_png_bytes(text_lines=("BROKEN PART",), size=(160, 120))


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    from app.models.product import Manufacturer, Product

    db_session.query(ClaimFile).delete()
    db_session.query(ClaimDocument).delete()
    db_session.query(Claim).delete()
    db_session.query(Notification).delete()
    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(PurchaseItem).delete()
    db_session.query(Purchase).delete()
    db_session.query(ExtractedField).delete()
    db_session.query(Document).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _stored_document(db_session, user_id: int, *, doc_type: DocumentType, filename: str, data: bytes) -> Document:
    """A real stored document: bytes in storage + a metadata row."""
    key = storage_service.storage_key("original", user_id, f"test-{hashlib.sha256(data).hexdigest()[:12]}", "png")
    storage_service.get_storage().put(key, data, "image/png")
    doc = Document(
        user_id=user_id,
        document_type=doc_type,
        original_filename=filename,
        mime_type="image/png",
        file_size=len(data),
        storage_key=key,
        sha256_hash=hashlib.sha256(data).hexdigest(),
        page_count=1,
        status=DocumentStatus.COMPLETED,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def _field(db_session, doc_id: int, name: str, value: str, status: VerificationStatus) -> ExtractedField:
    f = ExtractedField(
        document_id=doc_id,
        field_name=name,
        field_value=value,
        original_value=value,
        confidence=0.9,
        extraction_method="rule",
        model_name="fixture",
        model_version="1",
        verification_status=status,
    )
    db_session.add(f)
    db_session.flush()
    return f


def _warranty_purchase(app_client, tokens, db_session, purchase_date="2026-08-30"):
    from tests.api.test_warranty import _create_purchase, _seed

    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        tokens["access_token"],
        {
            "purchase_date": purchase_date,
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T", "serial_number": "SN12345"}],
        },
    )
    return p, s


def _make_claim(app_client, h, purchase_id: int) -> dict:
    return app_client.post(
        "/api/v1/claims",
        headers=h,
        json={
            "purchase_id": purchase_id,
            "issue_description": "Compressor noise, unit not cooling",
            "issue_category": "DEFECT",
            "contact_name": "Claim Tester",
            "contact_email": "claim@example.com",
        },
    ).json()["data"]["claim"]


# --- Provenance: every package section carries a trust state ---------------


def test_package_provenance_states(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])

    # a stored receipt linked to the purchase, with mixed field provenance
    doc = _stored_document(db_session, registered_user["user"]["id"], doc_type=DocumentType.RECEIPT, filename="receipt.png", data=RECEIPT_BYTES)
    purchase = db_session.get(Purchase, p["id"])
    purchase.document_id = doc.id
    db_session.commit()
    _field(db_session, doc.id, "purchase_date", "2026-08-30", VerificationStatus.AI_EXTRACTED)
    _field(db_session, doc.id, "serial_number", "SN12345", VerificationStatus.USER_CONFIRMED)
    _field(db_session, doc.id, "junk_field", "nope", VerificationStatus.REJECTED)
    db_session.commit()

    ev = app_client.post(
        f"/api/v1/claims/{claim['id']}/evidence",
        headers=h,
        files={"file": ("broken-part.png", PHOTO_BYTES, "image/png")},
    ).json()["data"]["evidence"]

    resp = app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)
    assert resp.status_code == 200, resp.text
    pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]

    # purchase: DRAFT (unconfirmed) -> INFERRED, never presented as verified
    assert pkg["purchase"]["provenance"] == "INFERRED"
    # items: user-entered
    assert pkg["items"][0]["provenance"] == "USER_CONFIRMED"
    assert pkg["items"][0]["serial_number"] == "SN12345"
    # issue + contact: user-provided
    assert pkg["issue_provenance"] == "USER_CONFIRMED"
    assert pkg["contact"]["provenance"] == "USER_CONFIRMED"
    # warranty: external source + computed timelines + match provenance
    w = pkg["warranty"][0]
    assert w["source_provenance"] == "EXTERNAL_SOURCE"
    assert w["source"]["trust_level"] == 2
    assert w["match_provenance"] in ("AI_EXTRACTED", "USER_CONFIRMED")
    for t in w["timelines"]:
        assert t["provenance"] == "COMPUTED"
    # receipt section: stored document + per-field provenance, rejected fields excluded
    assert pkg["receipt"] is not None
    assert pkg["receipt"]["sha256"] == hashlib.sha256(RECEIPT_BYTES).hexdigest()
    fields = {f["field_name"]: f for f in pkg["receipt"]["fields"]}
    assert fields["purchase_date"]["provenance"] == "AI_EXTRACTED"
    assert fields["serial_number"]["provenance"] == "USER_CONFIRMED"
    assert "junk_field" not in fields
    # evidence: user-uploaded, media kind derived
    assert pkg["evidence"][0]["provenance"] == "USER_CONFIRMED"
    assert pkg["evidence"][0]["media_kind"] == "PHOTO"
    assert pkg["evidence"][0]["sha256"] == ev["sha256"]
    # report present + mentions provenance
    assert "## Manufacturer Warranty Coverage" in pkg["report"]
    assert "AI_EXTRACTED" in pkg["report"]


def test_package_purchase_user_confirmed(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])
    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)

    purchase = db_session.get(Purchase, p["id"])
    purchase.verification_status = PurchaseVerification.USER_CONFIRMED
    db_session.commit()

    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)  # regenerate
    pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]
    assert pkg["purchase"]["provenance"] == "USER_CONFIRMED"


# --- Stored-document evidence selection --------------------------------------


def _attach(app_client, h, claim_id: int, doc_id: int, role: str = "EVIDENCE") -> dict:
    r = app_client.post(
        f"/api/v1/claims/{claim_id}/documents", headers=h, json={"document_id": doc_id, "role": role}
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_claim_document_selection_and_package(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])

    receipt = _stored_document(db_session, registered_user["user"]["id"], doc_type=DocumentType.RECEIPT, filename="rcpt.png", data=RECEIPT_BYTES)
    card = _stored_document(db_session, registered_user["user"]["id"], doc_type=DocumentType.WARRANTY_CARD, filename="card.png", data=WARRANTY_CARD_BYTES)
    db_session.commit()

    # the purchase has no linked document -> the user-selected RECEIPT becomes the receipt
    a = _attach(app_client, h, claim["id"], receipt.id, "RECEIPT")
    assert a["created"] is True and a["document"]["role"] == "RECEIPT"
    _attach(app_client, h, claim["id"], card.id, "WARRANTY")

    listing = app_client.get(f"/api/v1/claims/{claim['id']}/documents", headers=h).json()["data"]["items"]
    assert {d["role"] for d in listing} == {"RECEIPT", "WARRANTY"}

    # re-attach is idempotent (updates role, no duplicate)
    a2 = _attach(app_client, h, claim["id"], card.id, "WARRANTY")
    assert a2["created"] is False
    listing = app_client.get(f"/api/v1/claims/{claim['id']}/documents", headers=h).json()["data"]["items"]
    assert len(listing) == 2

    # audit
    assert (
        db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.CLAIM_DOCUMENT_ATTACHED,
                AuditLog.resource_type == "claim_document",
            )
        )
        is not None
    )

    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)
    pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]
    assert pkg["receipt"]["id"] == receipt.id
    assert pkg["receipt"]["original_filename"] == "rcpt.png"
    assert len(pkg["warranty_documents"]) == 1
    assert pkg["warranty_documents"][0]["id"] == card.id
    assert pkg["warranty_documents"][0]["provenance"] == "USER_CONFIRMED"

    # detach
    r = app_client.delete(f"/api/v1/claims/{claim['id']}/documents/{card.id}", headers=h)
    assert r.status_code == 200
    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)
    pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]
    assert pkg["warranty_documents"] == []


def test_claim_document_foreign_document_rejected(app_client, registered_user, db_session):
    other = _register(app_client, email="doc-owner@example.com")
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])

    foreign_doc = _stored_document(
        db_session, other["user"]["id"], doc_type=DocumentType.RECEIPT, filename="theirs.png", data=RECEIPT_BYTES
    )
    db_session.commit()

    r = app_client.post(
        f"/api/v1/claims/{claim['id']}/documents", headers=h, json={"document_id": foreign_doc.id, "role": "RECEIPT"}
    )
    assert r.status_code == 404
    r = app_client.post(
        f"/api/v1/claims/{claim['id']}/documents", headers=h, json={"document_id": 999999, "role": "RECEIPT"}
    )
    assert r.status_code == 404
    # bad role rejected
    mine = _stored_document(db_session, registered_user["user"]["id"], doc_type=DocumentType.RECEIPT, filename="mine.png", data=RECEIPT_BYTES)
    db_session.commit()
    r = app_client.post(
        f"/api/v1/claims/{claim['id']}/documents", headers=h, json={"document_id": mine.id, "role": "NOPE"}
    )
    assert r.status_code == 422


# --- Evidence bundle (ZIP) ---------------------------------------------------


def _bundle(app_client, h, claim_id: int) -> zipfile.ZipFile:
    r = app_client.get(f"/api/v1/claims/{claim_id}/package/bundle", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_evidence_bundle_contents(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])
    doc = _stored_document(db_session, registered_user["user"]["id"], doc_type=DocumentType.RECEIPT, filename="receipt.png", data=RECEIPT_BYTES)
    purchase = db_session.get(Purchase, p["id"])
    purchase.document_id = doc.id
    db_session.commit()

    app_client.post(
        f"/api/v1/claims/{claim['id']}/evidence", headers=h, files={"file": ("part.png", PHOTO_BYTES, "image/png")}
    )
    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)

    zf = _bundle(app_client, h, claim["id"])
    names = zf.namelist()
    assert "package.json" in names
    assert "claim-report.md" in names
    assert "README.txt" in names
    ev_names = [n for n in names if n.startswith("evidence/")]
    doc_names = [n for n in names if n.startswith("documents/")]
    assert len(ev_names) == 1 and len(doc_names) == 1

    # byte-exact files
    assert zf.read(ev_names[0]) == PHOTO_BYTES
    assert zf.read(doc_names[0]) == RECEIPT_BYTES

    # README carries verifiable sha256s
    readme = zf.read("README.txt").decode()
    assert hashlib.sha256(PHOTO_BYTES).hexdigest() in readme
    assert hashlib.sha256(RECEIPT_BYTES).hexdigest() in readme
    # the embedded package matches the API package
    embedded = zf.read("package.json")
    import json

    api_pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]
    assert json.loads(embedded) == api_pkg
    # bundle requires a generated package
    c2 = _make_claim(app_client, h, p["id"])
    assert app_client.get(f"/api/v1/claims/{c2['id']}/package/bundle", headers=h).status_code == 409


def test_package_report_endpoint(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])
    # 409 before generation
    assert app_client.get(f"/api/v1/claims/{claim['id']}/package/report", headers=h).status_code == 409
    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)
    r = app_client.get(f"/api/v1/claims/{claim['id']}/package/report", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    report = r.text
    assert "# Claim-Ready Report" in report
    assert "## Purchase" in report
    assert "## Items" in report
    assert "## Manufacturer Warranty Coverage" in report
    assert "serial SN12345" in report
    assert "TSD §57" in report
    assert "provenance: COMPUTED" in report


# --- Claim edits + MVP boundary ----------------------------------------------


def test_claim_update_no_status_field(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])

    r = app_client.patch(
        f"/api/v1/claims/{claim['id']}",
        headers=h,
        json={"notes": "Called the helpline", "issue_description": "Worse: no cooling at all", "status": "RESOLVED"},
    )
    assert r.status_code == 200, r.text
    out = r.json()["data"]["claim"]
    assert out["notes"] == "Called the helpline"
    assert out["issue_description"] == "Worse: no cooling at all"
    # status is NOT an editable field — the claim stays DRAFT
    assert out["status"] == "DRAFT"

    assert (
        db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.CLAIM_UPDATED)
        )
        is not None
    )


def test_mvp_boundary_no_submission(app_client, registered_user, db_session):
    """TSD §57: the MVP stops at CLAIM_READY. There is no submission path."""
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])
    app_client.post(f"/api/v1/claims/{claim['id']}/package", headers=h)

    # no submit/forward/external endpoints exist
    for method, url in [
        ("POST", f"/api/v1/claims/{claim['id']}/submit"),
        ("POST", f"/api/v1/claims/{claim['id']}/external-submission"),
        ("PATCH", f"/api/v1/claims/{claim['id']}/status"),
    ]:
        assert app_client.request(method, url, headers=h, json={}).status_code == 404

    pkg = app_client.get(f"/api/v1/claims/{claim['id']}/package", headers=h).json()["data"]["package"]
    assert pkg["submission"]["mode"] == "CLAIM_READY"
    assert pkg["submission"]["external_submission"] is False
    assert "CAPTCHA" in pkg["submission"]["note"] and "MFA" in pkg["submission"]["note"]
    assert app_client.get("/api/v1/claims", headers=h).json()["data"]["items"][0]["status"] == "READY"


# --- Portable account export bundle ------------------------------------------


def test_account_export_bundle(app_client, registered_user, db_session):
    h = auth_headers(registered_user)
    p, _s = _warranty_purchase(app_client, registered_user, db_session)
    claim = _make_claim(app_client, h, p["id"])
    doc = _stored_document(db_session, registered_user["user"]["id"], doc_type=DocumentType.RECEIPT, filename="export-rcpt.png", data=RECEIPT_BYTES)
    purchase = db_session.get(Purchase, p["id"])
    purchase.document_id = doc.id
    db_session.commit()
    app_client.post(
        f"/api/v1/claims/{claim['id']}/evidence", headers=h, files={"file": ("ev.png", PHOTO_BYTES, "image/png")}
    )

    r = app_client.get("/api/v1/account/export/bundle", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "manifest.json" in names
    assert any(n.startswith("documents/") and n.endswith("export-rcpt.png") for n in names)
    ev_names = [n for n in names if n.startswith("claims/") and n.endswith("ev.png")]
    assert len(ev_names) == 1
    assert zf.read(ev_names[0]) == PHOTO_BYTES

    import json

    manifest = json.loads(zf.read("manifest.json"))
    assert len(manifest["purchases"]) == 1
    assert manifest["purchases"][0]["id"] == p["id"]
    assert manifest["claims"][0]["id"] == claim["id"]
    # the JSON export endpoint still works and matches the manifest payload
    js = app_client.get("/api/v1/account/export", headers=h).json()["data"]
    assert js["purchases"][0]["id"] == p["id"]
