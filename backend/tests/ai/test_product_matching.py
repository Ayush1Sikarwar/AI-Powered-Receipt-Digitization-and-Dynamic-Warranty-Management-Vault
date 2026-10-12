"""AI tests: Product Intelligence matching (SRS ML-04, TSD §28).

Covers: exact match, abbreviation, typo, ambiguous products, multiple
candidates, no match, false-positive prevention, manufacturer mismatch.
"""

import pytest

from app.ai.product.matching import (
    ProductQuery,
    decide,
    generate_candidates,
)
from app.ai.product.normalization import (
    normalize_manufacturer,
    normalize_model,
    normalize_retailer,
    normalize_sku,
)
from app.models.product import (
    Manufacturer,
    Product,
    ProductAlias,
    ProductMatchStatus,
    Retailer,
    RetailerCatalogEntry,
)

AUTO = 0.90
REVIEW = 0.70


@pytest.fixture(autouse=True)
def _clean_catalog(db_session):
    """Catalog rows persist in the test DB; clear them after each test."""
    yield
    db_session.query(RetailerCatalogEntry).delete()
    db_session.query(ProductAlias).delete()
    db_session.query(Product).delete()
    db_session.query(Retailer).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _seed(db) -> dict:
    """A small, explicit catalog for matching tests."""
    mfr_samsung = Manufacturer(name="Samsung", name_key="samsung")
    mfr_lg = Manufacturer(name="LG", name_key="lg")
    mfr_qtech = Manufacturer(name="QTech Electronics", name_key="qtechelectronics")
    db.add_all([mfr_samsung, mfr_lg, mfr_qtech])
    r_nortech = Retailer(name="Nortech Home Appliances", name_key="nortechhomeappliances", country="US")
    r_bluebox = Retailer(name="BlueBox Electronics", name_key="blueboxelectronics", country="US")
    db.add_all([r_nortech, r_bluebox])
    db.flush()

    tv_samsung = Product(
        manufacturer_id=mfr_samsung.id,
        brand="Samsung",
        canonical_name="Samsung 55-inch QLED Smart TV",
        category="Television",
        model_number="QN55Q90RA",
        sku="SAMS-QN55-90",
    )
    tv_lg = Product(
        manufacturer_id=mfr_lg.id,
        brand="LG",
        canonical_name="LG 55-inch OLED TV",
        category="Television",
        model_number="QN55Q90RA",  # same model code, different manufacturer
        sku="LG-OLED-55",
    )
    keyboard = Product(
        manufacturer_id=mfr_qtech.id,
        brand="QTech",
        canonical_name="QTech Mechanical Keyboard K870",
        category="Peripherals",
        model_number="K870",
        sku="BX-K870",
    )
    ac = Product(
        manufacturer_id=mfr_samsung.id,
        brand="Samsung",
        canonical_name="Samsung Inverter Split AC 1.5 Ton",
        category="Air Conditioning",
        model_number="AR15FSSY",
        sku="SAM-AC-15",
    )
    db.add_all([tv_samsung, tv_lg, keyboard, ac])
    db.flush()

    # Aliases: retailer abbreviations / receipt text (originals preserved).
    db.add_all(
        [
            ProductAlias(product_id=tv_samsung.id, alias_text="SAM 55IN QLED", alias_type="RETAILER_ABBREVIATION"),
            ProductAlias(product_id=keyboard.id, alias_text="MECH KEYBOARD K870", alias_type="RECEIPT_TEXT"),
            ProductAlias(product_id=ac.id, alias_text="SAMSUNG SPLIT AC 1.5T", alias_type="RECEIPT_TEXT"),
        ]
    )
    # Retailer catalog entries.
    db.add_all(
        [
            RetailerCatalogEntry(
                retailer_id=r_nortech.id,
                product_id=ac.id,
                catalog_sku="NT-AC15",
                catalog_model="AR15FSSY",
                catalog_name="Inverter Split AC 1.5 Ton",
            ),
            RetailerCatalogEntry(
                retailer_id=r_bluebox.id,
                product_id=keyboard.id,
                catalog_sku="BX-K870",
                catalog_name="Mechanical Keyboard K870",
            ),
        ]
    )
    db.commit()
    return {
        "samsung": mfr_samsung,
        "lg": mfr_lg,
        "qtech": mfr_qtech,
        "nortech": r_nortech,
        "bluebox": r_bluebox,
        "tv_samsung": tv_samsung,
        "tv_lg": tv_lg,
        "keyboard": keyboard,
        "ac": ac,
    }


