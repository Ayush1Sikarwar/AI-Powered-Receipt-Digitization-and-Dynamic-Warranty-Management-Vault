"""Security/robustness regression tests (full-repo audit 2026-09-04).

Covers the CRITICAL/HIGH findings that were fixed:
- PDF page-cap rejection happens BEFORE rendering (no multi-minute
  rasterization of an over-cap PDF) and the document reaches a terminal
  FAILED state (it must never be left stuck in PROCESSING);
- image decode-bomb guard (declared dimensions capped before decode);
- out-of-range integer IDs return 404, not 500;
- the global per-IP API rate limit (rl_api_per_min) is enforced;
- the process refuses to start in production with the default JWT secret;
- oversized upload bodies are rejected while streaming (size cap).
"""

import io
import struct
import zlib

import pytest

from app.core.exceptions import ProcessingError
from app.models.document import Document, DocumentStatus
from tests.api.test_documents import make_png_bytes
from tests.conftest import auth_headers


def _png_bytes() -> bytes:
    return make_png_bytes()


def _craft_png_declaring(w: int, h: int) -> bytes:
    """Minimal PNG: huge declared dimensions (IHDR) + 1x1 pixel payload.
    Mimics a decompression-bomb upload: tiny file, multi-GB decode cost."""

    def chunk(typ: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _pdf_with_pages(n: int) -> bytes:
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    for i in range(n):
        c.drawString(100, 500, f"page {i}")
        c.showPage()
    c.save()
    return buf.getvalue()


# --- PDF page cap -----------------------------------------------------------


def test_pdf_page_cap_rejected_before_render():
    """Over-cap PDFs are rejected by the pre-render pypdf check — no
    rasterization of hundreds of pages into memory first."""
    from app.ai.pipeline import render_pages

    pdf = _pdf_with_pages(25)  # cap is 20
    with pytest.raises(ProcessingError) as exc_info:
        render_pages(pdf, "application/pdf")
    assert "page count" in exc_info.value.message
    # The under-cap case still works.
    from app.ai.pipeline import render_pages as rp

    assert len(rp(_pdf_with_pages(3), "application/pdf")) == 3


# --- image decode bomb --------------------------------------------------------


def test_image_decode_cap_rejects_declared_huge_dimensions():
    """A tiny PNG declaring 49 MP must be rejected BEFORE decode. 49 MP is
    above our 40 MP cap but below PIL's own ~178 MP bomb threshold, which is
    exactly the band where OUR guard adds protection (PIL would only warn).
    Decode would need ~147 MB; a 45k x 45k upload would need ~6 GB."""
    from app.ai.pipeline import MAX_DECODE_PIXELS, render_pages

    bomb = _craft_png_declaring(7000, 7000)  # 49 MP: > 40 MP cap, < PIL's 178 MP
    assert len(bomb) < 1024  # tiny file, huge declared size
    with pytest.raises(ProcessingError) as exc_info:
        render_pages(bomb, "image/png")
    assert "decoded size" in exc_info.value.message
    # A normal image still renders.
    assert len(render_pages(_png_bytes(), "image/png")) == 1
    assert MAX_DECODE_PIXELS == 40_000_000


# --- pipeline reaches terminal FAILED (never stuck in PROCESSING) --------------


def test_pipeline_page_cap_marks_document_failed(app_client, registered_user, db_session):
    """A deterministic ProcessingError (page cap) must leave the document in
    FAILED state with an error message — the pre-fix behavior left it stuck
    in PROCESSING while the job retried to DEAD."""
    from app.ai.pipeline import run_document_pipeline
    from app.services import storage as storage_service

    user = registered_user["user"]
    pdf = _pdf_with_pages(25)
    from uuid import uuid4

    key = storage_service.storage_key("original", user["id"], uuid4().hex, "pdf")
    storage_service.get_storage().put(key, pdf, "application/pdf")
    doc = Document(
        user_id=user["id"],
        document_type=None,
        original_filename="toolong.pdf",
        mime_type="application/pdf",
        file_size=len(pdf),
        storage_key=key,
        sha256_hash="ab" * 32,
    )
    from app.models.document import DocumentType

    doc.document_type = DocumentType.UNKNOWN
    db_session.add(doc)
    db_session.commit()

    with pytest.raises(ProcessingError):
        run_document_pipeline(db_session, doc)

    db_session.expire_all()
    doc = db_session.get(Document, doc.id)
    assert doc.status == DocumentStatus.FAILED
    assert doc.error_code == "PROCESSING_ERROR"
    assert "page count" in (doc.error_message or "")

    storage_service.get_storage().delete(key)


# --- out-of-range integer IDs --------------------------------------------------


def test_out_of_range_id_returns_404(app_client, registered_user):
    for path in ("/documents/999999999999", "/purchases/999999999999", "/claims/999999999999"):
        r = app_client.get(f"/api/v1{path}", headers=auth_headers(registered_user))
        assert r.status_code == 404, f"{path} -> {r.status_code}: {r.text[:200]}"
        assert r.json()["error_code"] == "NOT_FOUND"


# --- global API rate limit ------------------------------------------------------


def test_global_api_rate_limit_enforced(app_client, registered_user):
    """rl_api_per_min (300) applies to general API endpoints, not just
    auth/upload (SRS Security: 'Rate limits shall protect ... API endpoints')."""
    from app.core.config import get_settings

    limit = get_settings().rl_api_per_min
    # A burst straddling a fixed-window boundary splits its count across two
    # buckets, so a single burst of (limit + 5) is not guaranteed to trip the
    # limiter. Two consecutive full bursts (2*limit + 10, still far under the
    # window duration at test request rates) guarantee that at least one
    # 60s bucket receives more than `limit` hits, whichever alignment we hit.
    saw_429 = False
    for _ in range(2 * limit + 10):
        r = app_client.get("/api/v1/dashboard/summary", headers=auth_headers(registered_user))
        if r.status_code == 429:
            saw_429 = True
            assert r.json()["error_code"] == "RATE_LIMITED"
            assert "Retry-After" in r.headers
            break
    assert saw_429, "expected the global API rate limit to trigger"


# --- production secret policy ----------------------------------------------------


def _patch_main_settings(monkeypatch, **env):
    """Build a Settings from a controlled env and patch it into app.main,
    whose _enforce_secret_policy reads the module-level `settings` global."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.core.config as cfg
    from app import main as main_module

    monkeypatch.setattr(main_module, "settings", cfg.Settings(_env_file=None))
    return main_module


def test_production_refuses_default_auth_secret(monkeypatch):
    """A production deployment must not boot with the default/short JWT
    secret (anyone with source access could mint tokens)."""
    main_module = _patch_main_settings(
        monkeypatch, APP_ENV="production", AUTH_SECRET="dev-only-insecure-secret-change-me"
    )
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        main_module._enforce_secret_policy()


def test_production_refuses_short_auth_secret(monkeypatch):
    main_module = _patch_main_settings(monkeypatch, APP_ENV="production", AUTH_SECRET="short")
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        main_module._enforce_secret_policy()


def test_production_allows_long_random_secret(monkeypatch):
    """A strong secret in production must be accepted (no false positive)."""
    main_module = _patch_main_settings(
        monkeypatch, APP_ENV="production", AUTH_SECRET="a" * 64
    )
    main_module._enforce_secret_policy()  # must not raise


# --- streaming upload size cap ---------------------------------------------------


def test_upload_streaming_cap_rejects_oversized(app_client, registered_user):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (15 * 1024 * 1024 + 1)
    r = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("big.png", big, "image/png")},
    )
    assert r.status_code == 400
    assert r.json()["error_code"] == "FILE_ERROR"
