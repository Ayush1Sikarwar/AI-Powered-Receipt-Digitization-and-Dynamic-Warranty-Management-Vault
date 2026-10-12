"""Product matching engine (SRS ML-04, TSD §28).

Resolution order (TSD §28):
    Exact SKU/model -> Manufacturer+model -> Retailer catalog -> Alias ->
    Fuzzy matching -> Semantic matching -> User confirmation.

False-positive prevention (TSD §28): a low-confidence or ambiguous
candidate is NEVER auto-linked. Only a unique candidate at/above the
auto-accept threshold becomes AUTO_MATCHED; everything else is
REVIEW_REQUIRED (or NO_MATCH) with the full candidate list preserved for
human confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.registry import (
    PRODUCT_RANKER_MODEL_NAME,
    PRODUCT_RANKER_MODEL_VERSION,
)
from app.models.product import (
    Manufacturer,
    Product,
    ProductAlias,
    ProductMatchMethod,
    ProductMatchStatus,
    Retailer,
    RetailerCatalogEntry,
)

from .normalization import (
    make_name_key,
    normalize_manufacturer,
    normalize_model,
    normalize_retailer,
    normalize_sku,
    normalize_text,
)

# Method priority (lower = earlier resolution stage = more specific).
_METHOD_ORDER = {
    ProductMatchMethod.EXACT_SKU: 0,
    ProductMatchMethod.EXACT_MODEL: 1,
    ProductMatchMethod.MANUFACTURER_MODEL: 2,
    ProductMatchMethod.CATALOG_MATCH: 3,
    ProductMatchMethod.ALIAS_MATCH: 4,
    ProductMatchMethod.FUZZY_MATCH: 5,
    ProductMatchMethod.SEMANTIC_MATCH: 6,
    ProductMatchMethod.MANUAL: 7,
    ProductMatchMethod.NONE: 8,
}

FUZZY_MIN_RATIO = 0.72
FUZZY_CONF_FLOOR = 0.55
FUZZY_CONF_CAP = 0.85  # fuzzy can never reach auto-accept on its own
MODEL_FUZZY_MIN_RATIO = 0.80
MODEL_FUZZY_CONF = 0.75
SEMANTIC_JACCARD_MIN = 0.40
SEMANTIC_CONF_FLOOR = 0.40
SEMANTIC_CONF_CAP = 0.60

# Deterministic lexical-semantic synonym groups (rule-based semantic
# similarity — honest, no external model). "TV" and "television" etc.
SYNONYM_GROUPS: list[frozenset[str]] = [
    frozenset({"tv", "television", "smart tv", "led", "oled"}),
    frozenset({"fridge", "refrigerator"}),
    frozenset({"washer", "washing machine", "wash"}),
    frozenset({"dryer", "tumble dryer"}),
    frozenset({"ac", "air conditioner", "aircon", "split ac"}),
    frozenset({"laptop", "notebook", "notebook pc"}),
    frozenset({"monitor", "display"}),
    frozenset({"headphones", "headset", "earphones"}),
    frozenset({"keyboard", "kb"}),
    frozenset({"mouse", "pointer"}),
    frozenset({"milk", "dairy milk"}),
    frozenset({"bread", "loaf"}),
    frozenset({"coffee", "cold brew"}),
    frozenset({"eggs", "egg"}),
]


@dataclass
class ProductQuery:
    """Everything extracted from the receipt, verbatim (raw)."""

    product_name: str | None = None
    sku: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None
    retailer: str | None = None
    brand: str | None = None
    description: str | None = None

    def text(self) -> str:
        parts = [self.product_name or "", self.brand or "", self.description or ""]
        return " ".join(p for p in parts if p).strip()


@dataclass
class MatchCandidate:
    product_id: int
    product_name: str
    manufacturer: str | None
    model_number: str | None
    sku: str | None
    method: ProductMatchMethod
    confidence: float
    # Engine identity comes from the model registry (TSD §45) — versions are
    # never hard-coded in matching logic.
    engine: str = PRODUCT_RANKER_MODEL_NAME
    engine_version: str = PRODUCT_RANKER_MODEL_VERSION
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "manufacturer": self.manufacturer,
            "model_number": self.model_number,
            "sku": self.sku,
            "method": self.method.value,
            "confidence": round(self.confidence, 4),
            "engine": self.engine,
            "engine_version": self.engine_version,
            "evidence": self.evidence,
        }


# --- catalog loading ---------------------------------------------------------

@dataclass
class _Catalog:
    products: list[dict]
    retailer_by_key: dict[str, dict]
    manufacturer_by_key: dict[str, str]
    alias_index: list[tuple[str, int]]  # (normalized alias text, product row idx)
    catalog_by_retailer: dict[int, list[dict]]


def _load_catalog(db: Session) -> _Catalog:
    products = db.scalars(select(Product).where(Product.status == "ACTIVE")).all()
    manufacturers = {m.name_key: m.name for m in db.scalars(select(Manufacturer)).all()}
    rows: list[dict] = []
    for p in products:
        mfr = None
        if p.manufacturer_id is not None:
            mfr_row = db.get(Manufacturer, p.manufacturer_id)
            mfr = mfr_row.name if mfr_row else None
        rows.append(
            {
                "id": p.id,
                "name": p.canonical_name,
                "name_norm": normalize_text(p.canonical_name),
                "manufacturer": mfr,
                "manufacturer_key": make_name_key(mfr) if mfr else None,
                "model": p.model_number,
                "model_norm": normalize_model(p.model_number),
                "sku": p.sku,
                "sku_norm": normalize_sku(p.sku),
                "brand": p.brand,
            }
        )
    aliases = db.scalars(select(ProductAlias)).all()
    alias_index: list[tuple[str, int]] = []
    for a in aliases:
        idx = next((i for i, r in enumerate(rows) if r["id"] == a.product_id), None)
        if idx is not None:
            alias_index.append((normalize_text(a.alias_text), idx))

    retailers = {r.name_key: {"id": r.id, "name": r.name} for r in db.scalars(select(Retailer)).all()}
    entries = db.scalars(select(RetailerCatalogEntry)).all()
    catalog_by_retailer: dict[int, list[dict]] = {}
    for e in entries:
        idx = next((i for i, r in enumerate(rows) if r["id"] == e.product_id), None)
        if idx is None:
            continue
        catalog_by_retailer.setdefault(e.retailer_id, []).append(
            {
                "product_idx": idx,
                "sku": e.catalog_sku,
                "sku_norm": normalize_sku(e.catalog_sku),
                "model": e.catalog_model,
                "model_norm": normalize_model(e.catalog_model),
                "name": e.catalog_name,
                "name_norm": normalize_text(e.catalog_name),
                "source": e.source,
            }
        )
    return _Catalog(
        products=rows,
        retailer_by_key=retailers,
        manufacturer_by_key=manufacturers,
        alias_index=alias_index,
        catalog_by_retailer=catalog_by_retailer,
    )


def _mfr_key(name: str | None) -> str | None:
    if not name:
        return None
    canonical = normalize_manufacturer(name)
    return make_name_key(canonical) if canonical else None


def _mfr_match(a: str | None, b: str | None) -> bool:
    """Manufacturer keys match when equal or one is a prefix of the other
    (receipts print short brand names: 'QTech' vs 'QTech Electronics',
    'LG' vs 'LG Electronics')."""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


# --- candidate stages --------------------------------------------------------

def _add(out: dict[int, MatchCandidate], row: dict, method: ProductMatchMethod, conf: float, *evidence: str) -> None:
    """Insert-or-upgrade a candidate (keep the best method/conf per product)."""
    c = out.get(row["id"])
    if c is None:
        out[row["id"]] = MatchCandidate(
            product_id=row["id"],
            product_name=row["name"],
            manufacturer=row["manufacturer"],
            model_number=row["model"],
            sku=row["sku"],
            method=method,
            confidence=conf,
            evidence=list(evidence),
        )
        return
    if conf > c.confidence or (conf == c.confidence and _METHOD_ORDER[method] < _METHOD_ORDER[c.method]):
        c.method = method
        c.confidence = conf
        c.evidence.extend(e for e in evidence if e not in c.evidence)


def _stages(db: Session, q: ProductQuery) -> tuple[dict[int, MatchCandidate], list[str]]:
    cat = _load_catalog(db)
    notes: list[str] = []
    out: dict[int, MatchCandidate] = {}

    q_sku = normalize_sku(q.sku)
    q_model = normalize_model(q.model_number)
    q_name = normalize_text(q.text())
    q_mfr = _mfr_key(q.manufacturer or q.brand)
    q_retailer = normalize_retailer(q.retailer)
    retailer = cat.retailer_by_key.get(make_name_key(q_retailer)) if q_retailer else None

    # Stage 1: exact SKU (product-level, then retailer catalog-level).
    if q_sku:
        for row in cat.products:
            if row["sku_norm"] and row["sku_norm"] == q_sku:
                _add(out, row, ProductMatchMethod.EXACT_SKU, 1.0, f"SKU '{q.sku}' exactly matches catalog SKU '{row['sku']}'")
        if retailer:
            for e in cat.catalog_by_retailer.get(retailer["id"], []):
                if e["sku_norm"] and e["sku_norm"] == q_sku:
                    row = cat.products[e["product_idx"]]
                    _add(out, row, ProductMatchMethod.EXACT_SKU, 0.97, f"SKU '{q.sku}' exactly matches {retailer['name']} catalog entry")
    # Stage 1b: exact model.
    model_rows: list[dict] = []
    if q_model:
        for row in cat.products:
            if row["model_norm"] and row["model_norm"] == q_model:
                model_rows.append(row)
                _add(out, row, ProductMatchMethod.EXACT_MODEL, 0.98, f"Model '{q.model_number}' exactly matches catalog model '{row['model']}'")
    # Stage 2: manufacturer + model constraint.
    if model_rows and q_mfr:
        matched = [r for r in model_rows if r["manufacturer_key"] is None or _mfr_match(r["manufacturer_key"], q_mfr)]
        if not matched and model_rows:
            mfrs = sorted({r["manufacturer"] for r in model_rows if r["manufacturer"]})
            notes.append(f"manufacturer mismatch: model found under {mfrs}, query says '{q.manufacturer or q.brand}' — excluded")
        for r in matched:
            if len(model_rows) > 1:
                _add(
                    out,
                    r,
                    ProductMatchMethod.MANUFACTURER_MODEL,
                    0.95,
                    f"Model exact + manufacturer '{q.manufacturer or q.brand}' constraint resolved the match",
                )
    # Stage 3: retailer catalog.
    if retailer:
        for e in cat.catalog_by_retailer.get(retailer["id"], []):
            row = cat.products[e["product_idx"]]
            if q_model and e["model_norm"] and e["model_norm"] == q_model:
                _add(out, row, ProductMatchMethod.CATALOG_MATCH, 0.92, f"Model matches {retailer['name']} catalog listing")
            elif q_name and e["name_norm"] and e["name_norm"] == q_name:
                _add(out, row, ProductMatchMethod.CATALOG_MATCH, 0.88, f"Receipt text matches {retailer['name']} catalog name '{e['name']}'")
    # Stage 4: alias.
    if q_name:
        for alias_norm, idx in cat.alias_index:
            if alias_norm and alias_norm == q_name:
                row = cat.products[idx]
                _add(out, row, ProductMatchMethod.ALIAS_MATCH, 0.90, "Receipt text exactly matches a stored product alias")
    # Stage 5: fuzzy.
    if q_name or q_model:
        for row in cat.products:
            best = 0.0
            if q_name and row["name_norm"]:
                best = max(best, SequenceMatcher(None, q_name, row["name_norm"]).ratio())
            model_ratio = 0.0
            if q_model and row["model_norm"]:
                model_ratio = SequenceMatcher(None, q_model, row["model_norm"]).ratio()
            conf = None
            evidence = []
            if best >= FUZZY_MIN_RATIO:
                conf = min(FUZZY_CONF_CAP, FUZZY_CONF_FLOOR + (FUZZY_CONF_CAP - FUZZY_CONF_FLOOR) * (best - FUZZY_MIN_RATIO) / (1.0 - FUZZY_MIN_RATIO))
                evidence.append(f"Fuzzy similarity {best:.2f} vs '{row['name']}'")
            if model_ratio >= MODEL_FUZZY_MIN_RATIO:
                conf = max(conf or 0.0, MODEL_FUZZY_CONF)
                evidence.append(f"Model fuzzy similarity {model_ratio:.2f} vs '{row['model']}'")
            if conf is not None:
                _add(out, row, ProductMatchMethod.FUZZY_MATCH, round(conf, 4), *evidence)
    # Stage 6: semantic (lexical synonym groups + token overlap).
    if q_name:
        q_tokens = set(q_name.split())
        for row in cat.products:
            p_tokens = set(row["name_norm"].split())
            if not p_tokens:
                continue
            jaccard = len(q_tokens & p_tokens) / len(q_tokens | p_tokens)
            syn = any(g & q_tokens and g & p_tokens for g in SYNONYM_GROUPS)
            if jaccard >= SEMANTIC_JACCARD_MIN or syn:
                conf = min(SEMANTIC_CONF_CAP, SEMANTIC_CONF_FLOOR + 0.2 * max(jaccard, 0.5 if syn else 0.0))
                ev = []
                if syn:
                    ev.append("Semantic synonym overlap (e.g. 'TV' ~ 'television')")
                if jaccard >= SEMANTIC_JACCARD_MIN:
                    ev.append(f"Token overlap {jaccard:.2f} with '{row['name']}'")
                _add(out, row, ProductMatchMethod.SEMANTIC_MATCH, round(conf, 4), *ev)

    # Manufacturer constraint applies across ALL stages: a known candidate
    # manufacturer contradicting a known query manufacturer is a
    # false-positive risk and is excluded.
    if q_mfr:
        for pid in list(out.keys()):
            c = out[pid]
            c_key = _mfr_key(c.manufacturer)
            if c_key is not None and not _mfr_match(c_key, q_mfr):
                del out[pid]
                notes.append(f"excluded '{c.product_name}' — manufacturer '{c.manufacturer}' contradicts query '{q.manufacturer or q.brand}'")
    return out, notes


def generate_candidates(db: Session, q: ProductQuery) -> tuple[list[MatchCandidate], list[str]]:
    """Run all stages in TSD §28 order and return ranked candidates."""
    out, notes = _stages(db, q)
    ranked = sorted(
        out.values(),
        key=lambda c: (-c.confidence, _METHOD_ORDER[c.method], c.product_name),
    )
    return ranked, notes


def decide(
    candidates: list[MatchCandidate],
    auto_accept: float,
    user_review: float,
) -> tuple[ProductMatchStatus, MatchCandidate | None]:
    """Confidence decision (SDD confidence bands, PROVISIONAL).

    AUTO_MATCHED requires BOTH a high confidence AND a unique candidate
    (clear gap to the runner-up). Anything else that has candidates is
    REVIEW_REQUIRED — a low-confidence candidate never auto-links.
    """
    if not candidates:
        return ProductMatchStatus.NO_MATCH, None
    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    ambiguous = second is not None and (top.confidence - second.confidence) < 0.05
    if top.confidence >= auto_accept and not ambiguous:
        return ProductMatchStatus.AUTO_MATCHED, top
    return ProductMatchStatus.REVIEW_REQUIRED, None
