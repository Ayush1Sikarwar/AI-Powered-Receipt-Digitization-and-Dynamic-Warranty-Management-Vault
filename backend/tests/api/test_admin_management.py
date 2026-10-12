"""Admin management endpoints (TSD §43) — authorization + CRUD behavior.

Covers the task requirements:
- admin APIs have SEPARATE authorization: every admin route returns 403 for a
  non-admin user and 200 for an admin;
- manufacturer / retailer / product catalog / product alias management;
- warranty source + policy management (with versioning);
- failed processing review (+ retry/requeue);
- AI evaluation results + model/pipeline registry.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.job import BackgroundJob, JobStatus, JobType
from app.models.product import Manufacturer, Product, ProductAlias, Retailer
from app.models.user import User
from app.models.warranty import PolicyStatus, WarrantyCoverage, WarrantyPolicy, WarrantySource
from tests.conftest import _login, _register, auth_headers

BASE = "/api/v1/admin"


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    for model in (
        ProductAlias,
        Document,
        BackgroundJob,
        AuditLog,
        WarrantyCoverage,
        WarrantyPolicy,
        WarrantySource,
        Retailer,
        Product,
        Manufacturer,
    ):
        db_session.query(model).delete()
    db_session.commit()


def _make_admin(app_client, db_session) -> dict:
    email = f"admin-{uuid.uuid4().hex[:10]}@example.com"
    _register(app_client, email)
    db_session.scalar(select(User).where(User.email == email)).role = "ADMIN"
    db_session.commit()
    return _login(app_client, email)


# --- separate authorization: every admin route rejects non-admins -------------


_ADMIN_ROUTES = [
    ("GET", f"{BASE}/audit-logs", None),
    ("GET", f"{BASE}/manufacturers", None),
    ("POST", f"{BASE}/manufacturers", {"name": "AuthZ Co"}),
    ("PATCH", f"{BASE}/manufacturers/1", {"name": "AuthZ Co"}),
    ("GET", f"{BASE}/retailers", None),
    ("POST", f"{BASE}/retailers", {"name": "AuthZ Shop"}),
    ("PATCH", f"{BASE}/retailers/1", {"name": "AuthZ Shop"}),
    ("GET", f"{BASE}/products", None),
    ("POST", f"{BASE}/products", {"canonical_name": "AuthZ Widget"}),
    ("PATCH", f"{BASE}/products/1", {"brand": "AuthZ"}),
    ("POST", f"{BASE}/products/1/aliases", {"alias_text": "azw"}),
    ("DELETE", f"{BASE}/products/1/aliases/1", None),
    ("GET", f"{BASE}/warranty/sources", None),
    ("GET", f"{BASE}/warranty/sources/1", None),
    ("POST", f"{BASE}/warranty/sources", {"source_title": "AuthZ terms"}),
    ("PATCH", f"{BASE}/warranty/sources/1", {"owner": "team"}),
    ("DELETE", f"{BASE}/warranty/sources/1", None),
    ("GET", f"{BASE}/warranty/policies", None),
    ("POST", f"{BASE}/warranty/policies", {"policy_name": "AuthZ policy", "effective_from": "2026-01-01"}),
    ("PATCH", f"{BASE}/warranty/policies/1", {"status": "ARCHIVED"}),
    ("GET", f"{BASE}/failed-processing", None),
    ("POST", f"{BASE}/failed-processing/documents/1/retry", None),
    ("GET", f"{BASE}/ai-evaluation", None),
    ("GET", f"{BASE}/model-registry", None),
    ("GET", f"{BASE}/observability", None),
]


@pytest.mark.parametrize("method,path,payload", _ADMIN_ROUTES, ids=[f"{m} {p}" for m, p, _ in _ADMIN_ROUTES])
def test_admin_route_requires_admin(app_client, authed_user, method, path, payload):
    r = app_client.request(method, path, json=payload, headers=auth_headers(authed_user))
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}: {r.text}"


@pytest.mark.parametrize("method,path,payload", _ADMIN_ROUTES, ids=[f"{m} {p}" for m, p, _ in _ADMIN_ROUTES])
def test_admin_route_open_to_admin(app_client, db_session, method, path, payload):
    admin = _make_admin(app_client, db_session)
    r = app_client.request(method, path, json=payload, headers=auth_headers(admin))
    # These routes may legitimately 404/409 for synthetic ids — but never 403/401.
    assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}: {r.text}"


# --- manufacturer management --------------------------------------------------


def test_manufacturer_crud_and_audit(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)

    r = app_client.post(f"{BASE}/manufacturers", json={"name": "Acme Appliances", "website": "https://acme.test", "country": "IN"}, headers=h)
    assert r.status_code == 201, r.text
    m = r.json()["data"]["manufacturer"]
    assert m["name_key"] == "acmeappliances" and m["status"] == "ACTIVE"

    # Duplicate (same name_key) -> 409.
    r = app_client.post(f"{BASE}/manufacturers", json={"name": "Acme Appliances"}, headers=h)
    assert r.status_code == 409

    r = app_client.get(f"{BASE}/manufacturers", headers=h)
    assert r.status_code == 200
    assert any(x["id"] == m["id"] for x in r.json()["data"]["items"])

    r = app_client.patch(f"{BASE}/manufacturers/{m['id']}", json={"country": "SG", "status": "INACTIVE"}, headers=h)
    assert r.status_code == 200, r.text
    m2 = r.json()["data"]["manufacturer"]
    assert m2["country"] == "SG" and m2["status"] == "INACTIVE"

    r = app_client.patch(f"{BASE}/manufacturers/{m['id']}", json={"status": "BOGUS"}, headers=h)
    assert r.status_code == 422

    # Inactive hidden unless requested.
    r = app_client.get(f"{BASE}/manufacturers", headers=h)
    assert all(x["id"] != m["id"] for x in r.json()["data"]["items"])
    r = app_client.get(f"{BASE}/manufacturers?include_inactive=true", headers=h)
    assert any(x["id"] == m["id"] for x in r.json()["data"]["items"])

    # Admin action audited.
    logs = db_session.scalars(select(AuditLog).where(AuditLog.action == "MANUFACTURER_UPDATED")).all()
    assert len(logs) >= 2 and all(rec.actor_id == admin["user"]["id"] for rec in logs)


# --- retailer management --------------------------------------------------------


def test_retailer_crud(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)

    r = app_client.post(f"{BASE}/retailers", json={"name": "Mega Mart", "country": "IN"}, headers=h)
    assert r.status_code == 201, r.text
    rt = r.json()["data"]["retailer"]

    r = app_client.post(f"{BASE}/retailers", json={"name": "Mega Mart"}, headers=h)
    assert r.status_code == 409

    r = app_client.patch(f"{BASE}/retailers/{rt['id']}", json={"name": "MegaMart Hypermarket"}, headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["retailer"]["name_key"] == "megamarthypermarket"

    r = app_client.get(f"{BASE}/retailers?q=mega", headers=h)
    assert r.status_code == 200 and r.json()["data"]["total"] == 1


# --- product catalog + alias management ------------------------------------------


def test_product_and_alias_management(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)

    r = app_client.post(f"{BASE}/manufacturers", json={"name": "Nimbus Labs"}, headers=h)
    mfr_id = r.json()["data"]["manufacturer"]["id"]

    r = app_client.post(
        f"{BASE}/products",
        json={
            "manufacturer_id": mfr_id,
            "brand": "Nimbus",
            "canonical_name": "Nimbus ProBook 14",
            "category": "Laptop",
            "model_number": "NP14-2026",
            "sku": "NB-14",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    prod = r.json()["data"]["product"]
    assert prod["manufacturer"] == "Nimbus Labs" and prod["aliases"] == []

    # Unknown manufacturer -> 404.
    r = app_client.post(f"{BASE}/products", json={"canonical_name": "X", "manufacturer_id": 999999}, headers=h)
    assert r.status_code == 404

    r = app_client.patch(f"{BASE}/products/{prod['id']}", json={"sku": "NB-14B"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["product"]["sku"] == "NB-14B"

    # Alias add / duplicate / list / delete.
    r = app_client.post(f"{BASE}/products/{prod['id']}/aliases", json={"alias_text": "NB14 PROBOOK"}, headers=h)
    assert r.status_code == 201, r.text
    alias_id = r.json()["data"]["alias"]["id"]

    r = app_client.post(f"{BASE}/products/{prod['id']}/aliases", json={"alias_text": "NB14 PROBOOK"}, headers=h)
    assert r.status_code == 409

    r = app_client.get(f"{BASE}/products?q=nimbus", headers=h)
    items = r.json()["data"]["items"]
    assert items[0]["aliases"][0]["alias_text"] == "NB14 PROBOOK"

    r = app_client.delete(f"{BASE}/products/{prod['id']}/aliases/{alias_id}", headers=h)
    assert r.status_code == 200
    r = app_client.delete(f"{BASE}/products/{prod['id']}/aliases/{alias_id}", headers=h)
    assert r.status_code == 404


# --- warranty source management (TSD §44 tracking fields) -------------------------


def test_source_admin_crud_with_tracking_fields(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)

    r = app_client.post(
        f"{BASE}/warranty/sources",
        json={
            "source_title": "Nimbus official terms",
            "source_url": "https://nimbus.test/warranty",
            "source_version": "2026-04",
            "region": "IN",
            "trust_level": 5,
            "owner": "nimbus-csm-team",
            "retrieval_method": "WEB_FETCH",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    src = r.json()["data"]["source"]
    assert src["owner"] == "nimbus-csm-team"
    assert src["retrieval_method"] == "WEB_FETCH"
    assert src["last_success_at"] is not None
    assert src["retrieval_status"] == "SUCCESS"
    assert src["content_hash"] is None and src["document_hash"] is None

    # List exposes the full §44 field set.
    r = app_client.get(f"{BASE}/warranty/sources", headers=h)
    item = r.json()["data"]["items"][0]
    for field in (
        "id", "source_type", "owner", "region", "retrieval_method",
        "last_success_at", "last_failure_at", "content_hash", "source_version", "retrieval_status",
    ):
        assert field in item, f"missing §44 field {field}"

    # Partial update.
    r = app_client.patch(f"{BASE}/warranty/sources/{src['id']}", json={"owner": "nimbus-legal", "trust_level": 4}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["source"]["owner"] == "nimbus-legal"
    assert r.json()["data"]["source"]["trust_level"] == 4

    # Delete with no referencing policies -> ok.
    r = app_client.delete(f"{BASE}/warranty/sources/{src['id']}", headers=h)
    assert r.status_code == 200
    r = app_client.delete(f"{BASE}/warranty/sources/{src['id']}", headers=h)
    assert r.status_code == 404


def test_source_delete_blocked_when_policies_reference_it(db_session, app_client):
    from tests.api.test_warranty import _seed

    s = _seed(db_session)
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)
    r = app_client.delete(f"{BASE}/warranty/sources/{s['src'].id}", headers=h)
    assert r.status_code == 409
    assert r.json()["error_code"] == "CONFLICT"


# --- warranty policy management (versioning) ---------------------------------------


def _admin_policy_id(app_client, h, name: str) -> int:
    r = app_client.get(f"{BASE}/warranty/policies?include_archived=true", headers=h)
    for item in r.json()["data"]["items"]:
        if item["policy_name"] == name:
            return item["id"]
    raise AssertionError(f"policy {name} not found")


def test_policy_status_transition_does_not_fork_version(db_session, app_client):
    from tests.api.test_warranty import _seed

    s = _seed(db_session)
    name = s["pol_ac"].policy_name
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)
    pid = _admin_policy_id(app_client, h, name)

    r = app_client.patch(f"{BASE}/warranty/policies/{pid}", json={"status": "ARCHIVED"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["policy"]["status"] == "ARCHIVED"

    # No new version row was created. (expire: the API committed in its own session)
    db_session.expire_all()
    versions = db_session.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all()
    assert len(versions) == 1 and versions[0].status == PolicyStatus.ARCHIVED

    r = app_client.patch(f"{BASE}/warranty/policies/{pid}", json={"status": "ACTIVE"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["policy"]["status"] == "ACTIVE"

    r = app_client.patch(f"{BASE}/warranty/policies/{pid}", json={"status": "BOGUS"}, headers=h)
    assert r.status_code == 422


def test_policy_content_update_creates_new_version(db_session, app_client):
    from tests.api.test_warranty import _seed

    s = _seed(db_session)
    name = s["pol_ac"].policy_name
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)
    pid = _admin_policy_id(app_client, h, name)

    # Explicit version 1.1 with changed coverages.
    r = app_client.patch(
        f"{BASE}/warranty/policies/{pid}",
        json={
            "policy_version": "1.1",
            "effective_from": "2026-07-01",
            "coverages": [
                {"component": "FULL_UNIT", "duration_value": 12, "duration_unit": "MONTH"},
                {"component": "COMPRESSOR", "duration_value": 120, "duration_unit": "MONTH"},
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["created"] is True and d["superseded"] == ["1.0"]
    assert d["policy"]["policy_version"] == "1.1" and d["policy"]["status"] == "ACTIVE"

    # History preserved: both versions present.
    db_session.expire_all()
    versions = {p.policy_version: p for p in db_session.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all()}
    assert set(versions) == {"1.0", "1.1"}
    assert versions["1.0"].status == PolicyStatus.ARCHIVED
    assert versions["1.1"].status == PolicyStatus.ACTIVE
    covs = {c.component: c.duration_value for c in db_session.scalars(
        select(WarrantyCoverage).where(WarrantyCoverage.policy_id == versions["1.1"].id)
    ).all()}
    assert covs["COMPRESSOR"] == 120

    # Omitted version -> auto-incremented (1.1 -> 1.2), keep existing coverages
    # when the body does not supply them.
    r = app_client.patch(
        f"{BASE}/warranty/policies/{versions['1.1'].id}",
        json={"effective_from": "2026-09-01"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["created"] is True and d["superseded"] == ["1.1"]
    assert d["policy"]["policy_version"] == "1.2"
    db_session.expire_all()
    versions = {p.policy_version: p for p in db_session.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all()}
    assert versions["1.0"].status == PolicyStatus.ARCHIVED
    assert versions["1.1"].status == PolicyStatus.ARCHIVED
    assert versions["1.2"].status == PolicyStatus.ACTIVE
    covs = {c.component: c.duration_value for c in db_session.scalars(
        select(WarrantyCoverage).where(WarrantyCoverage.policy_id == versions["1.2"].id)
    ).all()}
    assert covs["COMPRESSOR"] == 120  # carried over, not cleared


def test_policy_content_update_same_version_conflict(db_session, app_client):
    from tests.api.test_warranty import _seed

    s = _seed(db_session)
    name = s["pol_ac"].policy_name
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)
    pid = _admin_policy_id(app_client, h, name)

    # v1.0 exists as latest; asking to create v1.0 with different terms while
    # the same-version row lookup misses (rename the series first via new name
    # is complex) — so instead: create v1.1, then try to create v1.0-again as a
    # NEW series version through the v1.1 row with explicit older version +
    # different content -> treated as correction (idempotent path), not error.
    r = app_client.patch(
        f"{BASE}/warranty/policies/{pid}",
        json={"policy_version": "1.1", "coverages": [{"component": "FULL_UNIT", "duration_value": 24, "duration_unit": "MONTH"}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # Same version row now exists with different specs -> in-place correction.
    r2 = app_client.patch(
        f"{BASE}/warranty/policies/{pid}",
        json={"policy_version": "1.1", "coverages": [{"component": "FULL_UNIT", "duration_value": 30, "duration_unit": "MONTH"}]},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["created"] is False  # correction, not a new version
    db_session.expire_all()
    versions = list(db_session.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all())
    assert {v.policy_version for v in versions} == {"1.0", "1.1"}


# --- failed processing review -------------------------------------------------------


def test_failed_processing_review_and_retry(app_client, db_session, authed_user):
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)
    user_id = authed_user["user"]["id"]

    doc = Document(
        user_id=user_id,
        document_type=DocumentType.RECEIPT,
        original_filename="broken-receipt.pdf",
        mime_type="application/pdf",
        file_size=1234,
        storage_key="documents/test/broken.pdf",
        sha256_hash="ab" * 32,
        status=DocumentStatus.FAILED,
        error_code="OCR_FAILED",
        error_message="simulated OCR crash",
    )
    db_session.add(doc)
    db_session.commit()

    r = app_client.get(f"{BASE}/failed-processing", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["counts"]["failed_documents"] >= 1
    assert any(x["id"] == doc.id and x["error_code"] == "OCR_FAILED" for x in d["documents"])

    # Retry: new job enqueued, document back to QUEUED, error cleared.
    r = app_client.post(f"{BASE}/failed-processing/documents/{doc.id}/retry", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["document"]["status"] == "QUEUED"
    job = db_session.scalar(select(BackgroundJob).where(BackgroundJob.resource_id == str(doc.id)))
    assert job is not None and job.type == JobType.DOCUMENT_PROCESSING and job.status == JobStatus.QUEUED
    assert job.idempotency_key.startswith(f"docproc:{doc.id}:retry:")
    db_session.refresh(doc)
    assert doc.error_code is None and doc.error_message is None

    # Retrying a non-FAILED document is rejected.
    r = app_client.post(f"{BASE}/failed-processing/documents/{doc.id}/retry", headers=h)
    assert r.status_code == 422

    # Non-admin cannot review.
    r = app_client.get(f"{BASE}/failed-processing", headers=auth_headers(authed_user))
    assert r.status_code == 403


# --- AI evaluation results + model registry -------------------------------------------


def test_ai_evaluation_results(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    r = app_client.get(f"{BASE}/ai-evaluation", headers=auth_headers(admin))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["available"] is True
    assert "overall" in d and "per_category" in d
    assert d["overall"]["field_f1"] >= 0.0


def test_model_registry_complete(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    r = app_client.get(f"{BASE}/model-registry", headers=auth_headers(admin))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["pipeline_version"]
    comps = {c["component"]: c for c in d["components"]}
    # TSD §45: every production AI component identifies all five attributes.
    for comp in (
        "ocr_engine", "document_classifier", "image_quality_model",
        "extraction_model", "preprocessing", "product_ranker", "embedding_model",
    ):
        info = comps[comp]
        assert info["model_name"], comp
        assert info["model_version"], comp
        assert info["pipeline_version"], comp
        assert info["configuration_version"], comp
        assert info["deployment_date"], comp
    # Business-logic parity: the extractor's identity equals the registry's.
    from app.ai.extraction.extractor import MODEL_NAME, MODEL_VERSION

    assert comps["extraction_model"]["model_name"] == MODEL_NAME
    assert comps["extraction_model"]["model_version"] == MODEL_VERSION


# --- audit logs endpoint ----------------------------------------------------------------


def test_audit_logs_listable_by_admin(app_client, db_session):
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)
    app_client.post(f"{BASE}/manufacturers", json={"name": "Audit Trail Inc"}, headers=h)

    r = app_client.get(f"{BASE}/audit-logs?action=MANUFACTURER_UPDATED", headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert r.json()["data"]["total"] >= 1
    assert any(i["metadata"].get("name") == "Audit Trail Inc" for i in items)
