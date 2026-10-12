"""Warranty matching rules engine (TSD §14, SRS FR-09/FR-10).

Covers: exact-model / exact-SKU / category matching, region preference,
effective windows, manufacturer scope, source priority, no-match cases.
"""

from datetime import date

import pytest

from app.models.product import Manufacturer, Product
from app.models.warranty import (
    MatchMethod,
    PolicyStatus,
    SourceType,
    WarrantyCoverage,
    WarrantyPolicy,
    WarrantySource,
)
from app.warranty.rules.matching import find_warranty_policy

PURCHASE = date(2026, 8, 31)


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    from app.models.warranty import WarrantyMapping, WarrantyTimeline

    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _seed(db) -> dict:
    mfr_qtech = Manufacturer(name="QTech Electronics", name_key="qtechelectronics")
    mfr_samsung = Manufacturer(name="Samsung", name_key="samsung")
    db.add_all([mfr_qtech, mfr_samsung])
    db.flush()

    src_official = WarrantySource(
        manufacturer_id=mfr_samsung.id,
        source_type=SourceType.OFFICIAL_WEB,
        source_url="https://www.samsung.com/in/support/warranty/",
        source_title="Samsung India official",
        trust_level=5,
        region="IN",
    )
    src_curated = WarrantySource(
        manufacturer_id=mfr_qtech.id,
        source_type=SourceType.CURATED_DATASET,
        source_title="QTech curated (fixture)",
        trust_level=2,
        region="IN",
    )
    db.add_all([src_official, src_curated])
    db.flush()

    ac = Product(manufacturer_id=mfr_qtech.id, brand="QTech", canonical_name="QTech Inverter Split AC 1.5 Ton", category="Air Conditioning", model_number="AR15FSSY", sku="QT-AC-15T")
    tv = Product(manufacturer_id=mfr_samsung.id, brand="Samsung", canonical_name="Samsung 55-inch QLED 4K Smart TV", category="Television")
    db.add_all([ac, tv])
    db.flush()
    return {"qtech": mfr_qtech, "samsung": mfr_samsung, "src_official": src_official, "src_curated": src_curated, "ac": ac, "tv": tv}


def _policy(db, *, mfr, name, category=None, pattern=None, region="IN", from_="2026-01-01", to=None, source=None, status=PolicyStatus.ACTIVE):
    p = WarrantyPolicy(
        manufacturer_id=mfr.id,
        product_category=category,
        product_model_pattern=pattern,
        region=region,
        policy_name=name,
        policy_version="1.0",
        effective_from=date.fromisoformat(from_),
        effective_to=date.fromisoformat(to) if to else None,
        source_id=source.id if source else None,
        status=status,
    )
    db.add(p)
    db.flush()
    return p


def test_exact_model_outranks_category(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["samsung"], name="Samsung TV category", category="Television", source=s["src_official"])
    exact = _policy(db_session, mfr=s["samsung"], name="Samsung QN55 exact", category="Television", pattern="QN55*", source=s["src_official"])
    match, notes = find_warranty_policy(
        db_session, product=s["tv"], manufacturer=s["samsung"], region="IN", purchase_date=PURCHASE, item_model="QN55Q90"
    )
    assert match is not None
    assert match.method == MatchMethod.EXACT_MODEL
    assert match.policy.id == exact.id
    assert match.confidence >= 0.95


def test_exact_sku(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="QTech AC by SKU", category="Air Conditioning", pattern="QT-AC-*", source=s["src_curated"])
    match, _ = find_warranty_policy(
        db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE, item_sku="QT-AC-15T"
    )
    assert match is not None and match.method == MatchMethod.EXACT_SKU


def test_category_match_with_region(db_session):
    s = _seed(db_session)
    p = _policy(db_session, mfr=s["qtech"], name="QTech AC category", category="Air Conditioning", source=s["src_curated"])
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE)
    assert match is not None
    assert match.policy.id == p.id
    assert match.method == MatchMethod.CATALOG_MATCH
    assert match.confidence == 0.85  # exact-region policy keeps base confidence


def test_worldwide_policy_lower_confidence(db_session):
    s = _seed(db_session)
    p = _policy(db_session, mfr=s["qtech"], name="QTech AC worldwide", category="Air Conditioning", region=None, source=s["src_curated"])
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE)
    assert match is not None and match.policy.id == p.id
    assert match.confidence < 0.85  # worldwide fallback discounted


def test_region_specific_policy_not_applied_to_other_region(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="QTech AC IN only", category="Air Conditioning", region="IN")
    match, notes = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="US", purchase_date=PURCHASE)
    assert match is None
    assert notes  # honest explanation


def test_unknown_region_matches_only_worldwide(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="QTech AC IN only", category="Air Conditioning", region="IN")
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region=None, purchase_date=PURCHASE)
    assert match is None


def test_effective_window_excludes_earlier_purchase(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="QTech AC 2027 onwards", category="Air Conditioning", from_="2027-01-01")
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=date(2026, 8, 31))
    assert match is None
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=date(2027, 6, 1))
    assert match is not None


