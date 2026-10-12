"""Warranty matching rules engine (TSD §14, SRS FR-10).

Ranks applicable warranty policies for a purchase item. Stages —
deterministic rules first, NLP/semantic support second:
1. EXACT_MODEL   — policy model pattern matches the item/product model
2. EXACT_SKU     — policy model pattern matches the SKU
3. CATALOG_MATCH — manufacturer + product category match
4. FUZZY_MATCH   — model/SKU string similarity against the policy pattern
   (rapidfuzz; supporting evidence, confidence < CATALOG_MATCH)
5. SEMANTIC_MATCH— token-overlap between product text and policy text
   (brand + name + category vs policy name + category + manufacturer;
   supporting evidence, lowest automatic confidence)

A policy is a candidate only when its manufacturer scope includes the
item's manufacturer (or is generic) and its effective window covers the
purchase date. Every match carries its evidence list so mappings stay
reviewable (TSD §14: "Low-confidence mappings must be reviewable").
"""

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.product import Manufacturer, Product
from app.models.warranty import MatchMethod, WarrantyPolicy, WarrantySource
from app.warranty.retrieval.policies import SOURCE_PRIORITY, active_policies

_METHOD_RANK = {
    MatchMethod.EXACT_MODEL: 0,
    MatchMethod.EXACT_SKU: 0,
    MatchMethod.CATALOG_MATCH: 1,
    MatchMethod.FUZZY_MATCH: 2,
    MatchMethod.SEMANTIC_MATCH: 2,
    MatchMethod.MANUAL: 3,
}


@dataclass
class PolicyMatch:
    policy: WarrantyPolicy
    method: MatchMethod
    confidence: float
    evidence: list[str] = field(default_factory=list)


def _source_priority(db: Session, policy: WarrantyPolicy) -> tuple[int, int]:
    source = db.get(WarrantySource, policy.source_id) if policy.source_id else None
    if source is None:
        return (6, 0)
    return (SOURCE_PRIORITY.get(source.source_type, 6), -source.trust_level)


def _pattern_matches(pattern: str | None, value: str | None) -> bool:
    if not pattern or not value:
        return False
    return fnmatch.fnmatch(value.strip().upper(), pattern.strip().upper())


_FUZZY_MIN_SCORE = 80.0  # rapidfuzz token_set_ratio threshold (0-100)
_SEMANTIC_MIN_JACCARD = 0.5


def _strip_wildcards(pattern: str) -> str:
    return re.sub(r"[*?]", "", pattern)


def _fuzzy_match(
    p: WarrantyPolicy,
    pattern: str | None,
    model: str | None,
    sku: str | None,
) -> tuple[MatchMethod, float, list[str]] | None:
    """Model/SKU string similarity against the policy pattern (support)."""
    if not pattern:
        return None
    literal = _strip_wildcards(pattern).strip().upper()
    if not literal:
        return None
    best = 0.0
    best_field = None
    for field_name, value in (("model", model), ("sku", sku)):
        if not value:
            continue
        score = fuzz.token_set_ratio(literal, value.strip().upper())
        if score > best:
            best, best_field = score, field_name
    if best < _FUZZY_MIN_SCORE:
        return None
    conf = 0.60 + (best - _FUZZY_MIN_SCORE) / 200.0  # 0.60 .. 0.70
    return MatchMethod.FUZZY_MATCH, round(conf, 4), [
        f"fuzzy {best_field}~pattern score={best:.0f} (threshold {_FUZZY_MIN_SCORE:.0f})"
    ]


_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "the", "to", "with"]
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS}