# --- normalizers -------------------------------------------------------------

def test_normalize_sku_and_model():
    assert normalize_sku("BX-K870") == normalize_sku("bx.k870") == "BXK870"
    assert normalize_model("qt-55q90ra") == normalize_model("QT 55 Q90 RA") == "QT55Q90RA"
    assert normalize_sku(None) == ""


def test_retailer_abbreviation_normalization():
    assert normalize_retailer("BLUEBOX") == "BlueBox Electronics"
    assert normalize_retailer("Nortech Home Appliances") == "Nortech Home Appliances"
    assert normalize_retailer("Some Unknown Store") is None


def test_manufacturer_abbreviation_normalization():
    assert normalize_manufacturer("SAM") == "Samsung"
    assert normalize_manufacturer("samsung electronics") == "Samsung"
    assert normalize_manufacturer("LGE") == "LG"


# --- matching stages ---------------------------------------------------------

def test_exact_sku_match_auto(db_session):
    s = _seed(db_session)
    cands, notes = generate_candidates(db_session, ProductQuery(sku="sams.qn55.90", product_name="whatever"))
    status, top = decide(cands, AUTO, REVIEW)
    assert status == ProductMatchStatus.AUTO_MATCHED
    assert top is not None and top.product_id == s["tv_samsung"].id
    assert top.method.value == "EXACT_SKU"
    assert top.confidence >= AUTO
    assert any("SKU" in e for e in top.evidence)


def test_exact_model_match_with_manufacturer_constraint(db_session):
    s = _seed(db_session)
    # Model code exists for BOTH Samsung and LG — manufacturer resolves it.
    cands, notes = generate_candidates(
        db_session, ProductQuery(model_number="QN55Q90RA", manufacturer="SAM", product_name="55in tv")
    )
    ids = {c.product_id for c in cands}
    assert s["tv_lg"].id not in ids, "manufacturer mismatch candidate must be excluded"
    status, top = decide(cands, AUTO, REVIEW)
    assert status == ProductMatchStatus.AUTO_MATCHED
    assert top.product_id == s["tv_samsung"].id


def test_manufacturer_mismatch_excluded_no_match(db_session):
    s = _seed(db_session)
    # Query says Samsung, but the only exact-model hit with a KNOWN different
    # manufacturer (LG) is excluded → no safe candidate.
    cands, notes = generate_candidates(
        db_session, ProductQuery(model_number="LG-OLED-55", manufacturer="Samsung", product_name="x")
    )
    # 'LG-OLED-55' is the LG SKU, not a model — use the model with a wrong mfr:
    cands, notes = generate_candidates(
        db_session, ProductQuery(model_number="QN55Q90RA", manufacturer="LG", product_name="samsung tv")
    )
    ids = {c.product_id for c in cands}
    assert s["tv_samsung"].id not in ids
    assert any("manufacturer mismatch" in n or "excluded" in n for n in notes)


def test_retailer_catalog_match(db_session):
    s = _seed(db_session)
    cands, notes = generate_candidates(
        db_session, ProductQuery(retailer="NORTECH", product_name="Inverter Split AC 1.5 Ton", sku="NT-AC15")
    )
    status, top = decide(cands, AUTO, REVIEW)
    assert status == ProductMatchStatus.AUTO_MATCHED
    assert top.product_id == s["ac"].id
    assert any("catalog" in e.lower() or "SKU" in e for e in top.evidence)


def test_alias_match_abbreviation(db_session):
    s = _seed(db_session)
    # "SAM 55IN QLED" is a stored RECEIPT_TEXT alias of the Samsung TV.
    cands, notes = generate_candidates(db_session, ProductQuery(product_name="SAM 55IN QLED"))
    top = next(c for c in cands if c.product_id == s["tv_samsung"].id)
    assert top.method.value == "ALIAS_MATCH"
    assert top.confidence >= 0.88


def test_typo_fuzzy_match_review_not_auto(db_session):
    s = _seed(db_session)
    # One-char typo in the product name → fuzzy, below auto-accept.
    cands, notes = generate_candidates(
        db_session, ProductQuery(product_name="QTech Mechanical Keyboad K870")
    )
    assert cands, "a fuzzy candidate is expected for a close typo"
    top = cands[0]
    assert top.product_id == s["keyboard"].id
    assert top.method.value == "FUZZY_MATCH"
    status, linked = decide(cands, AUTO, REVIEW)
    assert status == ProductMatchStatus.REVIEW_REQUIRED
    assert linked is None, "a typo match must never auto-link"


