"""API tests: purchases + search (TSD §19, SRS FR-14, TSD §55)."""

import uuid
from unittest.mock import patch

from tests.conftest import _register, auth_headers


def test_create_get_patch_purchase_flow(app_client, registered_user):
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [
            {"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}
        ],
    }
    created = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert created.status_code == 201, created.text
    purchase = created.json()["data"]["purchase"]
    # Region resolution: no explicit region, no retailer -> user account country.
    assert purchase["warranty_region"] == "IN"
    assert purchase["warranty_region_resolution_method"] == "USER_ACCOUNT"
    assert purchase["items"][0]["model_number"] == "QE55Q60"

    got = app_client.get(f"/api/v1/purchases/{purchase['id']}", headers=headers)
    assert got.status_code == 200

    patched = app_client.patch(
        f"/api/v1/purchases/{purchase['id']}",
        json={"purchase_date": "2026-08-15", "warranty_region": "US"},
        headers=headers,
    )
    assert patched.status_code == 200
    updated = patched.json()["data"]["purchase"]
    assert updated["purchase_date"] == "2026-08-15"
    assert updated["warranty_region"] == "US"
    assert updated["warranty_region_resolution_method"] == "USER_EXPLICIT"

    listing = app_client.get("/api/v1/purchases", headers=headers)
    assert listing.json()["data"]["total"] >= 1


