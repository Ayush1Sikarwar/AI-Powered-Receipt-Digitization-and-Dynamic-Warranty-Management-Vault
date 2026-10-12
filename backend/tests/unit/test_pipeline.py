"""Unit tests: rendering + quality (Phase 1 pipeline stages)."""

import numpy as np
from PIL import Image

from app.ai.pipeline import quality_score, render_pages


def _png(data: bytes) -> bytes:
    import io

    buf = io.BytesIO()
    Image.frombytes("RGB", (80, 80), data).save(buf, format="PNG")
    return buf.getvalue()


def test_render_single_image():
    png = _png(b"\x10\x10\x10" * (80 * 80))
    pages = render_pages(png, "image/png")
    assert len(pages) == 1
    assert pages[0].size == (80, 80)


def test_render_pdf_pages():
    import io

    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    c.drawString(100, 500, "page one")
    c.showPage()
    c.drawString(100, 500, "page two")
    c.showPage()
    c.save()
    pages = render_pages(buf.getvalue(), "application/pdf")
    assert len(pages) == 2


def test_quality_score_range_and_ordering():
    clear = Image.fromarray(np.full((100, 100), 200, dtype=np.uint8))
    clear_arr = np.full((100, 100), 200, dtype=np.uint8)
    clear_arr[:50] = 30  # add contrast
    contrasty = Image.fromarray(clear_arr.astype(np.uint8))
    assert 0.0 <= quality_score(clear) <= 1.0
    assert 0.0 <= quality_score(contrasty) <= 1.0
    # A high-contrast image should not score lower than a flat one.
    assert quality_score(contrasty) >= quality_score(clear)