def test_ambiguous_products_review_required(db_session):
    s = _seed(db_session)
    # Same model code, two manufacturers, NO manufacturer in the query.
    cands, notes = generate_candidates(db_session, ProductQuery(model_number="QN55Q90RA"))
    ids = {c.product_id for c in cands}
    assert s["tv_samsung"].id in ids and s["tv_lg"].id in ids
    status, linked = decide(cands, AUTO, REVIEW)
    assert status == ProductMatchStatus.REVIEW_REQUIRED
    assert linked is None, "ambiguous candidates must not auto-link"
    assert len(cands) >= 2


def test_multiple_candidates_ranked(db_session):
    s = _seed(db_session)
    # No manufacturer in the query: the shared model code yields multiple
    # candidates, all of which must be returned and ranked.
    cands, notes = generate_candidates(
        db_session,
        ProductQuery(product_name="Samsung 55 inch QLED Smart TV", model_number="QN55Q90RA"),
    )
    ids = {c.product_id for c in cands}
    assert s["tv_samsung"].id in ids and s["tv_lg"].id in ids
    confs = [c.confidence for c in cands]
    assert confs == sorted(confs, reverse=True), "candidates must be ranked by confidence"
    # With the manufacturer provided, the mismatched one drops out.
    cands2, _ = generate_candidates(
        db_session,
        ProductQuery(product_name="Samsung 55 inch QLED Smart TV", model_number="QN55Q90RA", manufacturer="Samsung"),
    )
    assert {c.product_id for c in cands2} == {s["tv_samsung"].id}


def test_semantic_synonym_match(db_session):
    s = _seed(db_session)
    # "television" vs product "…Smart TV" → semantic synonym candidate at
    # LOW confidence (never auto-links); manufacturer constraint drops the
    # LG twin.
    cands, notes = generate_candidates(
        db_session, ProductQuery(product_name="Samsung 55 inch television", manufacturer="Samsung")
    )
    assert cands, "semantic synonym overlap must produce a candidate"
    top = cands[0]
    assert top.product_id == s["tv_samsung"].id
    assert top.method.value == "SEMANTIC_MATCH"
    assert top.confidence < AUTO
    status, linked = decide(cands, AUTO, REVIEW)
    assert linked is None
    assert status == ProductMatchStatus.REVIEW_REQUIRED


def test_no_match_unrelated(db_session):
    _seed(db_session)
    cands, notes = generate_candidates(
        db_session, ProductQuery(product_name="Ceramic Espresso Grinder X1", sku="GRND-X1")
    )
    status, linked = decide(cands, AUTO, REVIEW)
    assert status == ProductMatchStatus.NO_MATCH
    assert linked is None


def test_false_positive_prevention_low_confidence_never_links(db_session):
    _seed(db_session)
    # Vaguely related text: some token overlap, no exact/fuzzy/semantic
    # confidence that clears auto-accept.
    cands, notes = generate_candidates(
        db_session, ProductQuery(product_name="Mechanical Keyboard Accessories", retailer="BlueBox Electronics")
    )
    for c in cands:
        assert c.confidence < AUTO, f"weak candidate {c.product_name} at {c.confidence} must not reach auto"
    status, linked = decide(cands, AUTO, REVIEW)
    assert linked is None
    assert status in (ProductMatchStatus.NO_MATCH, ProductMatchStatus.REVIEW_REQUIRED)


def test_low_confidence_candidate_not_auto_even_if_unique(db_session):
    _seed(db_session)
    cands, notes = generate_candidates(db_session, ProductQuery(product_name="QTech Mechanical Keyboad K870"))
    # Unique top candidate, but confidence is below auto-accept.
    status, linked = decide(cands, AUTO, REVIEW)
    assert linked is None
    assert status == ProductMatchStatus.REVIEW_REQUIRED


def test_no_candidates_at_all(db_session):
    # Empty catalog → no candidates, NO_MATCH.
    status, linked = decide([], AUTO, REVIEW)
    assert status == ProductMatchStatus.NO_MATCH
    assert linked is None
