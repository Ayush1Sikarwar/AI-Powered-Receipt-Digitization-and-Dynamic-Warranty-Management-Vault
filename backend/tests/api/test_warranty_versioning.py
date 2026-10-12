"""Warranty versioning, source refresh, multi-policy purchases (API level).

Covers the task requirements:
- policy versioning: new version + preserved predecessor + retained
  historical mappings (no silent overwrite);
- source failure: a failed refresh records FAILED and never touches the
  previous valid policy;
- multiple warranties: one purchase, several items -> several policies;
- component warranties: parts/labor/compressor/battery/display/accessories;
- installation-date start + registration-dependent coverage via the API;
- region resolution priority (explicit > retailer > user > unknown).
"""

import hashlib
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.product import Manufacturer, Product, Retailer
from app.models.purchase import Purchase, PurchaseItem
from app.models.warranty import (
    DurationUnit,
    PolicyStatus,
    RetrievalStatus,
    SourceType,
    StartCondition,
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantySource,
    WarrantyTimeline,
)
from app.warranty.sources import refresh_source
from app.warranty.versioning import upsert_policy_versioned
from tests.api.test_warranty import _create_purchase, _seed
from tests.conftest import _register, auth_headers


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(Notification).delete()
    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(PurchaseItem).delete()
    db_session.query(Purchase).delete()
    db_session.query(Retailer).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _policies(db, name: str) -> list[WarrantyPolicy]:
    return list(db.scalars(select(WarrantyPolicy).where(WarrantyPolicy.policy_name == name)).all())


def _coverage_specs(duration: int) -> list[dict]:
    return [
        {"component": "FULL_UNIT", "duration_value": 12, "duration_unit": "MONTH"},
        {"component": "COMPRESSOR", "duration_value": duration, "duration_unit": "MONTH"},
    ]


def test_policy_versioning_preserves_history(db_session, app_client, authed_user):
    s = _seed(db_session)
    token = authed_user["access_token"]
    name = s["pol_ac"].policy_name

    # v1.0 is the imported fixture (COMPRESSOR 60 mo).
    v1 = _policies(db_session, name)[0]
    assert v1.policy_version == "1.0" and v1.status == PolicyStatus.ACTIVE

    # A purchase BEFORE the terms change maps to v1.0.
    p_old = _create_purchase(
        app_client,
        token,
        {"purchase_date": "2026-03-15", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    old_mapping = db_session.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == p_old["items"][0]["id"]))
    assert old_mapping is not None and old_mapping.warranty_policy_id == v1.id

    # Manufacturer updates terms: v1.1 effective 2026-07-01, COMPRESSOR 120 mo.
    out = upsert_policy_versioned(
        db_session,
        manufacturer_id=s["mfr"].id,
        policy_name=name,
        region="IN",
        policy_version="1.1",
        effective_from=date(2026, 7, 1),
        product_category=s["pol_ac"].product_category,
        product_model_pattern=s["pol_ac"].product_model_pattern,
        source_id=s["src"].id,
        coverages=_coverage_specs(120),
    )
    db_session.commit()
    assert out["created"] is True and out["superseded"] == ["1.0"]

    db_session.expire_all()
    v1 = db_session.get(WarrantyPolicy, v1.id)
    v11 = [p for p in _policies(db_session, name) if p.policy_version == "1.1"][0]
    # Previous version preserved: archived + window closed, NOT deleted.
    assert v1.status == PolicyStatus.ARCHIVED
    assert v1.effective_to == date(2026, 6, 30)
    assert v11.status == PolicyStatus.ACTIVE
    assert v11.effective_from == date(2026, 7, 1)

    # A purchase AFTER the change maps to v1.1.
    p_new = _create_purchase(
        app_client,
        token,
        {"purchase_date": "2026-08-20", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    new_mapping = db_session.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == p_new["items"][0]["id"]))
    assert new_mapping is not None and new_mapping.warranty_policy_id == v11.id

    # Historical mapping retained on v1.0 with its original window.
    old_mapping = db_session.get(WarrantyMapping, old_mapping.id)
    assert old_mapping.warranty_policy_id == v1.id
    old_tl = db_session.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == old_mapping.purchase_item_id)).all()
    comps = {t.component: t for t in old_tl}
    assert comps["COMPRESSOR"].duration_value == 60  # original v1.0 terms

    # Both versions are browsable (history is queryable).
    r = app_client.get("/api/v1/warranty/policies", headers=auth_headers(authed_user))
    names = [p["policy_version"] for p in r.json()["data"]["items"] if p["policy_name"] == name]
    assert names == ["1.0", "1.1"]


