"""API tests: extracted-field review + correction + classification (FR-07, FR-17, TSD §18).

Flow: upload -> run the pipeline in-process (real OCR) -> GET fields ->
PATCH confirm/correct/reject -> classification endpoint.
"""

from tests.api.test_documents import upload
from tests.conftest import auth_headers


def _receipt_png(lines, size=(640, 900), font_size=28) -> bytes:
    """Render a receipt with a real TrueType font so OCR can actually read it
    (PIL's default bitmap font is below OCR quality)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
    ]

    font = None
    for font_path in font_candidates:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except OSError:
            continue

    if font is None:
        raise RuntimeError("No suitable TrueType font found for OCR test fixture")

    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    y = 40
    for line in lines:
        if line:
            d.text((40, y), line, fill="black", font=font)
        y += font_size + 22
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


RECEIPT_LINES = (
    "NORTECH HOME APPLIANCES",
    "Date: 2026-08-01",
    "Receipt No: R10245",
    "Nortech 55in 4K QLED TV x1 $1299.99",
    "Total: $1403.98",
)


def _processed_doc(app_client, user) -> int:
    """Upload a receipt and run the pipeline in-process; return doc id."""
    from app.ai.pipeline import run_document_pipeline
    from app.db.session import SessionLocal
    from app.models.document import Document

    resp = upload(app_client, user, filename="review.png", data=_receipt_png(RECEIPT_LINES))
    doc_id = resp.json()["data"]["document"]["id"]
    db = SessionLocal()
    try:
        run_document_pipeline(db, db.get(Document, doc_id))
    finally:
        db.close()
    return doc_id


def test_get_fields_returns_extracted_with_provenance(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    resp = app_client.get(f"/api/v1/documents/{doc_id}/fields", headers=auth_headers(registered_user))
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items, "pipeline should extract at least one field"

    by_name = {f["field_name"]: f for f in items}
    # A receipt date/total are reliably extractable from this fixture.
    assert "purchase_date" in by_name or "total_price" in by_name

    for f in items:
        assert f["model_name"]
        assert f["model_version"]
        assert f["source_region"]
        assert f["extraction_method"]
        assert f["verification_status"] == "AI_EXTRACTED"
        assert isinstance(f["is_critical"], bool)
        assert isinstance(f["requires_review"], bool)


def test_confirm_field_sets_user_confirmed(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)
    items = app_client.get(f"/api/v1/documents/{doc_id}/fields", headers=headers).json()["data"]["items"]
    field = next(f for f in items if f["value"])

    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": field["field_name"], "action": "confirm"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["field"]["verification_status"] == "USER_CONFIRMED"
    assert body["document_status"] in {"COMPLETED", "REVIEW_REQUIRED"}


def test_correct_field_replaces_value_and_preserves_ai_original(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)

    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": "invoice_number", "action": "correct", "value": "R-99901"},
    )
    assert resp.status_code == 200
    f = resp.json()["data"]["field"]
    assert f["verification_status"] == "USER_CORRECTED"
    assert f["value"] == "R-99901"
    assert f["confidence"] == 1.0
    assert f["extraction_method"] == "user"
    # The AI result is preserved for audit (FR-17) when correcting an extracted field.
    if f["original_value"] is not None:
        assert f["original_value"] != "R-99901"

    # The correction is durable.
    items = app_client.get(f"/api/v1/documents/{doc_id}/fields", headers=headers).json()["data"]["items"]
    got = next(x for x in items if x["field_name"] == "invoice_number")
    assert got["value"] == "R-99901"


def test_correct_missing_field_creates_user_field(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)
    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": "serial_number", "action": "correct", "value": "SN-USER-1"},
    )
    assert resp.status_code == 200
    f = resp.json()["data"]["field"]
    assert f["verification_status"] == "USER_CORRECTED"
    assert f["model_name"] == "user"


def test_reject_field(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)
    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": "retailer", "action": "reject"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["field"]["verification_status"] == "REJECTED"


def test_patch_invalid_action_rejected(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)
    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": "retailer", "action": "delete-everything"},
    )
    assert resp.status_code == 422 or resp.status_code == 400


def test_patch_unsupported_field_rejected(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)
    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": "not_a_real_field", "action": "confirm"},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


def test_patch_without_value_to_correct_rejected(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    headers = auth_headers(registered_user)
    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=headers,
        json={"field_name": "total_price", "action": "correct", "value": "   "},
    )
    assert resp.status_code == 422


def test_fields_other_users_document_404(app_client, registered_user):
    import uuid

    from tests.conftest import _register

    doc_id = _processed_doc(app_client, registered_user)
    other = _register(app_client, f"other-{uuid.uuid4().hex[:10]}@example.com")
    resp = app_client.get(f"/api/v1/documents/{doc_id}/fields", headers=auth_headers(other))
    assert resp.status_code == 404
    resp = app_client.patch(
        f"/api/v1/documents/{doc_id}/fields",
        headers=auth_headers(other),
        json={"field_name": "retailer", "action": "confirm"},
    )
    assert resp.status_code == 404


def test_classification_endpoint_returns_job_metadata(app_client, registered_user):
    from app.core.config import get_settings

    doc_id = _processed_doc(app_client, registered_user)
    resp = app_client.get(f"/api/v1/documents/{doc_id}/classification", headers=auth_headers(registered_user))
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["classification"]["document_type"] == "RECEIPT"
    assert body["pipeline_version"] == get_settings().pipeline_version
    assert isinstance(body["ocr_report"], dict)


def test_fields_requires_auth(app_client, registered_user):
    doc_id = _processed_doc(app_client, registered_user)
    resp = app_client.get(f"/api/v1/documents/{doc_id}/fields")
    assert resp.status_code == 401
