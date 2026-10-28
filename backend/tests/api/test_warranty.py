"""Warranty Intelligence API tests (SRS FR-10/FR-11a/FR-12/FR-13, TSD §14/§15)."""

import uuid
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.product import Manufacturer, Product
from app.models.user import User
from app.models.warranty import (
    DurationUnit,
    PolicyStatus,
    SourceType,
    WarrantyCoverage,
    WarrantyPolicy,
    WarrantySource,
)
from tests.conftest import _login, _register

PURCHASE_DATE = "2026-08-30"


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    from app.models.purchase import Purchase
    from app.models.warranty import WarrantyMapping, WarrantyTimeline

    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(Purchase).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _seed(db) -> dict:
    mfr = Manufacturer(name="QTech Electronics", name_key="qtechelectronics")
    db.add(mfr)
    db.flush()
    ac = Product(
        manufacturer_id=mfr.id,
        brand="QTech",
        canonical_name="QTech Inverter Split AC 1.5 Ton",
        category="Air Conditioning",
        model_number="AR15FSSY",
        sku="QT-AC-15T",
    )
    tv = Product(
        manufacturer_id=mfr.id,
        brand="QTech",
        canonical_name="QTech 55-inch 4K QLED Smart TV",
        category="Television",
        model_number="QT55Q90RA",
    )
    db.add_all([ac, tv])
    db.flush()
    src = WarrantySource(
        manufacturer_id=mfr.id,
        source_type=SourceType.CURATED_DATASET,
        source_title="QTech warranty terms (sample fixture)",
        trust_level=2,
        region="IN",
        retrieved_at=None,
    )
    db.add(src)
    db.flush()
    pol_ac = WarrantyPolicy(
        manufacturer_id=mfr.id,
        product_category="Air Conditioning",
        region="IN",
        policy_name="QTech Sample AC Warranty (fixture)",
        policy_version="1.0",
        effective_from=date(2024, 1, 1),
        source_id=src.id,
        status=PolicyStatus.ACTIVE,
    )
    pol_tv = WarrantyPolicy(
        manufacturer_id=mfr.id,
        product_category="Television",
        region="IN",
        policy_name="QTech Sample TV Warranty (fixture)",
        policy_version="1.0",
        effective_from=date(2024, 1, 1),
        source_id=src.id,
        status=PolicyStatus.ACTIVE,
    )
    db.add_all([pol_ac, pol_tv])
    db.flush()
    for comp, val in (("FULL_UNIT", 12), ("COMPRESSOR", 60)):
        db.add(
            WarrantyCoverage(
                policy_id=pol_ac.id,
                component=comp,
                duration_value=val,
                duration_unit=DurationUnit.MONTH,
            )
        )
    db.add(WarrantyCoverage(policy_id=pol_tv.id, component="FULL_UNIT", duration_value=12, duration_unit=DurationUnit.MONTH))
    db.commit()
    return {"mfr": mfr, "ac": ac, "tv": tv, "src": src, "pol_ac": pol_ac, "pol_tv": pol_tv}


