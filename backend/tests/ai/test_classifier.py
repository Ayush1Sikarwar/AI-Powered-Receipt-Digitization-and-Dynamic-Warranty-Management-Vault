"""AI tests: document classification (TSD §24, FR-03, ML-06).

The classifier is deterministic and must return UNKNOWN rather than guess
when evidence is weak (hallucination control).
"""

from app.ai.classification.classifier import classify_document
from app.models.document import DocumentType

RECEIPT_TEXT = """TEST RETAIL STORE
Date: 2026-08-01
Receipt No: R1001
Nortech 55in QLED TV x1  $1299.99
Sub-total: $1299.99
Tax: $103.99
Total: $1403.98
Thank you, come again!
"""

INVOICE_TEXT = """ACME SUPPLIES INC.
INVOICE NO: INV-2026-042
BILL TO: Example Corp
Due date: 2026-09-15
Terms: Net 30
Amount Due: $5,000.00
"""

WARRANTY_TEXT = """NORTECH LIMITED WARRANTY
This product warranty covers defects in materials.
Warranty period: 1 year from date of purchase.
Serial number: AB12CD34
Warranty registration: online within 30 days.
"""

PO_TEXT = """PURCHASE ORDER
PO Number: PO-99123
Buyer: Example Corp
Supplier: Nortech
Delivery date: 2026-10-01
"""


def test_receipt_classified():
    c = classify_document(RECEIPT_TEXT)
    assert c.document_type is DocumentType.RECEIPT
    assert c.confidence >= 0.5
    assert c.scores["RECEIPT"] > c.scores.get("INVOICE", 0)
    assert c.method


def test_invoice_classified():
    c = classify_document(INVOICE_TEXT)
    assert c.document_type is DocumentType.INVOICE
    assert c.scores["INVOICE"] >= c.scores["RECEIPT"]


def test_warranty_card_classified():
    c = classify_document(WARRANTY_TEXT)
    assert c.document_type is DocumentType.WARRANTY_CARD


def test_purchase_order_classified():
    c = classify_document(PO_TEXT)
    assert c.document_type is DocumentType.PURCHASE_ORDER


def test_empty_text_is_unknown_with_zero_confidence():
    for text in ("", "   ", "\n\n"):
        c = classify_document(text)
        assert c.document_type is DocumentType.UNKNOWN
        assert c.confidence == 0.0
        assert c.scores == {}


def test_weak_evidence_returns_unknown_not_guess():
    # ML-06: no confident guess on ambiguous gibberish (no keywords at all).
    c = classify_document("asdf qwer zxcv\nrandom words only\n")
    assert c.document_type is DocumentType.UNKNOWN
    assert c.confidence == 0.0


def test_weak_single_keyword_returns_unknown():
    # "card" + "payment" score 2 < 3: not enough evidence for RECEIPT.
    c = classify_document("card payment")
    assert c.document_type is DocumentType.UNKNOWN
    assert c.confidence <= 0.4
    assert c.scores["RECEIPT"] == 2


def test_near_tie_returns_unknown():
    # WARRANTY 5 vs INVOICE 4: margin (5-4)/9 = 0.11 < 0.15 -> no guess.
    c = classify_document("warranty invoice")
    assert c.document_type is DocumentType.UNKNOWN
    assert c.scores["WARRANTY_CARD"] == 5
    assert c.scores["INVOICE"] == 4
    assert c.confidence <= 0.4


def test_confidence_capped_below_one():
    c = classify_document(RECEIPT_TEXT)
    assert c.confidence <= 0.99