def test_purchase_date_validation(app_client, registered_user):
    resp = app_client.post(
        "/api/v1/purchases",
        json={"purchase_date": "08/01/2026"},
        headers=auth_headers(registered_user),
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


def test_purchase_isolation_idor(app_client, registered_user):
    headers = auth_headers(registered_user)
    created = app_client.post("/api/v1/purchases", json={"purchase_date": "2026-08-01"}, headers=headers)
    pid = created.json()["data"]["purchase"]["id"]

    other = _register(app_client, f"idor-{uuid.uuid4().hex[:6]}@example.com")
    assert app_client.get(f"/api/v1/purchases/{pid}", headers=auth_headers(other)).status_code == 404
    assert app_client.delete(f"/api/v1/purchases/{pid}", headers=auth_headers(other)).status_code == 404


def test_search_by_text_manufacturer_date(app_client, registered_user, db_session):
    """Free-text + structured search (TSD §55)."""
    # Seed a product/manufacturer so manufacturer filter has data.
    from app.models.product import Manufacturer, Product
    from app.models.purchase import Purchase, PurchaseItem, PurchaseVerification

    headers = auth_headers(registered_user)
    uid = registered_user["user"]["id"]

    mfr = Manufacturer(name="Nortech", name_key="nortech")
    db_session.add(mfr)
    db_session.commit()
    product = Product(manufacturer_id=mfr.id, brand="Nortech", canonical_name="Nortech X1", model_number="X1-200")
    db_session.add(product)
    db_session.commit()

    p = Purchase(user_id=uid, purchase_date="2026-07-10", verification_status=PurchaseVerification.DRAFT)
    db_session.add(p)
    db_session.commit()
    db_session.add(
        PurchaseItem(
            purchase_id=p.id,
            product_id=product.id,
            product_name_raw="Nortech X1",
            model_number_raw="X1-200",
            sku_raw="SKU-1",
        )
    )
    db_session.commit()

    # text search
    q = app_client.get("/api/v1/purchases?q=X1-200", headers=headers)
    assert q.json()["data"]["total"] >= 1
    # manufacturer filter
    q2 = app_client.get("/api/v1/purchases?manufacturer=nortech", headers=headers)
    assert q2.json()["data"]["total"] >= 1
    # date range
    q3 = app_client.get("/api/v1/purchases?date_from=2026-07-01&date_to=2026-07-31", headers=headers)
    assert q3.json()["data"]["total"] >= 1
    # date range that excludes it
    q4 = app_client.get("/api/v1/purchases?date_from=2025-01-01&date_to=2025-12-31", headers=headers)
    assert all(i["purchase_date"] != "2026-07-10" for i in q4.json()["data"]["items"])


def test_delete_purchase_hold_with_open_claim(app_client, registered_user, db_session):
    from app.models.claim import Claim, ClaimStatus

    headers = auth_headers(registered_user)
    created = app_client.post("/api/v1/purchases", json={"purchase_date": "2026-08-01"}, headers=headers)
    pid = created.json()["data"]["purchase"]["id"]
    db_session.add(Claim(user_id=registered_user["user"]["id"], purchase_id=pid, status=ClaimStatus.DRAFT))
    db_session.commit()

    resp = app_client.delete(f"/api/v1/purchases/{pid}", headers=headers)
    assert resp.json()["data"]["deleted"] is False
    assert resp.json()["data"]["status"] == "HOLD"


def test_delete_purchase_ok_without_claims(app_client, registered_user):
    headers = auth_headers(registered_user)
    created = app_client.post("/api/v1/purchases", json={"purchase_date": "2026-08-01"}, headers=headers)
    pid = created.json()["data"]["purchase"]["id"]
    resp = app_client.delete(f"/api/v1/purchases/{pid}", headers=headers)
    assert resp.json()["data"]["deleted"] is True
    assert app_client.get(f"/api/v1/purchases/{pid}", headers=headers).status_code == 404


def test_filter_by_document_id(app_client, registered_user):
    """Backward-compatible list filter used by the receipt->purchase prefill UI."""
    from tests.api.test_documents import upload

    headers = auth_headers(registered_user)
    resp = upload(app_client, registered_user, filename="prefill.png")
    doc_id = resp.json()["data"]["document"]["id"]

    a = app_client.post(
        "/api/v1/purchases", json={"purchase_date": "2026-01-01", "total": 10.0}, headers=headers
    )
    assert a.status_code == 201, a.text
    b = app_client.post(
        "/api/v1/purchases",
        json={"purchase_date": "2026-02-02", "total": 20.0, "document_id": doc_id},
        headers=headers,
    )
    assert b.status_code == 201, b.text

    filtered = app_client.get(f"/api/v1/purchases?document_id={doc_id}", headers=headers)
    assert filtered.status_code == 200
    body = filtered.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == doc_id

    unfiltered = app_client.get("/api/v1/purchases", headers=headers)
    assert unfiltered.json()["data"]["total"] >= 2


def test_create_purchase_normal_persists(app_client, registered_user):
    """Normal purchase creation: purchase is persisted and retrievable."""
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [{"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}],
    }
    resp = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    purchase = resp.json()["data"]["purchase"]
    assert purchase["id"] is not None
    assert purchase["purchase_date"] == "2026-08-01"
    assert purchase["total"] == 1299.99
    assert len(purchase["items"]) == 1

    # Purchase is retrievable (persisted across the session boundary).
    got = app_client.get(f"/api/v1/purchases/{purchase['id']}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["purchase"]["id"] == purchase["id"]


def test_create_purchase_alert_failure_does_not_break_purchase(app_client, registered_user):
    """Alert scheduling failure must not roll back the purchase."""
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [{"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}],
    }
    with patch(
        "app.services.notification_service.schedule_warranty_reminders",
        side_effect=Exception("alert scheduling boom"),
    ):
        resp = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    purchase = resp.json()["data"]["purchase"]

    # Purchase is still retrievable despite alert failure.
    got = app_client.get(f"/api/v1/purchases/{purchase['id']}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["purchase"]["id"] == purchase["id"]


def test_create_purchase_warranty_failure_does_not_break_purchase(app_client, registered_user):
    """Warranty sync failure must not roll back the purchase.

    Warranty mapping/timeline changes are rolled back independently; the
    purchase itself remains committed.
    """
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [{"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}],
    }
    with patch(
        "app.warranty.service.sync_purchase_warranties",
        side_effect=Exception("warranty sync boom"),
    ):
        resp = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    purchase = resp.json()["data"]["purchase"]

    # Purchase is still retrievable despite warranty failure.
    got = app_client.get(f"/api/v1/purchases/{purchase['id']}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["purchase"]["id"] == purchase["id"]


def test_create_purchase_core_validation_failure(app_client, registered_user):
    """Core purchase validation failure fails the purchase with no record persisted."""
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "retailer_id": 999999,  # non-existent retailer
        "items": [{"product_name": "TV", "quantity": 1, "unit_price": 10.0}],
    }
    resp = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "VALIDATION_ERROR"

    # No purchase was persisted.
    listing = app_client.get("/api/v1/purchases", headers=headers)
    assert listing.json()["data"]["total"] == 0


def test_update_purchase_persists_changes(app_client, registered_user):
    """A successful purchase update persists all changed fields."""
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [{"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}],
    }
    created = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert created.status_code == 201, created.text
    purchase_id = created.json()["data"]["purchase"]["id"]

    update = app_client.patch(
        f"/api/v1/purchases/{purchase_id}",
        json={"invoice_number": "INV-2026-001", "currency": "EUR", "total": 1499.99},
        headers=headers,
    )
    assert update.status_code == 200, update.text
    data = update.json()["data"]["purchase"]
    assert data["invoice_number"] == "INV-2026-001"
    assert data["currency"] == "EUR"
    assert data["total"] == 1499.99

    # Changes persist across the session boundary.
    got = app_client.get(f"/api/v1/purchases/{purchase_id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["purchase"]["invoice_number"] == "INV-2026-001"
    assert got.json()["data"]["purchase"]["currency"] == "EUR"
    assert got.json()["data"]["purchase"]["total"] == 1499.99


def test_update_purchase_alert_failure_does_not_break_update(app_client, registered_user):
    """Alert scheduling failure must not roll back a committed purchase update."""
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [{"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}],
    }
    created = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert created.status_code == 201, created.text
    purchase_id = created.json()["data"]["purchase"]["id"]

    with patch(
        "app.services.notification_service.schedule_warranty_reminders",
        side_effect=Exception("alert scheduling boom"),
    ):
        update = app_client.patch(
            f"/api/v1/purchases/{purchase_id}",
            json={"invoice_number": "INV-ALERT-FAIL"},
            headers=headers,
        )
    assert update.status_code == 200, update.text
    assert update.json()["data"]["purchase"]["invoice_number"] == "INV-ALERT-FAIL"

    # Purchase update is still retrievable despite alert failure.
    got = app_client.get(f"/api/v1/purchases/{purchase_id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["purchase"]["invoice_number"] == "INV-ALERT-FAIL"


def test_update_purchase_warranty_failure_does_not_break_update(app_client, registered_user):
    """Warranty sync failure must not roll back a committed purchase update.

    Warranty mapping/timeline changes are rolled back independently; the
    purchase update itself remains committed.
    """
    headers = auth_headers(registered_user)
    body = {
        "purchase_date": "2026-08-01",
        "currency": "usd",
        "total": 1299.99,
        "items": [{"product_name": "55in LED TV", "model_number": "QE55Q60", "quantity": 1, "unit_price": 1299.99}],
    }
    created = app_client.post("/api/v1/purchases", json=body, headers=headers)
    assert created.status_code == 201, created.text
    purchase_id = created.json()["data"]["purchase"]["id"]

    with patch(
        "app.warranty.service.sync_purchase_warranties",
        side_effect=Exception("warranty sync boom"),
    ):
        update = app_client.patch(
            f"/api/v1/purchases/{purchase_id}",
            json={"invoice_number": "INV-WARRANTY-FAIL"},
            headers=headers,
        )
    assert update.status_code == 200, update.text
    assert update.json()["data"]["purchase"]["invoice_number"] == "INV-WARRANTY-FAIL"

    # Purchase update is still retrievable despite warranty failure.
    got = app_client.get(f"/api/v1/purchases/{purchase_id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["purchase"]["invoice_number"] == "INV-WARRANTY-FAIL"
