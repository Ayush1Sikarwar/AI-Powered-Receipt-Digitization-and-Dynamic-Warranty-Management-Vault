"""API tests: Product Intelligence (SRS ML-04/ML-06/ML-07, TSD §20/§28).

Covers the document match lifecycle (auto / review / confirm / reject /
create-from-receipt), TSD §20 ad-hoc resolution output, preservation of
original receipt values, cross-user isolation, and purchase linkage.
"""

import pytest
from sqlalchemy import select

from app.models.product import (
    Manufacturer,
    Product,
    ProductAlias,
    Retailer,
    RetailerCatalogEntry,
)
from tests.api.test_documents import upload
from tests.api.test_fields import _receipt_png
from tests.conftest import auth_headers

AUTO = 0.90


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(RetailerCatalogEntry).delete()
    db_session.query(ProductAlias).delete()
    db_session.query(Product).delete()
    db_session.query(Retailer).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _seed(db) -> dict:
    mfr = Manufacturer(name="QTech Electronics", name_key="qtechelectronics")
    db.add(mfr)
    r_bluebox = Retailer(name="BlueBox Electronics", name_key="blueboxelectronics", country="US")
    db.add(r_bluebox)
    db.flush()
    keyboard = Product(
        manufacturer_id=mfr.id,
        brand="QTech",
        canonical_name="QTech Mechanical Keyboard K870",
        category="Peripherals",
        model_number="K870",
        sku="QT-KB-870",
    )
    tv = Product(
        manufacturer_id=mfr.id,
        brand="QTech",
        canonical_name="QTech 55-inch 4K QLED Smart TV",
        category="Television",
        model_number="QT55Q90RA",
        sku="QT55Q9-4K",
    )
    db.add_all([keyboard, tv])
    db.flush()
    db.add(
        ProductAlias(
            product_id=keyboard.id,
            alias_text="MECH KEYBOARD K870",
            alias_type="RECEIPT_TEXT",
            source="test",
        )
    )
    db.add(
        RetailerCatalogEntry(
            retailer_id=r_bluebox.id,
            product_id=keyboard.id,
            catalog_sku="BX-K870",
            catalog_model="K870",
            catalog_name="Mechanical Keyboard K870",
            source="test",
        )
    )
    db.commit()
    return {"keyboard": keyboard, "tv": tv}


def _processed_doc(app_client, user, lines) -> int:
    from app.ai.pipeline import run_document_pipeline
    from app.db.session import SessionLocal
    from app.models.document import Document

    resp = upload(app_client, user, filename="pi.png", data=_receipt_png(lines))
    doc_id = resp.json()["data"]["document"]["id"]
    db = SessionLocal()
    try:
        run_document_pipeline(db, db.get(Document, doc_id))
        db.commit()
    finally:
        db.close()
    return doc_id


KEYBOARD_RECEIPT = (
    "BLUEBOX ELECTRONICS",
    "Date: 2026-08-05",
    "Receipt No: BX-1001",
    "Mechanical Keyboard K870 $189.00",
    "SKU: BX-K870",
    "Qty: 1",
    "Total: $189.00",
)


# --- catalog API (TSD §20) ---------------------------------------------------

