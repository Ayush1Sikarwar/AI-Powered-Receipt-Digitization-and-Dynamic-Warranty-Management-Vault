"""AI tests: OCR engine (TSD §26)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.ai.ocr.engine import TesseractEngine, get_engine, run_ocr_on_pages
from app.core.exceptions import OcrError


def make_image(lines: list[str], size=(700, 400)) -> np.ndarray:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 40 + i * 50), line, fill="black")
    return np.asarray(img)[:, :, ::-1].copy()  # BGR


def test_tesseract_available():
    assert TesseractEngine().is_available()


def test_ocr_returns_lines_with_confidence_and_boxes():
    img = make_image(["TOTAL 129.99", "Date 2026-08-01"])
    lines = get_engine().recognize(img, 1)
    assert lines, "expected at least one line"
    all_text = " ".join(line.text for line in lines).upper()
    assert "TOTAL" in all_text or "129" in all_text
    for line in lines:
        assert 0 <= line.confidence <= 100
        assert len(line.words) >= 1
        for w in line.words:
            assert w.bbox[2] > 0 and w.bbox[3] > 0  # width/height > 0


def test_run_ocr_two_stage_report():
    img = make_image(["Hello WarrantyVault", "Amount 99.99"])
    result, report = run_ocr_on_pages([img], low_confidence_trigger=60.0)
    assert result.engine
    assert "primary_engine" in report
    assert "primary_mean_confidence" in report
    assert result.text().strip() != ""


def test_unavailable_engine_raises():
    class Missing(TesseractEngine):
        def is_available(self) -> bool:
            return False

    with pytest.raises(OcrError):
        Missing().recognize(make_image(["x"]), 1)


def test_paddleocr_honest_not_configured():
    from app.ai.ocr.engine import PaddleOcrEngine

    eng = PaddleOcrEngine()
    if not eng.is_available():
        with pytest.raises(OcrError, match="not configured"):
            eng.recognize(make_image(["x"]), 1)


def test_report_persists_line_level_ocr_record():
    """The stored OCR record must carry text, confidence, bounding boxes,
    page, engine, and model/version (SRS ML-03 / TSD §26)."""
    img = make_image(["NORTECH STORE", "Date: 2026-08-01", "Total: $1403.98"])
    result, report = run_ocr_on_pages([img], low_confidence_trigger=60.0)
    assert report["lines"], "report must persist per-line OCR records"
    for entry in report["lines"]:
        assert entry["page"] >= 1
        assert isinstance(entry["line"], int)
        assert isinstance(entry["text"], str)
        assert 0 <= entry["confidence"] <= 100
        x, y, w, h = entry["bbox"]
        assert w > 0 and h > 0, f"bbox must be non-degenerate: {entry['bbox']}"
    assert report["engine"] == result.engine
    assert report["engine_version"] == result.engine_version
    assert report["line_source"] in ("primary", "primary+fallback(per-line best)")
    # Line records agree with the returned OcrResult lines.
    result_lines = [ln for p in result.pages for ln in p]
    assert len(report["lines"]) == len(result_lines)
