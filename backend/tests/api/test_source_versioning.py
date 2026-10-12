"""Source versioning & refresh tracking (TSD §44 Data Source Management).

Covers the task requirements:
- every source tracks: identifier, type, owner, region, retrieval method,
  last successful retrieval, last failure, content hash, version, status;
- content changes produce a NEW policy version (predecessor archived,
  history preserved); unchanged content is a no-op;
- a failed refresh records the failure (last_failure_at) and NEVER touches
  the previous valid policy;
- model/pipeline identity is registry-sourced (no hard-coded versions in
  business logic).
"""

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.product import Manufacturer, Product, Retailer
from app.models.purchase import Purchase, PurchaseItem
from app.models.user import User
from app.models.warranty import (
    PolicyStatus,
    RetrievalStatus,
    SourceType,
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantySource,
    WarrantyTimeline,
)
from app.warranty.sources import refresh_source, source_out
from tests.api.test_warranty import _seed
from tests.conftest import _login, _register, auth_headers


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    for model in (
        Notification,
        WarrantyTimeline,
        WarrantyMapping,
        WarrantyCoverage,
        WarrantyPolicy,
        WarrantySource,
        PurchaseItem,
        Purchase,
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


def _tracked_source(db, mfr) -> WarrantySource:
    src = WarrantySource(
        manufacturer_id=mfr.id,
        source_type=SourceType.OFFICIAL_WEB,
        source_url="https://example.com/warranty-terms",
        source_title="Sample official terms page (fixture)",
        source_version="2026-04",
        region="IN",
        trust_level=5,
        owner="qtech-warranty-team",
        retrieval_method="WEB_FETCH",
    )
    db.add(src)
    db.commit()
    return src


def _coverage_specs(compressor_months: int) -> list[dict]:
    return [
        {"component": "FULL_UNIT", "duration_value": 12, "duration_unit": "MONTH"},
        {"component": "COMPRESSOR", "duration_value": compressor_months, "duration_unit": "MONTH"},
    ]


# --- tracking fields -----------------------------------------------------------


def test_source_out_exposes_full_tracking_set(db_session):
    s = _seed(db_session)
    src = _tracked_source(db_session, s["mfr"])
    content = "warranty terms 1.0: compressor 60 months"
    src.document_hash = hashlib.sha256(content.encode()).hexdigest()
    db_session.commit()

    result = refresh_source(db_session, src.id, fetch=lambda url: content)
    assert result["status"] == "UNCHANGED"
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    out = source_out(db_session, src)

    assert out["id"] == src.id                      # source identifier
    assert out["source_type"] == SourceType.OFFICIAL_WEB.value  # source type
    assert out["owner"] == "qtech-warranty-team"    # owner
    assert out["region"] == "IN"                    # region
    assert out["retrieval_method"] == "WEB_FETCH"   # retrieval method
    assert out["last_success_at"] is not None       # last successful retrieval
    assert out["last_failure_at"] is None           # last failure (none yet)
    assert out["content_hash"] == hashlib.sha256(content.encode()).hexdigest()  # content hash
    assert out["document_hash"] == out["content_hash"]
    assert out["source_version"] == "2026-04"       # version
    assert out["retrieval_status"] == RetrievalStatus.SUCCESS.value  # status


def test_content_hash_changes_with_content(db_session):
    s = _seed(db_session)
    src = _tracked_source(db_session, s["mfr"])
    content_v1 = "warranty terms 1.0: compressor 60 months"
    src.document_hash = hashlib.sha256(content_v1.encode()).hexdigest()
    db_session.commit()

    result = refresh_source(db_session, src.id, fetch=lambda url: "warranty terms 1.1: compressor 120 months",
                            policies=[
                                {
                                    "policy_name": s["pol_ac"].policy_name,
                                    "policy_version": "1.1",
                                    "effective_from": "2026-07-01",
                                    "product_category": s["pol_ac"].product_category,
                                    "region": "IN",
                                    "coverages": _coverage_specs(120),
                                }
                            ])
    assert result["status"] == "CHANGED" and result["content_changed"] is True
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    # The content hash now reflects the NEW content (so a later unchanged
    # refresh is detected correctly).
    assert src.document_hash == hashlib.sha256(b"warranty terms 1.1: compressor 120 months").hexdigest()


# --- version bump via structured terms -------------------------------------------


def test_refresh_version_bump_preserves_history(db_session):
    s = _seed(db_session)
    name = s["pol_ac"].policy_name
    src = _tracked_source(db_session, s["mfr"])
    content_v1 = "warranty terms 1.0: compressor 60 months"
    src.document_hash = hashlib.sha256(content_v1.encode()).hexdigest()
    db_session.commit()

    result = refresh_source(
        db_session, src.id,
        fetch=lambda url: "warranty terms 1.1: compressor 120 months",
        policies=[
            {
                "policy_name": name,
                "policy_version": "1.1",
                "effective_from": "2026-07-01",
                "product_category": s["pol_ac"].product_category,
                "region": "IN",
                "coverages": _coverage_specs(120),
            }
        ],
    )
    assert result["status"] == "CHANGED"
    assert result["policies"][0]["created"] is True
    assert result["policies"][0]["superseded"] == ["1.0"]

    versions = {p.policy_version: p for p in
                db_session.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all()}
    assert set(versions) == {"1.0", "1.1"}
    assert versions["1.0"].status == PolicyStatus.ARCHIVED
    assert versions["1.1"].status == PolicyStatus.ACTIVE

    # New version carries the NEW terms.
    covs = {c.component: c.duration_value for c in db_session.scalars(
        select(WarrantyCoverage).where(WarrantyCoverage.policy_id == versions["1.1"].id)).all()}
    assert covs["COMPRESSOR"] == 120
    covs = {c.component: c.duration_value for c in db_session.scalars(
        select(WarrantyCoverage).where(WarrantyCoverage.policy_id == versions["1.0"].id)).all()}
    assert covs["COMPRESSOR"] == 60


# --- failed refresh never touches the policy --------------------------------------


def test_failed_refresh_records_last_failure_and_keeps_policy(db_session):
    s = _seed(db_session)
    name = s["pol_ac"].policy_name
    src = _tracked_source(db_session, s["mfr"])
    content_v1 = "warranty terms 1.0: compressor 60 months"
    src.document_hash = hashlib.sha256(content_v1.encode()).hexdigest()
    db_session.commit()

    # First: a successful refresh sets last_success_at.
    refresh_source(db_session, src.id, fetch=lambda url: content_v1)
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    assert src.last_success_at is not None

    before_success = src.last_success_at
    n_versions_before = db_session.query(WarrantyPolicy).filter_by(policy_name=name).count()

    # Now: a failing refresh.
    def failing_fetch(url: str) -> str:
        raise ConnectionError("simulated outage")

    result = refresh_source(db_session, src.id, fetch=failing_fetch)
    assert result["status"] == "FAILED"
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)

    assert src.retrieval_status == RetrievalStatus.FAILED
    assert src.last_failure_at is not None and src.last_failure_at >= before_success
    assert "simulated outage" in (src.last_error or "")
    # Content hash and version are untouched by the failure.
    assert src.document_hash == hashlib.sha256(content_v1.encode()).hexdigest()
    assert src.source_version == "2026-04"

    # The previous valid policy is fully intact: same versions, all ACTIVE.
    versions = list(db_session.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all())
    assert len(versions) == n_versions_before
    assert all(p.status == PolicyStatus.ACTIVE for p in versions)


