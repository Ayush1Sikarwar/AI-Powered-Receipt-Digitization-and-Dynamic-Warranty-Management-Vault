"""Structured extraction (SRS FR-06, TSD §27, ML-03).

Schema-constrained, evidence-grounded extraction from OCR line structure.
Every produced field carries: value, confidence, source (page + line),
extraction method, and model identity. When evidence is insufficient the
field is simply NOT produced (null) — the extractor never invents values
(ML-06, TSD §27).

Confidence bands (SDD §8, PROVISIONAL until calibrated):
  >= 0.90 auto-accept · 0.70–0.89 user review · < 0.70 manual
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.ai.ocr.engine import OcrLine

# Model identity comes from the model registry (TSD §45) — never hard-coded
# in business logic.
from app.ai.registry import (
    EXTRACTION_MODEL_NAME as MODEL_NAME,
)
from app.ai.registry import (
    EXTRACTION_MODEL_VERSION as MODEL_VERSION,
)

# --- patterns -------------------------------------------------------------
DATE_PATTERNS = [
    (re.compile(r"\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.]((?:19|20)\d{2})\b"), "dmy_or_mdy"),
    (re.compile(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+((?:19|20)\d{2})\b", re.I), "dmy-word"),
    (re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", re.I), "mdy-word"),
]
MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
)}

# Only real ISO currency codes qualify as a "cur" prefix — a bare
# [A-Za-z]{3} class would match any 3-letter word ("Set 3", "Lot 2", "Qty 5")
# and misread it as a currency-coded amount.
_CURRENCY_CODE_ALTS = r"USD|EUR|GBP|INR|AUD|CAD|SGD|AED|CHF|JPY|PLN|CNY"
MONEY = re.compile(
    rf"(?P<cur>{_CURRENCY_CODE_ALTS}|\$|€|£|₹)?\s?(?P<num>\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{2}})|\d+\.\d{{2}}|\d{{1,6}})",
    re.I,
)

KEY_FIELD_PATTERNS = {
    "invoice_number": re.compile(
        r"^\s*(?:invoice\s*(?:no|number|#)|inv\s*(?:no|#)|receipt\s*(?:no|#)|bill\s*(?:no|#)|doc\s*(?:no|#))\s*[:\-=]?\s*([A-Za-z0-9\-/]{3,20})\s*$",
        re.I | re.M,
    ),
    "sku": re.compile(
        r"^\s*(?:sku|item\s*(?:no|#)?|code)\s*[:\-=]?\s*([A-Za-z0-9\-]{2,24})\s*$", re.I | re.M
    ),
    "model_number": re.compile(
        r"^\s*(?:model\s*(?:no|number|#)?|model)\s*[:\-=]?\s*([A-Za-z0-9\-\./]{2,24})\s*$", re.I | re.M
    ),
    "serial_number": re.compile(
        r"^\s*(?:serial\s*(?:no|number|#)?|s/n|sn)\s*[:\-=]?\s*([A-Za-z0-9\-]{4,32})\s*$", re.I | re.M
    ),
    "quantity": re.compile(r"^\s*(?:qty|quantity)\s*[:\-=]?\s*(\d{1,4})\s*$", re.I | re.M),
    "total_price": re.compile(
        r"^\s*(?:grand\s*total|total|amount\s*due|balance\s*due|total\s*due)\s*[:\-=]?\s*(?P<money>USD|EUR|GBP|INR|AUD|CAD|SGD|AED|CHF|JPY|PLN|CNY|\$|€|£|₹)?\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2}|\d{1,6})\s*$",
        re.I | re.M,
    ),
    "tax": re.compile(
        r"^\s*(?:tax|gst|vat|sales\s*tax|service\s*tax|incl\.\s*tax)\s*[:\-=]?\s*(?P<money>USD|EUR|GBP|INR|AUD|CAD|SGD|AED|CHF|JPY|PLN|CNY|\$|€|£|₹)?\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2}|\d{1,6})\s*$",
        re.I | re.M,
    ),
}

CURRENCY_MAP = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
CURRENCY_CODES = re.compile(rf"\b({_CURRENCY_CODE_ALTS})\b", re.I)

# Amount-written-in-words lines ("Rupees Twelve Thousand Seven Hundred Only")
# are common on Indian retail bills and are long + alphabetic, which used to
# make them outscore genuine product lines in the positional heuristic below.
# They are never product names and must be disqualified outright.
AMOUNT_WORDS_RE = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|"
    r"lakhs|crore|crores|rupees|paise|only)\b",
    re.I,
)
# Lines that are clearly part of the totals block, even when they don't carry
# a trailing money amount the total-price pattern can match (e.g. "GRAND
# TOTAL =" printed on its own line, with the amount elsewhere/misOCR'd).
NON_PRODUCT_KEYWORDS_RE = re.compile(
    r"\b(?:total|subtotal|sub-total|grand\s*total|amount\s*due|balance\s*due|"
    r"cash|change|tender(?:ed)?|thank\s*you)\b",
    re.I,
)

# Known manufacturer dictionary (grows via admin-managed knowledge base).
MANUFACTURERS = {
    "samsung", "lg", "sony", "philips", "bosch", "whirlpool", "haier", "dyson",
    "apple", "dell", "hp", "lenovo", "asus", "panasonic", "hitachi", "nokia",
    "nortech", "acme", "cooltech", "voltcraft", "brightline",
}


@dataclass
class FieldExtraction:
    field_name: str
    value: str
    normalized_value: str
    confidence: float
    source_page: int
    source_region: str
    method: str
    notes: str = ""
    # Model identity (TSD §45): every AI output identifies its producer.
    model_name: str = MODEL_NAME
    model_version: str = MODEL_VERSION


@dataclass
class ExtractionResult:
    fields: list[FieldExtraction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def get(self, name: str) -> FieldExtraction | None:
        for f in self.fields:
            if f.field_name == name:
                return f
        return None


def _line_index(lines: list[OcrLine], idx: int) -> tuple[int, OcrLine]:
    return idx + 1, lines[idx]


def _norm_money(raw: str) -> tuple[str, str | None]:
    matches = list(MONEY.finditer(raw))
    if not matches:
        return "", None
    # Prefer the rightmost decimal amount: prices end the line, while bare
    # integers earlier in a line are often part of names ("8kg", "355ml",
    # "12pk"). Fall back to the rightmost match when nothing has decimals.
    decimal = [m for m in matches if "." in m.group("num")]
    m = (decimal or matches)[-1]
    cur = m.group("cur")
    num = m.group("num").replace(",", "")
    code = CURRENCY_MAP.get(cur) if cur else None
    return num, code


def _money_price_match(raw: str) -> tuple[str, str | None] | None:
    """Rightmost money amount that plausibly IS a price: must carry a
    decimal (cents) or a currency symbol. Bare integers ("X90", "8kg",
    "355ml") are not prices."""
    matches = [m for m in MONEY.finditer(raw) if "." in m.group("num") or m.group("cur")]
    if not matches:
        return None
    m = matches[-1]
    cur = m.group("cur")
    return m.group("num").replace(",", ""), (CURRENCY_MAP.get(cur) if cur else None)


def _extract_date(text: str) -> tuple[str | None, float]:
    for pat, kind in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        groups = [g for g in m.groups() if g is not None]
        try:
            if kind == "ymd":
                y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
                conf = 0.92
            elif kind == "dmy_or_mdy":
                a, b, y = int(groups[0]), int(groups[1]), int(groups[2])
                if a > 12:
                    d, mo = a, b
                elif b > 12:
                    mo, d = a, b
                else:
                    mo, d = a, b  # ambiguous; assume MM/DD
                conf = 0.72
            elif kind == "dmy-word":
                d, mo_name, y = int(groups[0]), groups[1].lower(), int(groups[2])
                mo = MONTHS[mo_name]
                conf = 0.92
            else:  # mdy-word
                mo_name, d, y = groups[0].lower(), int(groups[1]), int(groups[2])
                mo = MONTHS[mo_name]
                conf = 0.92
            dt = datetime(y, mo, d)
            return dt.date().isoformat(), conf
        except (ValueError, KeyError):
            continue
    return None, 0.0


def extract_from_lines(lines: list[OcrLine], page: int = 1) -> ExtractionResult:
    """Extract fields from one page's OCR lines (the page with the most
    evidence; the pipeline passes the primary page)."""
    result = ExtractionResult()
    if not lines:
        result.warnings.append("no OCR lines available")
        return result

    all_text = "\n".join(line.text for line in lines)
    seen: set[str] = set()

    def add(f: FieldExtraction) -> None:
        if f.field_name in seen:
            return
        seen.add(f.field_name)
        result.fields.append(f)

    # --- key-value fields -------------------------------------------------
    total_currency: str | None = None
    for field_name, pat in KEY_FIELD_PATTERNS.items():
        for li, line in enumerate(lines):
            m = pat.search(line.text)
            if m:
                if field_name in ("total_price", "tax"):
                    num, code = _norm_money(m.group(0))
                    if not num:
                        continue
                    add(FieldExtraction(field_name, num, num, 0.95, page, f"line {li + 1}", "keyword+pattern"))
                    if field_name == "total_price" and code:
                        total_currency = code
                else:
                    value = m.group(1).strip()
                    conf = 0.93 if field_name in ("invoice_number", "serial_number") else 0.88
                    add(FieldExtraction(field_name, value, value, conf, page, f"line {li + 1}", "keyword+pattern"))
                break

    # --- purchase date ----------------------------------------------------
    for li, line in enumerate(lines):
        iso, conf = _extract_date(line.text)
        if iso:
            add(FieldExtraction("purchase_date", iso, iso, conf, page, f"line {li + 1}", "pattern"))
            break

    # --- currency ---------------------------------------------------------
    currency = None
    currency_conf = 0.8
    if total_currency:
        currency, currency_conf = total_currency, 0.9
    else:
        cm = CURRENCY_CODES.search(all_text)
        if cm:
            currency, currency_conf = cm.group(1), 0.85
        else:
            sym = re.search(r"[\$€£₹]", all_text)
            if sym:
                currency, currency_conf = CURRENCY_MAP[sym.group(0)], 0.75
    if currency:
        add(FieldExtraction("currency", currency, currency, currency_conf, page, "page", "symbol/code"))

    # --- retailer (first plausible header line) ----------------------------
    retailer_line_idx: int | None = None
    for li, line in enumerate(lines[:6]):
        t = line.text.strip()
        if not t:
            continue
        if re.search(r"\d{4}", t) or re.search(r"[\$€£₹]", t) or len(t) < 3:
            continue
        conf = 0.7 if li == 0 else 0.6
        retailer_line_idx = li
        add(FieldExtraction("retailer", t, t.lower(), conf, page, f"line {li + 1}", "header-heuristic"))
        break

    # --- manufacturer -----------------------------------------------------
    # The retailer header line is a store name, not a manufacturer (e.g.
    # "NORTECH HOME APPLIANCES" must not set manufacturer='Nortech' when no
    # product line mentions a brand). A brand token repeated on a product
    # line is still a legitimate manufacturer signal.
    for li, line in enumerate(lines):
        if li == retailer_line_idx:
            continue
        low = line.text.lower()
        for mfr in MANUFACTURERS:
            if re.search(rf"\b{re.escape(mfr)}\b", low):
                add(FieldExtraction("manufacturer", mfr.capitalize(), mfr, 0.8, page, f"line {li + 1}", "dictionary"))
                break
        else:
            continue
        break

    # --- product name + unit price (positional heuristics) -----------------
    total_idx = None
    for li, line in enumerate(lines):
        if KEY_FIELD_PATTERNS["total_price"].search(line.text):
            total_idx = li
            break
    scan_end = total_idx if total_idx is not None else len(lines)
    scan = lines[1:scan_end]
    best = None
    product_candidates = 0
    for li, line in enumerate(scan):
        t = line.text.strip()
        if len(t) < 8 or ":" in t:
            continue  # too short, or a "label: value" line
        if any(pat.search(t) for pat, _ in DATE_PATTERNS):
            continue  # a date line, not a product
        if AMOUNT_WORDS_RE.search(t) or NON_PRODUCT_KEYWORDS_RE.search(t):
            continue  # amount-in-words or totals/cash-block line, not a product
        # Digits are legitimate in product lines (model numbers: "55in",
        # "4K", "QT55Q9", "X90"). Disqualify only structural noise: strip a
        # trailing money amount ONLY when it is preceded by whitespace or a
        # currency symbol (never the digits of a model number), then score.
        core = re.sub(r"(?:[$€£₹]\s*\d[\d,.]*|\s\d[\d,.]*)$", " ", t)
        # Quantity suffixes ("x1", "qty 2") are lowercase in POS prints; a
        # capital-X model suffix ("X90") is not a quantity — hence
        # case-sensitive here (unlike the money regex above).
        core = re.sub(r"(?:^|\s)(?:x|×|qty)\s*\d{1,4}\s*$", " ", core)
        tokens = core.split()
        alpha_tokens = [tok for tok in tokens if any(c.isalpha() for c in tok)]
        pure_num_tokens = [tok for tok in tokens if not any(c.isalpha() for c in tok)]
        if len(alpha_tokens) < 2:
            continue
        product_candidates += 1
        score = 10 * len(alpha_tokens) - 4 * len(pure_num_tokens)
        if not any(c.isalpha() for c in tokens[0]):
            score -= 8  # leading street number → address line, not a product
        if best is None or score > best[0]:
            best = (score, li, core.strip())
    if best:
        li, t = best[1], best[2]
        abs_idx = 1 + li  # scan starts at line index 1
        add(FieldExtraction("product_name", t, t, 0.62, page, f"line {abs_idx + 1}", "positional-heuristic"))

        # unit price: a plausible price amount (cents or currency symbol) on
        # the same line or the adjacent line.
        for adj in (scan[li], scan[li + 1] if li + 1 < len(scan) else None):
            if adj is None:
                continue
            price = _money_price_match(adj.text)
            if price is not None:
                num, code = price
                add(FieldExtraction("unit_price", num, num, 0.68, page, f"line {abs_idx + 1}", "proximity"))
                if code and result.get("currency") is None:
                    add(FieldExtraction("currency", code, code, 0.8, page, "line", "symbol/code"))
                break

        # Single-item evidence-backed inference: exactly one product
        # candidate line, quantity 1, and a total present → the total IS the
        # unit price (invoices print "Amount Due" rather than a unit price).
        qty_field = result.get("quantity")
        if (
            product_candidates == 1
            and qty_field is not None
            and qty_field.normalized_value == "1"
            and result.get("total_price") is not None
        ):
            total = result.get("total_price")
            assert total is not None
            add(
                FieldExtraction(
                    "unit_price",
                    total.normalized_value,
                    total.normalized_value,
                    0.5,
                    page,
                    "total line",
                    "single-item-inference",
                )
            )

    # Fallbacks for missing money fields: scan remaining money lines.
    if result.get("total_price") is None:
        money_lines = [(li, line) for li, line in enumerate(lines) if MONEY.search(line.text) and re.search(r"^\s*(grand\s*total|total|amount due)\b", line.text, re.I) is None]
        # The last money line in a receipt is usually the total.
        if money_lines:
            li, line = money_lines[-1]
            num, code = _norm_money(line.text)
            if num:
                add(FieldExtraction("total_price", num, num, 0.6, page, f"line {li + 1}", "last-money-line"))
    if result.get("tax") is None:
        for li, line in enumerate(lines):
            if re.search(r"\b(tax|gst|vat)\b", line.text, re.I) and MONEY.search(line.text):
                num, code = _norm_money(line.text)
                if num:
                    add(FieldExtraction("tax", num, num, 0.7, page, f"line {li + 1}", "keyword+pattern"))
                break

    if not result.fields:
        result.warnings.append("no fields could be extracted with sufficient evidence")
    return result