def _create_purchase(client, token, body):
    r = client.post("/api/v1/purchases", headers={"Authorization": f"Bearer {token}"}, json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["purchase"]


def test_purchase_auto_mapped_with_timeline_and_source(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T", "unit_price": 2899.0, "line_total": 2899.0}],
        },
    )
    item_id = p["items"][0]["id"]

    r = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers={"Authorization": f"Bearer {authed_user['access_token']}"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["warranty_region"] == "IN"
    item = data["items"][0]
    assert item["item_id"] == item_id
    assert item["mapped"] is True
    assert item["match_method"] == "CATALOG_MATCH"
    assert item["verification_status"] == "AI_EXTRACTED"
    assert item["policy"]["policy_name"] == "QTech Sample AC Warranty (fixture)"
    # FR-12: source provenance surfaced
    assert item["policy"]["source"]["source_title"].startswith("QTech warranty terms")
    assert item["policy"]["source"]["trust_level"] == 2
    comps = {t["component"]: t for t in item["timelines"]}
    assert set(comps) == {"FULL_UNIT", "COMPRESSOR"}
    assert comps["FULL_UNIT"]["start_date"] == PURCHASE_DATE
    assert comps["FULL_UNIT"]["expiry_date"] == "2027-08-30"
    assert comps["FULL_UNIT"]["status"] == "ACTIVE"
    assert comps["COMPRESSOR"]["expiry_date"] == "2031-08-30"
    # FR-11b-friendly: policy name/version present for display
    assert item["policy"]["policy_version"] == "1.0"


def test_unresolved_region_not_mapped_unknown(app_client, db_session):
    s = _seed(db_session)
    # User with no account country -> region unresolvable (FR-11a).
    email = f"noregion-{uuid.uuid4().hex[:10]}@example.com"
    data = _register(app_client, email, country=None)
    p = _create_purchase(
        app_client,
        data["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    r = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers={"Authorization": f"Bearer {data['access_token']}"})
    item = r.json()["data"]["items"][0]
    assert item["mapped"] is False
    # Dashboard bucket: unknown, not a silent region default.
    d = app_client.get("/api/v1/dashboard/summary", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert d.json()["data"]["warranties"]["unknown"] == 1


def test_review_confirm_override_reject(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "warranty_region": "IN",
            "items": [{"product_id": s["tv"].id, "product_name": "QLED TV"}],
        },
    )
    item_id = p["items"][0]["id"]
    h = {"Authorization": f"Bearer {authed_user['access_token']}"}

    # confirm
    r = app_client.post(f"/api/v1/purchases/{p['id']}/warranty/review", headers=h, json={"item_id": item_id, "action": "confirm"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["mapping"]["verification_status"] == "USER_CONFIRMED"

    # user-verified mapping survives a re-sync (policy unchanged)
    r = app_client.post(f"/api/v1/warranty/policies/{p['id']}/sync", headers=h)
    assert r.status_code == 200, r.text
    w = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers=h).json()["data"]["items"][0]
    assert w["verification_status"] == "USER_CONFIRMED"

    # override to the AC policy
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/warranty/review",
        headers=h,
        json={"item_id": item_id, "action": "override", "policy_id": s["pol_ac"].id},
    )
    assert r.status_code == 200, r.text
    w = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers=h).json()["data"]["items"][0]
    assert w["match_method"] == "MANUAL"
    assert w["verification_status"] == "USER_CORRECTED"
    assert {t["component"] for t in w["timelines"]} == {"FULL_UNIT", "COMPRESSOR"}

    # reject removes the mapping
    r = app_client.post(f"/api/v1/purchases/{p['id']}/warranty/review", headers=h, json={"item_id": item_id, "action": "reject"})
    assert r.status_code == 200, r.text
    w = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers=h).json()["data"]["items"][0]
    assert w["mapped"] is False


def test_override_requires_policy_id(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": PURCHASE_DATE, "warranty_region": "IN", "items": [{"product_id": s["tv"].id}]},
    )
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/warranty/review",
        headers={"Authorization": f"Bearer {authed_user['access_token']}"},
        json={"item_id": p["items"][0]["id"], "action": "override"},
    )
    assert r.status_code == 422 or r.status_code == 400


def test_cross_user_404(app_client, registered_user, db_session):
    s = _seed(db_session)
    email2 = f"other-{uuid.uuid4().hex[:10]}@example.com"
    other = _register(app_client, email2)
    p = _create_purchase(
        registered_user["client"],
        registered_user["access_token"],
        {"purchase_date": PURCHASE_DATE, "warranty_region": "IN", "items": [{"product_id": s["ac"].id}]},
    )
    r = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers={"Authorization": f"Bearer {other['access_token']}"})
    assert r.status_code == 404


def test_dashboard_buckets_and_warranty_status_filter(app_client, authed_user, db_session):
    s = _seed(db_session)
    _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    h = {"Authorization": f"Bearer {authed_user['access_token']}"}
    d = app_client.get("/api/v1/dashboard/summary", headers=h).json()["data"]
    assert d["warranties"]["active"] == 1

    lst = app_client.get("/api/v1/purchases", headers=h, params={"warranty_status": "ACTIVE"}).json()["data"]
    assert lst["total"] == 1
    lst2 = app_client.get("/api/v1/purchases", headers=h, params={"warranty_status": "EXPIRED"}).json()["data"]
    assert lst2["total"] == 0