def _web_source(db, mfr) -> WarrantySource:
    src = WarrantySource(
        manufacturer_id=mfr.id,
        source_type=SourceType.OFFICIAL_WEB,
        source_url="https://example.com/warranty-terms",
        source_title="Sample official terms page (fixture)",
        trust_level=5,
        region="IN",
    )
    db.add(src)
    db.commit()
    return src


def test_failed_source_refresh_never_touches_policy(db_session, app_client, authed_user):
    s = _seed(db_session)
    token = authed_user["access_token"]
    src = _web_source(db_session, s["mfr"])
    assert src.retrieval_status == RetrievalStatus.SUCCESS

    def failing_fetch(url: str) -> str:
        raise RuntimeError("connection refused (simulated outage)")

    result = refresh_source(db_session, src.id, fetch=failing_fetch)
    assert result["status"] == "FAILED"
    assert result["policies_unchanged"] is True

    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    assert src.retrieval_status == RetrievalStatus.FAILED
    assert "connection refused" in (src.last_error or "")

    # The previous valid policy remains active and still maps purchases.
    policy = s["pol_ac"]
    db_session.expire_all()
    policy = db_session.get(WarrantyPolicy, policy.id)
    assert policy.status == PolicyStatus.ACTIVE
    p = _create_purchase(
        app_client,
        token,
        {"purchase_date": "2026-08-30", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    m = db_session.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == p["items"][0]["id"]))
    assert m is not None and m.warranty_policy_id == policy.id


def test_source_refresh_unchanged_then_versioned(db_session, app_client, authed_user):
    s = _seed(db_session)
    token = authed_user["access_token"]
    name = s["pol_ac"].policy_name
    src = _web_source(db_session, s["mfr"])

    content_v1 = "warranty terms 1.0: compressor 60 months"
    # Simulate a previously successful retrieval (content hash on file).
    src.document_hash = hashlib.sha256(content_v1.encode("utf-8")).hexdigest()
    db_session.commit()

    # 1) Refresh with identical content -> UNCHANGED, status SUCCESS.
    result = refresh_source(db_session, src.id, fetch=lambda url: content_v1)
    assert result["status"] == "UNCHANGED"
    db_session.expire_all()
    src = db_session.get(WarrantySource, src.id)
    assert src.retrieval_status == RetrievalStatus.SUCCESS
    assert src.last_error is None
    assert src.document_hash is not None

    # 2) Refresh with changed content + structured terms -> v1.1 applied,
    #    predecessor archived, historical mapping untouched.
    p_old = _create_purchase(
        app_client,
        token,
        {"purchase_date": "2026-03-15", "warranty_region": "IN", "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC"}]},
    )
    result = refresh_source(
        db_session,
        src.id,
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
    assert result["status"] == "CHANGED" and result["content_changed"] is True
    assert result["policies"][0]["created"] is True and result["policies"][0]["superseded"] == ["1.0"]

    db_session.expire_all()
    versions = {p.policy_version: p for p in _policies(db_session, name)}
    assert versions["1.0"].status == PolicyStatus.ARCHIVED
    assert versions["1.1"].status == PolicyStatus.ACTIVE
    # Historical mapping kept on the archived version.
    m = db_session.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == p_old["items"][0]["id"]))
    assert m is not None and m.warranty_policy_id == versions["1.0"].id


def test_source_refresh_changed_without_spec_flags_review(db_session):
    s = _seed(db_session)
    src = _web_source(db_session, s["mfr"])
    result = refresh_source(db_session, src.id, fetch=lambda url: "brand new terms")
    assert result["status"] == "CHANGED"
    assert result["review_required"] is True
    # Policy still valid — nothing deleted or archived.
    policy = db_session.get(WarrantyPolicy, s["pol_ac"].id)
    assert policy.status == PolicyStatus.ACTIVE


def test_refresh_endpoint_requires_admin(app_client, db_session, authed_user):
    s = _seed(db_session)
    r = app_client.post(
        f"/api/v1/warranty/sources/{s['src'].id}/refresh",
        headers=auth_headers(authed_user),
        json={},
    )
    assert r.status_code == 403


def test_multiple_warranties_one_purchase(db_session, app_client, authed_user):
    """One purchase, two items -> two different policies + timelines."""
    s = _seed(db_session)
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": "2026-08-30",
            "warranty_region": "IN",
            "items": [
                {"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"},
                {"product_id": s["tv"].id, "product_name": "QLED TV"},
            ],
        },
    )
    item_ids = [p["items"][0]["id"], p["items"][1]["id"]]
    mappings = {
        m.purchase_item_id: m
        for m in db_session.scalars(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id.in_(item_ids))).all()
    }
    assert set(mappings) == set(item_ids)
    assert mappings[item_ids[0]].warranty_policy_id == s["pol_ac"].id
    assert mappings[item_ids[1]].warranty_policy_id == s["pol_tv"].id
    assert mappings[item_ids[0]].warranty_policy_id != mappings[item_ids[1]].warranty_policy_id

    # Timelines per item/coverage.
    tl_ac = db_session.scalars(
        select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_ids[0])
    ).all()
    tl_tv = db_session.scalars(
        select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_ids[1])
    ).all()
    assert {t.component for t in tl_ac} == {"FULL_UNIT", "COMPRESSOR"}
    assert {t.component for t in tl_tv} == {"FULL_UNIT"}

    # The warranty endpoint shows both mapped items with their policies.
    r = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers=auth_headers(authed_user))
    items = r.json()["data"]["items"]
    assert len(items) == 2 and all(i["mapped"] for i in items)
    policy_names = {i["policy"]["policy_name"] for i in items}
    assert len(policy_names) == 2