def test_archived_policy_ignored(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="QTech AC archived", category="Air Conditioning", status=PolicyStatus.ARCHIVED)
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE)
    assert match is None


def test_wrong_manufacturer_not_matched(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["samsung"], name="Samsung AC policy", category="Air Conditioning")
    match, _ = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE)
    assert match is None


def test_official_source_outranks_curated(db_session):
    s = _seed(db_session)
    # Two policies with identical scope; only sources differ.
    p_curated = _policy(db_session, mfr=s["samsung"], name="Samsung TV curated", category="Television", source=s["src_official"])
    # Give the curated-lower-trust source to the first policy, official to the second.
    s_curated_low = WarrantySource(
        manufacturer_id=s["samsung"].id,
        source_type=SourceType.CURATED_DATASET,
        source_title="curated low trust",
        trust_level=2,
        region="IN",
    )
    db_session.add(s_curated_low)
    db_session.flush()
    p_curated.source_id = s_curated_low.id
    p_official = _policy(db_session, mfr=s["samsung"], name="Samsung TV official", category="Television", source=s["src_official"])
    db_session.flush()
    match, notes = find_warranty_policy(db_session, product=s["tv"], manufacturer=s["samsung"], region="IN", purchase_date=PURCHASE)
    assert match is not None
    assert match.policy.id == p_official.id  # official web source wins the tie


def test_no_match_when_no_scope(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="QTech Laundry policy", category="Laundry")
    match, notes = find_warranty_policy(db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE)
    assert match is None
    assert notes


# --- Supporting-evidence stages (fuzzy / semantic) ---------------------------


def test_fuzzy_match_near_miss_model(db_session):
    s = _seed(db_session)
    # Pattern is a near-miss of the item model: fnmatch fails, but token
    # similarity is high -> FUZZY_MATCH as supporting evidence.
    p = _policy(db_session, mfr=s["qtech"], name="QTech AC Warranty (fixture)", pattern="AR15FSSXY")
    match, notes = find_warranty_policy(
        db_session,
        product=s["ac"],
        manufacturer=s["qtech"],
        region="IN",
        purchase_date=PURCHASE,
        item_model="AR15FSSY",
    )
    assert match is not None
    assert match.method == MatchMethod.FUZZY_MATCH
    assert match.policy.id == p.id
    assert 0.55 <= match.confidence < 0.85
    assert any("fuzzy" in e for e in match.evidence)


def test_semantic_match_supporting_evidence(db_session):
    _seed(db_session)
    mfr = Manufacturer(name="VoltHaus", name_key="volthaus")
    db_session.add(mfr)
    db_session.flush()
    product = Product(manufacturer_id=mfr.id, brand="VoltHaus", canonical_name="VoltHaus 300W Portable Power Station", category="Power Station")
    db_session.add(product)
    db_session.flush()
    p = _policy(db_session, mfr=mfr, name="VoltHaus Power Station Warranty", category=None, pattern=None)
    match, notes = find_warranty_policy(
        db_session, product=product, manufacturer=mfr, region="IN", purchase_date=PURCHASE
    )
    assert match is not None
    assert match.method == MatchMethod.SEMANTIC_MATCH
    assert match.policy.id == p.id
    assert 0.40 <= match.confidence < 0.70
    assert any("semantic" in e for e in match.evidence)


def test_below_threshold_support_does_not_match(db_session):
    s = _seed(db_session)
    _policy(db_session, mfr=s["qtech"], name="Unrelated Policy", pattern="ZZZZ-999", category=None)
    match, notes = find_warranty_policy(
        db_session, product=s["ac"], manufacturer=s["qtech"], region="IN", purchase_date=PURCHASE
    )
    assert match is None
    assert notes


def test_deterministic_rules_outrank_support_evidence(db_session):
    s = _seed(db_session)
    catalog = _policy(db_session, mfr=s["qtech"], name="QTech AC Catalog Policy", category="Air Conditioning")
    _policy(db_session, mfr=s["qtech"], name="QTech AC Near Pattern", pattern="AR15FSSXY")
    match, _ = find_warranty_policy(
        db_session,
        product=s["ac"],
        manufacturer=s["qtech"],
        region="IN",
        purchase_date=PURCHASE,
        item_model="AR15FSSY",
    )
    assert match is not None
    assert match.method == MatchMethod.CATALOG_MATCH
    assert match.policy.id == catalog.id


def test_fuzzy_outranks_semantic(db_session):
    s = _seed(db_session)
    fuzzy = _policy(db_session, mfr=s["qtech"], name="QTech AC Near Pattern", pattern="AR15FSSXY")
    _policy(db_session, mfr=s["qtech"], name="QTech Air Conditioning General", category=None, pattern=None)
    match, _ = find_warranty_policy(
        db_session,
        product=s["ac"],
        manufacturer=s["qtech"],
        region="IN",
        purchase_date=PURCHASE,
        item_model="AR15FSSY",
    )
    assert match is not None
    assert match.policy.id == fuzzy.id
    assert match.method == MatchMethod.FUZZY_MATCH
