"""Document classification (FR-03, SDD).

Classifies into: RECEIPT, INVOICE, WARRANTY_CARD, PURCHASE_ORDER, OTHER,
UNKNOWN.

Deterministic, evidence-based keyword + structure scoring over the OCR text.
Returns UNKNOWN with low confidence when evidence is weak (ML-06: never guess
beyond the evidence). Versioned via the model registry.
"""

import re
from dataclasses import dataclass

from app.ai.registry import CLASSIFIER_MODEL_NAME
from app.models.document import DocumentType

# Keyword weight tables (case-insensitive). Weights reflect how diagnostic a
# token is for a class.
_RECEIPT = {
    "thank you": 3, "thanks": 2, "please come again": 3, "receipt": 3, "cashier": 2,
    "tax": 1, "sub-total": 2, "subtotal": 2, "total": 1, "cash": 1, "card": 1,
    "payment": 1, "amount due": 2, "paid": 1, "balance due": 2,
}
_INVOICE = {
    "invoice": 4, "bill to": 3, "invoice no": 3, "invoice number": 3, "due date": 3,
    "terms": 1, "remittance": 3, "payment due": 2, "purchase order": 1, "billed": 2,
}
_WARRANTY = {
    "warranty": 5, "warranties": 4, "guarantee": 3, "coverage": 2, "warranty period": 4,
    "manufacturer": 1, "serial number": 2, "registration": 1, "warranty registration": 4,
    "limited warranty": 4, "product warranty": 4,
}
_PURCHASE_ORDER = {
    "purchase order": 5, "po number": 4, "po no": 4, "order number": 2, "buyer": 2,
    "supplier": 2, "delivery date": 2,
}

# Structural features.
_HAS_TOTAL_LINE = re.compile(r"^\s*(total|amount due|grand total)\s*[:\-]?\s*\$?\d", re.I | re.M)
_HAS_DATE = re.compile(r"\b(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b|\b(0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])[-/](19|20)\d{2}\b")
_HAS_LINE_ITEMS = re.compile(r"^\s*.{1,40}\s+\d{1,4}\s*\$\d", re.M)


@dataclass
class Classification:
    document_type: DocumentType
    confidence: float  # 0..1
    scores: dict[str, float]
    method: str = CLASSIFIER_MODEL_NAME


def classify_document(text: str) -> Classification:
    if not text or not text.strip():
        return Classification(DocumentType.UNKNOWN, 0.0, {}, )

    lowered = text.lower()
    scores: dict[str, float] = {}
    for name, table in (
        ("RECEIPT", _RECEIPT),
        ("INVOICE", _INVOICE),
        ("WARRANTY_CARD", _WARRANTY),
        ("PURCHASE_ORDER", _PURCHASE_ORDER),
    ):
        s = 0.0
        for kw, w in table.items():
            s += w * (lowered.count(kw))
        scores[name] = round(s, 2)

    # Structure bonuses.
    if _HAS_TOTAL_LINE.search(text):
        scores["RECEIPT"] += 2
        scores["INVOICE"] += 1
    if _HAS_DATE.search(text):
        scores["RECEIPT"] += 1
        scores["INVOICE"] += 1
    if _HAS_LINE_ITEMS.search(text):
        scores["RECEIPT"] += 1

    total = sum(scores.values())
    if total <= 0:
        return Classification(DocumentType.UNKNOWN, 0.0, scores)

    best = max(scores, key=lambda k: scores[k])
    confidence = scores[best] / total
    # Require a meaningful margin to avoid weak guesses (ML-06).
    sorted_scores = sorted(scores.values(), reverse=True)
    margin = (sorted_scores[0] - sorted_scores[1]) / total if len(sorted_scores) > 1 else 1.0
    if scores[best] < 3 or margin < 0.15:
        return Classification(DocumentType.UNKNOWN, round(min(confidence, 0.4), 3), scores)

    type_map = {
        "RECEIPT": DocumentType.RECEIPT,
        "INVOICE": DocumentType.INVOICE,
        "WARRANTY_CARD": DocumentType.WARRANTY_CARD,
        "PURCHASE_ORDER": DocumentType.PURCHASE_ORDER,
    }
    return Classification(type_map[best], round(min(confidence, 0.99), 3), scores)