def test_component_warranties_via_api(db_session, app_client, authed_user):
    """A laptop policy with parts/labor/battery/display/accessories coverage
    produces one timeline per component."""
    s = _seed(db_session)
    mfr = s["mfr"]
    laptop = Product(
        manufacturer_id=mfr.id,
        brand="QTech",
        canonical_name="QTech Notebook 14",
        category="Laptop",
        model_number="QN14",
    )
    db_session.add(laptop)
    db_session.flush()
    policy = WarrantyPolicy(
        manufacturer_id=mfr.id,
        product_category="Laptop",
        region="IN",
        policy_name="QTech Sample Laptop Warranty (fixture)",
        policy_version="1.0",
        effective_from=date(2024, 1, 1),
        source_id=s["src"].id,
        status=PolicyStatus.ACTIVE,
    )
    db_session.add(policy)
    db_session.flush()
    specs = {
        "PARTS": (12, "MONTH"),
        "LABOR": (12, "MONTH"),
        "BATTERY": (12, "MONTH"),
        "DISPLAY": (12, "MONTH"),
        "ACCESSORIES": (6, "MONTH"),
        "MOTHERBOARD": (24, "MONTH"),  # other component-specific
    }
    for comp, (val, unit) in specs.items():
        db_session.add(
            WarrantyCoverage(
                policy_id=policy.id,
                component=comp,
                duration_value=val,
                duration_unit=DurationUnit(unit),
                start_condition=StartCondition.PURCHASE_DATE,
            )
        )
    db_session.commit()

    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-30", "warranty_region": "IN", "items": [{"product_id": laptop.id, "product_name": "NOTEBOOK 14"}]},
    )
    item_id = p["items"][0]["id"]
    timelines = db_session.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_id)).all()
    comps = {t.component: t for t in timelines}
    assert set(comps) == set(specs)
    assert comps["ACCESSORIES"].expiry_date == date(2027, 2, 28)
    assert comps["MOTHERBOARD"].expiry_date == date(2028, 8, 30)
    assert comps["BATTERY"].expiry_date == date(2027, 8, 30)

    r = app_client.get(f"/api/v1/purchases/{p['id']}/warranty", headers=auth_headers(authed_user))
    item = r.json()["data"]["items"][0]
    assert {t["component"] for t in item["timelines"]} == set(specs)


def test_installation_date_start_via_api(db_session, app_client, authed_user):
    s = _seed(db_session)
    mfr = s["mfr"]
    fridge = Product(manufacturer_id=mfr.id, brand="QTech", canonical_name="QTech Inverter Refrigerator", category="Refrigerator")
    db_session.add(fridge)
    db_session.flush()
    policy = WarrantyPolicy(
        manufacturer_id=mfr.id,
        product_category="Refrigerator",
        region="IN",
        policy_name="QTech Sample Refrigerator Warranty (fixture)",
        policy_version="1.0",
        effective_from=date(2024, 1, 1),
        source_id=s["src"].id,
        status=PolicyStatus.ACTIVE,
    )
    db_session.add(policy)
    db_session.flush()
    # Compressor warranty starts from INSTALLATION_DATE (120 months).
    db_session.add(
        WarrantyCoverage(
            policy_id=policy.id,
            component="COMPRESSOR",
            duration_value=120,
            duration_unit=DurationUnit.MONTH,
            start_condition=StartCondition.INSTALLATION_DATE,
        )
    )
    db_session.commit()

    # 1) No installation date recorded -> UNKNOWN (never guessed).
    p1 = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-06-01", "warranty_region": "IN", "items": [{"product_id": fridge.id, "product_name": "REFRIGERATOR"}]},
    )
    t1 = db_session.scalar(
        select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == p1["items"][0]["id"])
    )
    assert t1 is not None and t1.status.value == "UNKNOWN" and t1.start_date is None

    # 2) User records the installation date via PATCH -> timeline computed
    #    from the installation date, not the purchase date.
    r = app_client.patch(
        f"/api/v1/purchases/{p1['id']}",
        headers=auth_headers(authed_user),
        json={"items": [{"product_id": fridge.id, "product_name": "REFRIGERATOR", "installation_date": "2026-07-01"}]},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    new_item_id = db_session.scalar(select(PurchaseItem.id).where(PurchaseItem.purchase_id == p1["id"]))
    t2 = db_session.scalar(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == new_item_id))
    assert t2 is not None
    assert t2.start_date == date(2026, 7, 1)
    assert t2.expiry_date == date(2036, 7, 1)
    assert t2.status.value == "ACTIVE"


