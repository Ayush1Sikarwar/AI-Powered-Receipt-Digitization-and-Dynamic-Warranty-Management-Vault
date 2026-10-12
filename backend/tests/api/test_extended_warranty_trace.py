"""Extended warranties + TSD §42 traceability + dashboard/search.

Covers: extended-warranty CRUD + timeline computation (where data supports
it, never guessed), multiple simultaneous coverage periods, the full
traceability chain with provenance labels, and dashboard/search filters.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.models.product import Manufacturer, Product
from app.models.purchase import Purchase
from app.models.warranty import (
    PurchaseExtendedWarranty,
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantyTimeline,
)
from tests.api.test_warranty import _create_purchase, _seed
from tests.conftest import auth_headers

TODAY = date(2026, 8, 31)


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(WarrantyTimeline).delete()
    db_session.query(PurchaseExtendedWarranty).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    from app.models.purchase import PurchaseItem
    from app.models.warranty import WarrantySource

    db_session.query(WarrantySource).delete()
    db_session.query(PurchaseItem).delete()
    db_session.query(Purchase).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _timelines_for_item(db, item_id):
    return list(db.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_id)).all())


# --- Extended warranty CRUD + timeline computation ---------------------------


def test_add_extended_warranty_creates_timeline(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    item_id = p["items"][0]["id"]
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/extended-warranties",
        headers=auth_headers(authed_user),
        json={
            "component": "COMPRESSOR",
            "duration_value": 24,
            "duration_unit": "MONTH",
            "start_condition": "PURCHASE_DATE",
            "source_type": "USER_DECLARED",
            "notes": "bought an extended compressor cover",
        },
    )
    assert r.status_code == 201, r.text
    ew = r.json()["data"]["extended_warranty"]
    assert ew["timelines"][0]["start_date"] == "2026-08-31"
    assert ew["timelines"][0]["expiry_date"] == "2028-08-31"

    # Both the base 60-month compressor cover AND the 24-month extension
    # are simultaneously in force (two COMPRESSOR timelines).
    tls = _timelines_for_item(db_session, item_id)
    comps = [t.component for t in tls]
    assert comps.count("COMPRESSOR") == 2
    base = [t for t in tls if t.component == "COMPRESSOR" and t.coverage_id is not None]
    ext = [t for t in tls if t.component == "COMPRESSOR" and t.extended_warranty_id is not None]
    assert base[0].expiry_date == date(2031, 8, 31)  # 60 months
    assert ext[0].expiry_date == date(2028, 8, 31)  # 24 months


def test_extended_warranty_without_duration_is_unknown(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    # A finite unit with no recorded duration must be UNKNOWN (never guessed).
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/extended-warranties",
        headers=auth_headers(authed_user),
        json={"component": "DISPLAY", "duration_unit": "MONTH", "source_type": "USER_DECLARED"},
    )
    assert r.status_code == 201, r.text
    tl = r.json()["data"]["extended_warranty"]["timelines"][0]
    assert tl["status"] == "UNKNOWN"
    assert tl["expiry_date"] is None


def test_extended_warranty_installation_unknown_until_recorded(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/extended-warranties",
        headers=auth_headers(authed_user),
        json={"component": "FULL_UNIT", "duration_value": 12, "duration_unit": "MONTH", "start_condition": "INSTALLATION_DATE"},
    )
    assert r.status_code == 201, r.text
    tl = r.json()["data"]["extended_warranty"]["timelines"][0]
    assert tl["status"] == "UNKNOWN"  # no installation date yet

    # Record the installation date -> the extension clock starts from it.
    # (Re-applying items recreates the item row -> new id from the response.)
    r = app_client.patch(
        f"/api/v1/purchases/{p['id']}",
        headers=auth_headers(authed_user),
        json={"items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC", "installation_date": "2026-09-01"}]},
    )
    assert r.status_code == 200, r.text
    new_item_id = r.json()["data"]["purchase"]["items"][0]["id"]
    tls = _timelines_for_item(db_session, new_item_id)
    ext = [t for t in tls if t.extended_warranty_id is not None and t.component == "FULL_UNIT"]
    assert ext and ext[0].start_date == date(2026, 9, 1)
    assert ext[0].expiry_date == date(2027, 9, 1)
    # The purchase-level extension followed the re-created item, and the
    # base coverage timelines were recomputed for it too.
    base = [t for t in tls if t.coverage_id is not None]
    assert {t.component for t in base} == {"FULL_UNIT", "COMPRESSOR"}


def test_unlimited_extended_warranty_never_expires(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/extended-warranties",
        headers=auth_headers(authed_user),
        json={"component": "PARTS", "duration_unit": "UNLIMITED"},
    )
    assert r.status_code == 201, r.text
    tl = r.json()["data"]["extended_warranty"]["timelines"][0]
    assert tl["expiry_date"] is None
    assert tl["status"] == "ACTIVE"


def test_delete_extended_warranty_removes_timeline(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    item_id = p["items"][0]["id"]
    ew_id = app_client.post(
        f"/api/v1/purchases/{p['id']}/extended-warranties",
        headers=auth_headers(authed_user),
        json={"component": "BATTERY", "duration_value": 6, "duration_unit": "MONTH"},
    ).json()["data"]["extended_warranty"]["id"]

    r = app_client.delete(f"/api/v1/purchases/{p['id']}/extended-warranties/{ew_id}", headers=auth_headers(authed_user))
    assert r.status_code == 200, r.text
    tls = _timelines_for_item(db_session, item_id)
    assert not any(t.component == "BATTERY" and t.extended_warranty_id is not None for t in tls)
    # The base mapping/coverage timelines are untouched.
    comps = [t.component for t in tls if t.coverage_id is not None]
    assert "FULL_UNIT" in comps and "COMPRESSOR" in comps


def test_extended_warranty_requires_ownership(app_client, authed_user, db_session):
    import uuid as _uuid

    from tests.conftest import _register

    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    other = _register(app_client, f"other-{_uuid.uuid4().hex[:8]}@example.com")
    # A different user cannot touch this purchase.
    r = app_client.get(f"/api/v1/purchases/{p['id']}/extended-warranties", headers=auth_headers(other))
    assert r.status_code == 404
    r = app_client.post(
        f"/api/v1/purchases/{p['id']}/extended-warranties",
        headers=auth_headers(other),
        json={"component": "X", "duration_value": 1, "duration_unit": "YEAR"},
    )
    assert r.status_code == 404


# --- TSD §42 traceability chain ----------------------------------------------


def test_trace_chain_full(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    # Add a claim so the final chain link exists.
    cr = app_client.post(
        "/api/v1/claims",
        headers=auth_headers(authed_user),
        json={"purchase_id": p["id"], "issue_description": "Compressor noise after 3 months"},
    )
    assert cr.status_code == 201, cr.text

    r = app_client.get(f"/api/v1/purchases/{p['id']}/trace", headers=auth_headers(authed_user))
    assert r.status_code == 200, r.text
    t = r.json()["data"]

    # Every link of the TSD chain is present and labelled.
    assert t["purchase"]["provenance"] in ("AI_EXTRACTED", "USER_CONFIRMED")
    assert t["receipt"] is None  # manual purchase, no document
    assert t["products"][0]["canonical_name"] == "QTech Inverter Split AC 1.5 Ton"
    assert t["warranty"][0]["mapped"] is True
    assert t["warranty"][0]["policy_provenance"] == "EXTERNAL_SOURCE"
    assert any(e["component"] == "COMPRESSOR" for e in t["expiry"])
    assert all(e["provenance"] == "COMPUTED" for e in t["expiry"])
    assert t["claim"]["provenance"] == "USER_CONFIRMED"
    # The legend distinguishes all required categories.
    legend = t["provenance_legend"]
    for key in ("AI_EXTRACTED", "USER_CONFIRMED", "VERIFIED", "EXTERNAL_SOURCE", "INFERRED", "COMPUTED"):
        assert key in legend


def test_trace_provenance_never_marks_inferred_as_verified(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    t = app_client.get(f"/api/v1/purchases/{p['id']}/trace", headers=auth_headers(authed_user)).json()["data"]
    # The mapping was produced by the AI (catalog match) -> not "verified".
    m = t["warranty"][0]["mapping"]
    assert m["provenance"] in ("AI_EXTRACTED", "INFERRED", "USER_CONFIRMED")
    assert m["provenance"] != "VERIFIED"
    # Computed expiry is explicitly a computation, never a verified fact.
    assert t["expiry"][0]["provenance"] == "COMPUTED"


def test_trace_ownership(app_client, authed_user, db_session):
    import uuid as _uuid

    from tests.conftest import _register

    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    other = _register(app_client, f"other-{_uuid.uuid4().hex[:8]}@example.com")
    r = app_client.get(f"/api/v1/purchases/{p['id']}/trace", headers=auth_headers(other))
    assert r.status_code == 404


# --- Dashboard buckets + search filters --------------------------------------


def test_dashboard_incomplete_and_needs_review(app_client, authed_user, db_session):
    s = _seed(db_session)
    # 1) A mapped, active warranty.
    _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    # 2) Region resolves (owner has country IN) but no product -> incomplete.
    _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_name": "Unknown widget"}]},
    )

    d = app_client.get("/api/v1/dashboard/summary", headers=auth_headers(authed_user)).json()["data"]
    # The owner sees one active warranty and one incomplete (needs review).
    assert d["warranties"]["active"] == 1
    assert d["warranties"]["incomplete"] == 1
    assert d["total_purchases"] == 2


def test_search_warranty_status_and_needs_review(app_client, authed_user, db_session):
    s = _seed(db_session)
    # Mapped + active (region resolved, product known).
    p_active = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    # Region resolved but no product -> incomplete (needs review).
    p_incomplete = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_name": "Unknown widget"}]},
    )

    # Per-item warranty status is now exposed on list output.
    lst = app_client.get("/api/v1/purchases", headers=auth_headers(authed_user)).json()["data"]
    by_id = {i["id"]: i for i in lst["items"]}
    assert by_id[p_active["id"]]["warranty_status"] == "ACTIVE"
    assert by_id[p_incomplete["id"]]["warranty_status"] == "UNKNOWN"

    # Filter by a specific status.
    active = app_client.get("/api/v1/purchases", headers=auth_headers(authed_user), params={"warranty_status": "ACTIVE"}).json()["data"]
    assert any(i["id"] == p_active["id"] for i in active["items"])
    assert not any(i["id"] == p_incomplete["id"] for i in active["items"])

    # NEEDS_REVIEW captures the incomplete one.
    review = app_client.get("/api/v1/purchases", headers=auth_headers(authed_user), params={"warranty_status": "NEEDS_REVIEW"}).json()["data"]
    assert any(i["id"] == p_incomplete["id"] for i in review["items"])
    assert not any(i["id"] == p_active["id"] for i in review["items"])


def test_search_by_manufacturer_and_dates(app_client, authed_user, db_session):
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-31", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    # Manufacturer filter matches the QTech brand.
    r = app_client.get("/api/v1/purchases", headers=auth_headers(authed_user), params={"manufacturer": "QTech"}).json()["data"]
    assert any(i["id"] == p["id"] for i in r["items"])
    # A date window that excludes the purchase returns nothing.
    r2 = app_client.get(
        "/api/v1/purchases",
        headers=auth_headers(authed_user),
        params={"date_from": "2027-01-01", "date_to": "2027-12-31"},
    ).json()["data"]
    assert not any(i["id"] == p["id"] for i in r2["items"])
    # Product/SKU/model free-text search.
    r3 = app_client.get("/api/v1/purchases", headers=auth_headers(authed_user), params={"q": "AR15FSSY"}).json()["data"]
    assert any(i["id"] == p["id"] for i in r3["items"])