def test_products_search_and_get(db_session, authed_user):
    s = _seed(db_session)
    client, h = authed_user["client"], auth_headers(authed_user)
    r = client.get("/api/v1/products/search", params={"q": "keyboard"}, headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert any(i["canonical_name"] == "QTech Mechanical Keyboard K870" for i in items)
    r = client.get(f"/api/v1/products/{s['keyboard'].id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["sku"] == "QT-KB-870"
    assert any(a["alias_text"] == "MECH KEYBOARD K870" for a in body["aliases"])
    # unauthenticated
    assert client.get(f"/api/v1/products/{s['keyboard'].id}").status_code == 401


def test_adhoc_resolve_output_shape(db_session, authed_user):
    s = _seed(db_session)
    client, h = authed_user["client"], auth_headers(authed_user)
    r = client.post(
        "/api/v1/products/resolve",
        json={"sku": "BX-K870", "retailer": "BlueBox", "description": "keyboard"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    for key in ("candidate_product", "confidence", "match_method", "supporting_evidence", "requires_confirmation"):
        assert key in body, f"TSD §20 output key {key} missing"
    assert body["candidate_product"]["product_id"] == s["keyboard"].id
    assert body["match_method"] == "EXACT_SKU"
    assert body["requires_confirmation"] is False
    assert body["confidence"] >= AUTO


# --- document match lifecycle ------------------------------------------------

def test_document_match_auto_recorded(db_session, authed_user):
    s = _seed(db_session)
    doc_id = _processed_doc(authed_user["client"], authed_user, KEYBOARD_RECEIPT)
    h = auth_headers(authed_user)
    r = authed_user["client"].get(f"/api/v1/documents/{doc_id}/product-match", headers=h)
    assert r.status_code == 200, r.text
    m = r.json()["data"]["match"]
    assert m["status"] == "AUTO_MATCHED"
    assert m["product_id"] == s["keyboard"].id
    assert m["match_method"] == "EXACT_SKU"
    assert m["match_confidence"] >= AUTO
    # Preservation contract: original + normalized values + candidates + evidence.
    assert m["product_name_raw"] == "Mechanical Keyboard K870"
    assert m["sku_raw"] == "BX-K870"
    assert m["sku_normalized"] == "BXK870"
    assert m["retailer_raw"] is not None
    assert m["candidates"], "candidate list must be persisted"
    assert m["evidence"], "evidence must be persisted"
    assert m["candidates"][0]["evidence"]


def test_low_confidence_never_linked_document_review(db_session, authed_user):
    s = _seed(db_session)
    # Typo + no SKU/model → fuzzy candidate only → REVIEW_REQUIRED, unlinked.
    lines = (
        "BLUEBOX ELECTRONICS",
        "Date: 2026-08-05",
        "Mechanical Keyboad K870 $189.00",
        "Total: $189.00",
    )
    doc_id = _processed_doc(authed_user["client"], authed_user, lines)
    h = auth_headers(authed_user)
    r = authed_user["client"].get(f"/api/v1/documents/{doc_id}/product-match", headers=h)
    m = r.json()["data"]["match"]
    assert m["status"] == "REVIEW_REQUIRED"
    assert m["product_id"] is None, "a low-confidence candidate must NOT become the product"
    assert m["candidates"], "candidates must be presented for user confirmation"
    top = m["candidates"][0]
    assert top["product_id"] == s["keyboard"].id
    assert top["method"] == "FUZZY_MATCH"
    assert top["confidence"] < AUTO
    # document routed to review
    d = authed_user["client"].get(f"/api/v1/documents/{doc_id}", headers=h).json()["data"]
    assert d["status"] == "REVIEW_REQUIRED"


def test_confirm_preserves_originals_and_links(db_session, authed_user):
    s = _seed(db_session)
    lines = (
        "BLUEBOX ELECTRONICS",
        "Date: 2026-08-05",
        "Mechanical Keyboad K870 $189.00",
        "Total: $189.00",
    )
    doc_id = _processed_doc(authed_user["client"], authed_user, lines)
    h = auth_headers(authed_user)
    client = authed_user["client"]
    m0 = client.get(f"/api/v1/documents/{doc_id}/product-match", headers=h).json()["data"]["match"]
    candidate_id = m0["candidates"][0]["product_id"]
    ai_product_value = client.get(f"/api/v1/documents/{doc_id}/fields", headers=h).json()["data"]["items"]
    ai_val = next((f for f in ai_product_value if f["field_name"] == "product_name"), None)
    assert ai_val is not None

    r = client.post(
        f"/api/v1/documents/{doc_id}/product-match",
        json={"action": "confirm", "product_id": candidate_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    m1 = r.json()["data"]["match"]
    assert m1["status"] == "CONFIRMED"
    assert m1["product_id"] == s["keyboard"].id
    assert m1["confirmed_by"] is not None
    # Original receipt value preserved on the match record.
    assert m1["product_name_raw"] == "Mechanical Keyboad K870"
    # The extracted field keeps the AI original in original_value.
    fields = client.get(f"/api/v1/documents/{doc_id}/fields", headers=h).json()["data"]["items"]
    p = next(f for f in fields if f["field_name"] == "product_name")
    assert p["verification_status"] == "USER_CONFIRMED"
    assert p["original_value"] == ai_val["value"], "AI original must be preserved on confirmation"
    assert p["value"] == "QTech Mechanical Keyboard K870"


def test_reject_flow(db_session, authed_user):
    _seed(db_session)
    lines = (
        "BLUEBOX ELECTRONICS",
        "Date: 2026-08-05",
        "Mechanical Keyboad K870 $189.00",
        "Total: $189.00",
    )
    doc_id = _processed_doc(authed_user["client"], authed_user, lines)
    h = auth_headers(authed_user)
    r = authed_user["client"].post(
        f"/api/v1/documents/{doc_id}/product-match", json={"action": "reject"}, headers=h
    )
    assert r.status_code == 200, r.text
    m = r.json()["data"]["match"]
    assert m["status"] == "REJECTED"
    assert m["product_id"] is None


def test_create_product_from_receipt(db_session, authed_user):
    _seed(db_session)
    lines = (
        "BLUEBOX ELECTRONICS",
        "Date: 2026-08-05",
        "Ceramic Espresso Grinder X1 $59.99",
        "Total: $59.99",
    )
    doc_id = _processed_doc(authed_user["client"], authed_user, lines)
    h = auth_headers(authed_user)
    client = authed_user["client"]
    m0 = client.get(f"/api/v1/documents/{doc_id}/product-match", headers=h).json()["data"]["match"]
    assert m0["status"] in ("NO_MATCH", "REVIEW_REQUIRED")
    r = client.post(
        f"/api/v1/documents/{doc_id}/product-match", json={"action": "create_confirm"}, headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["match"]["status"] == "CONFIRMED"
    assert body["product"]["canonical_name"].startswith("Ceramic Espresso Grinder")
    # Original receipt value preserved on the match record (SRS ML-06).
    assert body["match"]["product_name_raw"] == "Ceramic Espresso Grinder X1"
    prod = db_session.scalar(select(Product).where(Product.canonical_name.ilike("%Espresso%")))
    assert prod is not None and prod.id == body["match"]["product_id"]


def test_cross_user_isolation(authed_user, app_client):
    from tests.conftest import _register

    s_user = _seed_for_doc(app_client, authed_user)
    other = _register(app_client, f"x{__import__('uuid').uuid4().hex[:8]}@example.com")
    oh = auth_headers(other)
    # foreign document → 404 on both endpoints
    r = app_client.get(f"/api/v1/documents/{s_user}/product-match", headers=oh)
    assert r.status_code == 404
    r = app_client.post(
        f"/api/v1/documents/{s_user}/product-match", json={"action": "reject"}, headers=oh
    )
    assert r.status_code == 404


def _seed_for_doc(app_client, user) -> int:
    db = None
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        _seed(db)
    finally:
        db.close()
    return _processed_doc(app_client, user, KEYBOARD_RECEIPT)


def test_purchase_uses_confirmed_product(db_session, authed_user):
    s = _seed(db_session)
    doc_id = _processed_doc(authed_user["client"], authed_user, KEYBOARD_RECEIPT)
    h = auth_headers(authed_user)
    client = authed_user["client"]
    # confirm the auto-matched product explicitly
    r = client.post(
        f"/api/v1/documents/{doc_id}/product-match",
        json={"action": "confirm"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # create a purchase from the document WITHOUT sending a product_id
    r = client.post(
        "/api/v1/purchases",
        json={
            "document_id": doc_id,
            "purchase_date": "2026-08-05",
            "currency": "USD",
            "total": 189.00,
            "items": [{"product_name": "Mechanical Keyboard K870", "quantity": 1, "unit_price": 189.00}],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    purchase = r.json()["data"]["purchase"]
    items = purchase.get("items") or []
    assert items and items[0].get("product_id") == s["keyboard"].id