def _semantic_match(
    p: WarrantyPolicy,
    product_text: str,
    mfr_name: str,
) -> tuple[MatchMethod, float, list[str]] | None:
    """Token-overlap between product text and policy text (supporting NLP
    evidence): brand + canonical name + category vs policy name + category
    + manufacturer + model pattern. Deterministic and explainable."""
    if not product_text:
        return None
    policy_text = " ".join(
        part for part in [p.policy_name, p.product_category or "", mfr_name, _strip_wildcards(p.product_model_pattern or "")]
        if part
    ).strip()
    if not policy_text:
        return None
    a, b = _tokens(product_text), _tokens(policy_text)
    if not a or not b:
        return None
    jaccard = len(a & b) / len(a | b)
    if jaccard < _SEMANTIC_MIN_JACCARD:
        return None
    conf = 0.40 + 0.30 * jaccard  # 0.40 .. 0.70 — always below CATALOG_MATCH
    shared = sorted(a & b)[:6]
    return MatchMethod.SEMANTIC_MATCH, round(conf, 4), [
        f"semantic overlap jaccard={jaccard:.2f} (shared: {', '.join(shared)})"
    ]


def find_warranty_policy(
    db: Session,
    *,
    product: Product,
    manufacturer: Manufacturer | None,
    region: str | None,
    purchase_date: date | None,
    item_model: str | None = None,
    item_sku: str | None = None,
) -> tuple[PolicyMatch | None, list[str]]:
    """Return the best matching policy (or None) plus a note trail.

    The model used is the item's raw model (from the receipt) falling back
    to the catalog product's model; same for SKU.
    """
    notes: list[str] = []
    candidates: list[PolicyMatch] = []

    model = (item_model or product.model_number or "").strip() or None
    sku = (item_sku or product.sku or "").strip() or None
    category = (product.category or "").strip().lower() or None

    policies = active_policies(
        db,
        manufacturer_id=manufacturer.id if manufacturer else None,
        region=region,
        category=category,
        purchase_date=purchase_date,
    )
    if not policies:
        notes.append("no active policy in scope (manufacturer/region/effective window)")
        return None, notes

    product_text = " ".join(
        part for part in [product.brand, product.canonical_name, product.category] if part
    ).strip().lower()
    mfr_name = (manufacturer.name if manufacturer else "").strip().lower()

    for p in policies:
        pattern = p.product_model_pattern
        method: MatchMethod | None = None
        conf = 0.0
        evidence: list[str] = []
        if pattern and model and _pattern_matches(pattern, model):
            method, conf = MatchMethod.EXACT_MODEL, 0.95
            evidence.append("model pattern matched")
        elif pattern and sku and _pattern_matches(pattern, sku):
            method, conf = MatchMethod.EXACT_SKU, 0.95
            evidence.append("sku pattern matched")
        elif category and (p.product_category or "").strip().lower() == category:
            method, conf = MatchMethod.CATALOG_MATCH, 0.85
            evidence.append(f"category '{category}'")
        else:
            # Supporting-evidence stages (deterministic scoring, lower
            # confidence than the rules above) — only when no exact /
            # catalog rule fired for this policy.
            fm = _fuzzy_match(p, pattern, model, sku)
            if fm is not None:
                method, conf, ev = fm
                evidence.extend(ev)
            if method is None:
                sm = _semantic_match(p, product_text, mfr_name)
                if sm is not None:
                    method, conf, ev = sm
                    evidence.extend(ev)

        if method is None:
            continue
        # Region precision: an exact-region policy outranks worldwide.
        if region and p.region and p.region.upper() == region.upper():
            conf = min(1.0, conf)
        else:
            conf *= 0.95  # worldwide fallback — slightly less precise
        if pattern:
            evidence.append(f"pattern='{pattern}'")
        if p.region:
            evidence.append(f"region={p.region}")
        else:
            evidence.append("region=worldwide")
        candidates.append(PolicyMatch(policy=p, method=method, confidence=round(conf, 4), evidence=evidence))

    if not candidates:
        notes.append(
            "policies in scope but none matched model/sku/category "
            "(fuzzy/semantic support below threshold)"
        )
        return None, notes

    candidates.sort(
        key=lambda m: (
            _METHOD_RANK[m.method],
            -m.confidence,
            _source_priority(db, m.policy),
            m.policy.id,
        )
    )
    top = candidates[0]
    if len(candidates) > 1:
        notes.append(f"{len(candidates)} candidate policies; best: {top.policy.policy_name}")
    return top, notes
