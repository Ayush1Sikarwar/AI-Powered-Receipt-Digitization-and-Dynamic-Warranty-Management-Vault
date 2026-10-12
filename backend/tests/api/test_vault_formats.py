"""Digital Vault format & integrity tests (SRS: JPG/JPEG/PNG/WEBP/PDF).

Covers: valid files of every supported format (actually uploaded and
retrieved), invalid extensions, MIME/extension mismatch, corrupted files,
duplicate-hash behavior, and original-immutability during processing.
"""

import io

import pytest
from PIL import Image

from tests.api.test_documents import make_png_bytes, upload
from tests.conftest import auth_headers

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_jpeg(lines=("JPG RECEIPT", "Total: $50.00")):
    img = Image.new("RGB", (640, 300), "white")
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 40 + i * 40), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_webp(lines=("WEBP RECEIPT", "Total: $60.00")):
    img = Image.new("RGB", (640, 300), "white")
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 40 + i * 40), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def make_pdf():
    """A real, minimal, single-page PDF (valid xref) that poppler renders."""
    content = b"BT /F1 24 Tf 72 720 Td (WARRANTY VAULT TEST PDF) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objs) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(
        b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return buf.getvalue()


def _run_pipeline_inprocess(doc_id):
    from app.ai.pipeline import run_document_pipeline
    from app.db.session import SessionLocal
    from app.models.document import Document

    db = SessionLocal()
    try:
        run_document_pipeline(db, db.get(Document, doc_id))
    finally:
        db.close()