def test_recovery_after_failed_refresh(db_session):
    s = _seed(db_session)
    src = _tracked_source(db_session, s["mfr"])
    content_v1 = "warranty terms 1.0: compressor 60 months"
    src.document_hash = hashlib.sha256(content_v1.encode()).hexdigest()
    db_session.commit()

    refresh_source(db_session, src.id, fetch=lambda url: (_ for _ in ()).throw(ConnectionError("down")))
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    assert src.retrieval_status == RetrievalStatus.FAILED and src.last_failure_at is not None

    # Recovery: unchanged content -> SUCCESS, last_failure_at retained (history),
    # last_success_at refreshed.
    result = refresh_source(db_session, src.id, fetch=lambda url: content_v1)
    assert result["status"] == "UNCHANGED"
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    assert src.retrieval_status == RetrievalStatus.SUCCESS
    assert src.last_error is None
    assert src.last_success_at is not None and src.last_success_at >= src.last_failure_at


# --- refresh via the admin HTTP endpoint ------------------------------------------


def test_admin_refresh_endpoint_sets_tracking_fields(db_session, app_client, monkeypatch):
    s = _seed(db_session)
    src = _tracked_source(db_session, s["mfr"])
    admin = _make_admin(app_client, db_session)
    h = auth_headers(admin)

    # Stub the network fetch: patch the symbol the endpoint module imported.
    import app.api.v1.warranty as warranty_module

    def stub_refresh(db, source_id, **kwargs):
        return refresh_source(db, source_id, fetch=lambda url: "warranty terms 1.0: compressor 60 months", **kwargs)

    monkeypatch.setattr(warranty_module, "refresh_source", stub_refresh)

    r = app_client.post(f"/api/v1/warranty/sources/{src.id}/refresh", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["source"]["last_success_at"] is not None
    assert d["source"]["content_hash"] == hashlib.sha256(b"warranty terms 1.0: compressor 60 months").hexdigest()
    assert d["source"]["owner"] == "qtech-warranty-team"
    assert d["source"]["retrieval_method"] == "WEB_FETCH"


# --- model/pipeline identity is registry-sourced ------------------------------------


def test_model_identity_comes_from_registry():
    from app.ai import registry
    from app.ai.classification.classifier import Classification
    from app.ai.extraction.extractor import MODEL_NAME, MODEL_VERSION
    from app.ai.product.matching import MatchCandidate, ProductMatchMethod

    reg = {i.component: i for i in registry.registry()}
    assert reg["extraction_model"].model_name == MODEL_NAME
    assert reg["extraction_model"].model_version == MODEL_VERSION

    c = MatchCandidate(
        product_id=1, product_name="x", manufacturer=None, model_number=None,
        sku=None, method=ProductMatchMethod.EXACT_MODEL, confidence=0.9,
    )
    assert c.engine == reg["product_ranker"].model_name
    assert c.engine_version == reg["product_ranker"].model_version

    cl = Classification(document_type=None, confidence=0.1, scores={})
    assert cl.method == reg["document_classifier"].model_name
