"""AI tests: structured extraction (TSD §27, ML-03/ML-06)."""

from app.ai.extraction.extractor import extract_from_lines
from app.ai.ocr.engine import OcrLine


def L(n: int, text: str, conf: float = 90.0) -> OcrLine:
    return OcrLine(line_number=n, text=text, confidence=conf, words=[], bbox=(0, 0, 0, 0))


def lines_of(*texts: str) -> list[OcrLine]:
    return [L(i + 1, t) for i, t in enumerate(texts)]


def test_full_receipt_extraction():
    ls = lines_of(
        "NORTECH HOME APPLIANCES",
        "12 Commerce Ave, Springfield",
        "",
        "Date: 2026-08-01",
        "Receipt No: R10245",
        "Model: QT55Q9",
        "SKU: QT55Q9-2026",
        "",
        "Nortech 55in 4K QLED TV x1  $1299.99",
        "Sub-total: $1299.99",
        "Tax: $103.99",
        "Total: $1403.98",
        "",
        "Thank you - please come again!",
    )
    r = extract_from_lines(ls, page=1)
    got = {f.field_name: f for f in r.fields}

    assert got["purchase_date"].normalized_value == "2026-08-01"
    assert got["purchase_date"].confidence >= 0.9
    assert got["invoice_number"].value == "R10245"
    assert got["model_number"].value == "QT55Q9"
    assert got["sku"].value == "QT55Q9-2026"
    assert got["total_price"].value == "1403.98"
    assert got["tax"].value == "103.99"
    assert got["currency"].value == "USD"
    assert got["retailer"].value.upper().startswith("NORTECH")
    assert got["manufacturer"].value == "Nortech"
    # Product name heuristic finds the long alphabetic line.
    assert "QLED" in got["product_name"].value.upper() or "NORTECH" in got["product_name"].value.upper()
    # Every field has provenance.
    for f in r.fields:
        assert f.source_page == 1
        assert f.source_region
        assert f.model_name
        assert f.model_version


def test_serial_and_quantity():
    ls = lines_of("STORE", "S/N: AB12CD34", "Qty: 2", "Total: $50.00")
    r = extract_from_lines(ls, 1)
    got = {f.field_name: f for f in r.fields}
    assert got["serial_number"].value == "AB12CD34"
    assert got["quantity"].value == "2"
    assert got["total_price"].value == "50.00"


def test_no_evidence_returns_null_fields_not_invented():
    """ML-06: no invented values. A receipt with no recognizable structure
    yields few/no fields, never fabricated ones."""
    ls = lines_of("random gibberish line one", "another unrelated phrase here", "zzz qqq xxx")
    r = extract_from_lines(ls, 1)
    got = {f.field_name: f for f in r.fields}
    assert "purchase_date" not in got
    assert "total_price" not in got
    assert "serial_number" not in got
    assert "invoice_number" not in got


def test_empty_input():
    r = extract_from_lines([], 1)
    assert r.fields == []
    assert r.warnings


def test_date_formats():
    for raw, expected in [
        ("Date: 08/01/2026", None),  # ambiguous day-first -> MM/DD assumed
        ("Date: 2026-08-01", "2026-08-01"),
        ("Date: 15 August 2026", "2026-08-15"),
        ("Date: August 15, 2026", "2026-08-15"),
    ]:
        ls = lines_of(raw)
        r = extract_from_lines(ls, 1)
        f = r.get("purchase_date")
        if expected:
            assert f is not None, raw
            assert f.normalized_value == expected, raw
        else:
            assert f is not None, raw


def test_currency_detection():
    ls = lines_of("TOTAL 1299.00 INR")
    r = extract_from_lines(ls, 1)
    assert r.get("currency").value == "INR"
    ls2 = lines_of("Total: €89.99")
    r2 = extract_from_lines(ls2, 1)
    assert r2.get("currency").value == "EUR"


# --- Hallucination protection (SRS ML-06) ----------------------------------

def test_no_fields_invented_without_evidence():
    """Fields absent from the OCR text must be absent from the output —
    never invented dates, prices, SKUs, models, serials, or retailers."""
    lines = [
        OcrLine(line_number=1, text="Date: 2026-08-01", confidence=95.0, page=1),
        OcrLine(line_number=2, text="Total: 1403.98", confidence=92.0, page=1),
    ]
    result = extract_from_lines(lines, page=1)
    names = {f.field_name for f in result.fields}
    assert "purchase_date" in names
    assert "total_price" in names
    for invented in ("product_name", "sku", "model_number", "serial_number", "tax", "retailer", "unit_price"):
        assert invented not in names, f"{invented} invented without evidence: {result.fields}"


def test_blank_document_yields_no_fields():
    result = extract_from_lines([], page=1)
    assert result.fields == []
    assert result.warnings, "blank input must be reported, not silently treated as empty success"