def test_png_upload_and_retrieval(app_client, registered_user, db_session):
    data = make_png_bytes()
    resp = upload(app_client, registered_user, filename="valid.png", data=data)
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    import hashlib

    assert doc["sha256"] == hashlib.sha256(data).hexdigest()
    assert doc["mime_type"] == "image/png"

    dl = app_client.get(f"/api/v1/documents/{doc['id']}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content == data  # retrieved bytes identical to uploaded bytes


def test_jpg_upload_and_retrieval(app_client, registered_user):
    data = make_jpeg()
    resp = upload(app_client, registered_user, filename="receipt.jpg", data=data, mime="image/jpeg")
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    assert doc["mime_type"] == "image/jpeg"

    dl = app_client.get(f"/api/v1/documents/{doc['id']}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content == data


def test_webp_upload_and_retrieval(app_client, registered_user):
    data = make_webp()
    resp = upload(app_client, registered_user, filename="receipt.webp", data=data, mime="image/webp")
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    assert doc["mime_type"] == "image/webp"

    dl = app_client.get(f"/api/v1/documents/{doc['id']}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content == data


def test_pdf_upload_processing_and_retrieval(app_client, registered_user, db_session):
    data = make_pdf()
    resp = upload(app_client, registered_user, filename="manual.pdf", data=data, mime="application/pdf")
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    assert doc["mime_type"] == "application/pdf"

    # Real processing through the pipeline (poppler render + OCR).
    _run_pipeline_inprocess(doc["id"])
    detail = app_client.get(f"/api/v1/documents/{doc['id']}", headers=auth_headers(registered_user)).json()["data"]
    assert detail["status"] in ("COMPLETED", "REVIEW_REQUIRED")
    assert detail["page_count"] == 1
    assert detail["pages"][0]["width"] > 0

    # The ORIGINAL pdf is retrievable, byte-identical, still a PDF.
    dl = app_client.get(f"/api/v1/documents/{doc['id']}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content == data
    assert dl.headers["content-type"] == "application/pdf"

    # Enhanced derivative is separately authorized (401 without token).
    assert (
        app_client.get(f"/api/v1/documents/{doc['id']}/download?kind=enhanced").status_code == 401
    )


def test_invalid_extension_rejected(app_client, registered_user):
    resp = upload(app_client, registered_user, filename="notes.txt", data=b"plain text, not a document", mime="text/plain")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_code"] == "FILE_ERROR"
    assert "png" in str(body["details"]["allowed"])

    resp = upload(app_client, registered_user, filename="page.html", data=b"<html></html>", mime="text/html")
    assert resp.status_code == 400


def test_mime_extension_mismatch_rejected(app_client, registered_user):
    # Real PNG content with a .pdf filename: signature says image, extension
    # says pdf -> refused (extension must match detected content type).
    resp = upload(app_client, registered_user, filename="fake.pdf", data=make_png_bytes(), mime="application/pdf")
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "FILE_ERROR"


def test_corrupted_image_graceful_failure(app_client, registered_user, db_session):
    from app.core.exceptions import ProcessingError
    from app.models.document import Document

    good = make_png_bytes()
    corrupted = good[: len(good) // 3]  # valid PNG signature, broken body
    assert corrupted.startswith(PNG_MAGIC)

    resp = upload(app_client, registered_user, filename="corrupt.png", data=corrupted)
    assert resp.status_code == 201  # storage is format-agnostic; processing must fail safely
    doc_id = resp.json()["data"]["document"]["id"]

    with pytest.raises(ProcessingError):
        _run_pipeline_inprocess(doc_id)

    doc = db_session.get(Document, doc_id)
    assert doc.status.value == "FAILED"
    assert doc.error_code == "PROCESSING_ERROR"
    assert doc.error_message

    # The original evidence is still retrievable even after failed processing.
    dl = app_client.get(f"/api/v1/documents/{doc_id}/download", headers=auth_headers(registered_user))
    assert dl.status_code == 200
    assert dl.content == corrupted


def test_duplicate_hash_allowed(app_client, registered_user):
    """The same content may be uploaded twice: there is no cross-document
    dedup (each upload is its own document with its own storage object)."""
    import hashlib

    data = make_png_bytes()
    sha = hashlib.sha256(data).hexdigest()
    r1 = upload(app_client, registered_user, filename="dup-1.png", data=data)
    r2 = upload(app_client, registered_user, filename="dup-2.png", data=data)
    assert r1.status_code == 201 and r2.status_code == 201
    d1, d2 = r1.json()["data"]["document"], r2.json()["data"]["document"]
    assert d1["id"] != d2["id"]
    assert d1["sha256"] == sha == d2["sha256"]

    for d in (d1, d2):
        dl = app_client.get(f"/api/v1/documents/{d['id']}/download", headers=auth_headers(registered_user))
        assert dl.status_code == 200 and dl.content == data


def test_original_never_overwritten_during_processing(app_client, registered_user, db_session):
    """Immutability: processing creates derivatives under NEW keys; the
    original object and its metadata are untouched."""
    from app.models.document import Document

    data = make_png_bytes()
    resp = upload(app_client, registered_user, filename="immut.png", data=data)
    doc_id = resp.json()["data"]["document"]["id"]
    # storage_key is never exposed by the API (TSD §35) — read it from the DB.
    original_key = db_session.get(Document, doc_id).storage_key

    from app.services import storage as storage_service

    store = storage_service.get_storage()
    original_before = store.get(original_key)

    _run_pipeline_inprocess(doc_id)

    doc = db_session.get(Document, doc_id)
    assert doc.storage_key == original_key  # pointer unchanged
    assert store.get(original_key) == original_before  # bytes unchanged
    import hashlib

    assert doc.sha256_hash == hashlib.sha256(data).hexdigest()

    # Derivatives API lists the types (keys themselves are never exposed).
    ders = app_client.get(f"/api/v1/documents/{doc_id}/derivatives", headers=auth_headers(registered_user))
    kinds = {d["derivative_type"] for d in ders.json()["data"]["items"]}
    assert "THUMBNAIL" in kinds
    assert "ENHANCED" in kinds

    # At the storage level: every derivative lives under its own key, in its
    # own category — never the original's key.
    from sqlalchemy import select as _select

    from app.models.document import DocumentDerivative

    rows = db_session.scalars(
        _select(DocumentDerivative).where(DocumentDerivative.document_id == doc_id)
    ).all()
    assert len(rows) >= 2
    for d in rows:
        assert d.storage_key != original_key
        assert store.exists(d.storage_key)
    assert any(
        d.derivative_type.value == "ENHANCED" and d.storage_key.startswith("enhanced/") for d in rows
    )
    assert any(
        d.derivative_type.value == "THUMBNAIL" and d.storage_key.startswith("thumbnails/") for d in rows
    )