def test_registration_dependent_coverage_via_api(db_session, app_client, authed_user):
    s = _seed(db_session)
    mfr = s["mfr"]
    washer = Product(manufacturer_id=mfr.id, brand="QTech", canonical_name="QTech Front Load Washer", category="Washing Machine")
    db_session.add(washer)
    db_session.flush()
    policy = WarrantyPolicy(
        manufacturer_id=mfr.id,
        product_category="Washing Machine",
        region="IN",
        policy_name="QTech Sample Washer Warranty (fixture)",
        policy_version="1.0",
        effective_from=date(2024, 1, 1),
        source_id=s["src"].id,
        status=PolicyStatus.ACTIVE,
    )
    db_session.add(policy)
    db_session.flush()
    # Motor coverage: 120 months, but ONLY after product registration.
    db_session.add(
        WarrantyCoverage(
            policy_id=policy.id,
            component="MOTOR",
            duration_value=120,
            duration_unit=DurationUnit.MONTH,
            start_condition=StartCondition.PURCHASE_DATE,
            registration_required=True,
        )
    )
    db_session.commit()

    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {"purchase_date": "2026-08-30", "warranty_region": "IN", "items": [{"product_id": washer.id, "product_name": "FRONT LOAD WASHER"}]},
    )
    item_id = p["items"][0]["id"]
    t = db_session.scalar(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_id))
    assert t is not None and t.status.value == "UNKNOWN"  # not registered yet

    # Record the registration date -> coverage clock starts from it.
    r = app_client.patch(
        f"/api/v1/purchases/{p['id']}",
        headers=auth_headers(authed_user),
        json={"items": [{"product_id": washer.id, "product_name": "FRONT LOAD WASHER", "registration_date": "2026-09-15"}]},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    item_id2 = db_session.scalar(select(PurchaseItem.id).where(PurchaseItem.purchase_id == p["id"]))
    t2 = db_session.scalar(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_id2))
    assert t2.start_date == date(2026, 9, 15)
    assert t2.expiry_date == date(2036, 9, 15)


def test_region_resolution_priority(db_session, app_client, registered_user):
    """explicit > retailer country > user account > unknown (FR-11a)."""
    h = auth_headers(registered_user)

    # 1) Retailer with a known country beats the user's account country.
    retailer = Retailer(name="SG Appliance Hub", name_key="sg-appliance-hub", country="SG")
    db_session.add(retailer)
    db_session.commit()
    r = app_client.post(
        "/api/v1/purchases",
        headers=h,
        json={"purchase_date": "2026-08-01", "retailer_id": retailer.id, "items": [{"product_name": "Widget"}]},
    )
    assert r.status_code == 201, r.text
    p = r.json()["data"]["purchase"]
    assert p["warranty_region"] == "SG"
    assert p["warranty_region_resolution_method"] == "RETAILER_COUNTRY"

    # 2) Explicit region beats the retailer.
    r = app_client.post(
        "/api/v1/purchases",
        headers=h,
        json={"purchase_date": "2026-08-01", "retailer_id": retailer.id, "warranty_region": "IN", "items": [{"product_name": "Widget"}]},
    )
    p2 = r.json()["data"]["purchase"]
    assert p2["warranty_region"] == "IN"
    assert p2["warranty_region_resolution_method"] == "USER_EXPLICIT"

    # 3) No explicit, no retailer -> user account country.
    r = app_client.post(
        "/api/v1/purchases",
        headers=h,
        json={"purchase_date": "2026-08-01", "items": [{"product_name": "Widget"}]},
    )
    p3 = r.json()["data"]["purchase"]
    assert p3["warranty_region"] == "IN"
    assert p3["warranty_region_resolution_method"] == "USER_ACCOUNT"

    # 4) Nobody has a country -> UNKNOWN (never a silent default).
    other = _register(app_client, f"nreg-{uuid.uuid4().hex[:8]}@example.com", country=None)
    r = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(other),
        json={"purchase_date": "2026-08-01", "items": [{"product_name": "Widget"}]},
    )
    p4 = r.json()["data"]["purchase"]
    assert p4["warranty_region"] is None
    assert p4["warranty_region_resolution_method"] == "UNRESOLVED"