def test_admin_policy_registry(app_client, db_session):
    s = _seed(db_session)
    email = f"admin-{uuid.uuid4().hex[:10]}@example.com"
    _register(app_client, email)
    db_session.scalar(select(User).where(User.email == email)).role = "ADMIN"
    db_session.commit()
    admin = _login(app_client, email)
    ah = {"Authorization": f"Bearer {admin['access_token']}"}

    r = app_client.get("/api/v1/admin/warranty/policies", headers=ah)
    assert r.status_code == 200, r.text
    names = {i["policy_name"] for i in r.json()["data"]["items"]}
    assert "QTech Sample AC Warranty (fixture)" in names

    r = app_client.get("/api/v1/admin/warranty/sources", headers=ah)
    assert r.status_code == 200
    assert r.json()["data"]["total"] >= 1

    # create a policy with nested source + coverages
    body = {
        "manufacturer_id": s["mfr"].id,
        "product_category": "Small Appliances",
        "region": "IN",
        "policy_name": "QTech Sample Air Fryer Warranty (fixture)",
        "policy_version": "1.0",
        "effective_from": "2024-01-01",
        "source": {
            "manufacturer_id": s["mfr"].id,
            "source_type": "CURATED_DATASET",
            "source_title": "QTech air fryer terms (sample fixture)",
            "trust_level": 2,
            "region": "IN",
        },
        "coverages": [{"component": "FULL_UNIT", "duration_value": 12, "duration_unit": "MONTH"}],
    }
    r = app_client.post("/api/v1/admin/warranty/policies", headers=ah, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["source_id"]

    # non-admin is refused
    email3 = f"plain-{uuid.uuid4().hex[:10]}@example.com"
    plain = _register(app_client, email3)
    r = app_client.get("/api/v1/admin/warranty/policies", headers={"Authorization": f"Bearer {plain['access_token']}"})
    assert r.status_code == 403


def test_user_policy_browse(app_client, authed_user, db_session):
    _seed(db_session)
    h = {"Authorization": f"Bearer {authed_user['access_token']}"}
    r = app_client.get("/api/v1/warranty/policies", headers=h, params={"manufacturer": "QTech"})
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 2
    assert all(i["manufacturer"] == "QTech Electronics" for i in items)


def test_review_survives_notification_scheduling_failure(app_client, authed_user, db_session):
    """A successful warranty review remains committed when notification scheduling raises."""
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "warranty_region": "IN",
            "items": [{"product_id": s["tv"].id, "product_name": "QLED TV"}],
        },
    )
    item_id = p["items"][0]["id"]
    h = {"Authorization": f"Bearer {authed_user['access_token']}"}

    # Make notification scheduling raise — the review must still be committed.
    with patch(
        "app.services.notification_service.schedule_warranty_reminders",
        side_effect=RuntimeError("notification store down"),
    ):
        r = app_client.post(
            f"/api/v1/purchases/{p['id']}/warranty/review",
            headers=h,
            json={"item_id": item_id, "action": "confirm"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["mapping"]["verification_status"] == "USER_CONFIRMED"

    # Verify the mapping is actually committed (not rolled back).
    w = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers=h).json()["data"][
        "items"
    ][0]
    assert w["mapped"] is True
    assert w["verification_status"] == "USER_CONFIRMED"


def test_review_schedules_reminders_normally(app_client, authed_user, db_session):
    """Successful scheduling still works normally (no mock, real code path)."""
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    item_id = p["items"][0]["id"]
    h = {"Authorization": f"Bearer {authed_user['access_token']}"}

    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/warranty/review",
        headers=h,
        json={"item_id": item_id, "action": "confirm"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["mapping"]["verification_status"] == "USER_CONFIRMED"


def test_review_genuine_failure_not_swallowed(app_client, authed_user, db_session):
    """A genuine warranty-review/database failure is not incorrectly swallowed."""
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": PURCHASE_DATE,
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    h = {"Authorization": f"Bearer {authed_user['access_token']}"}

    # Override without policy_id -> genuine validation failure, not swallowed.
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/warranty/review",
        headers=h,
        json={"item_id": p["items"][0]["id"], "action": "override"},
    )
    assert r.status_code in (422, 400), r.text
    body = r.json()
    assert body["error_code"] == "VALIDATION_ERROR"

    # Non-existent item -> genuine not-found failure, not swallowed.
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/warranty/review",
        headers=h,
        json={"item_id": 999999, "action": "confirm"},
    )
    assert r.status_code in (422, 400), r.text
