"""Product Intelligence service (SRS ML-04/ML-06/ML-07, TSD §20/§28).

Responsibilities:
- resolve document extractions against the product catalog (staged,
  manufacturer-constrained, confidence-scored);
- persist the full resolution record (original + normalized values,
  candidate list, method, confidence, evidence);
- user confirmation (confirm / reject / create-from-receipt);
- ad-hoc catalog search and resolution (TSD §20 Product API).

False-positive prevention: a candidate is linked to the document ONLY when
the decision is AUTO_MATCHED (high confidence + unambiguous) or CONFIRMED
(explicit user action). Low-confidence or ambiguous candidates stay
REVIEW_REQUIRED with product_id NULL.
"""

import logging
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.product.matching import (
    MatchCandidate,
    ProductQuery,
    decide,
    generate_candidates,
)
from app.ai.product.normalization import (
    make_name_key,
    normalize_manufacturer,
    normalize_model,
    normalize_retailer,
    normalize_sku,
)
from app.ai.registry import (
    PRODUCT_RANKER_MODEL_NAME as _MATCHER_NAME,
)
from app.ai.registry import (
    PRODUCT_RANKER_MODEL_VERSION as _MATCHER_VERSION,
)
from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.document import Document
from app.models.extraction import ExtractedField, VerificationStatus
from app.models.product import (
    EntityStatus,
    Manufacturer,
    Product,
    ProductAlias,
    ProductMatch,
    ProductMatchMethod,
    ProductMatchStatus,
    Retailer,
    RetailerCatalogEntry,
)
from app.models.user import User

logger = logging.getLogger(__name__)

# _MATCHER_NAME / _MATCHER_VERSION come from the model registry (TSD §45) —
# versions are never hard-coded in business logic.


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- identity helpers --------------------------------------------------------

def get_or_create_manufacturer(db: Session, name: str | None) -> Manufacturer | None:
    if not name or not name.strip():
        return None
    canonical = normalize_manufacturer(name) or name.strip()
    key = make_name_key(canonical)
    m = db.scalar(select(Manufacturer).where(Manufacturer.name_key == key))
    if m is None:
        m = Manufacturer(name=canonical, name_key=key)
        db.add(m)
        db.flush()
    return m


def get_or_create_retailer(db: Session, name: str | None) -> Retailer | None:
    if not name or not name.strip():
        return None
    canonical = normalize_retailer(name) or name.strip()
    key = make_name_key(canonical)
    r = db.scalar(select(Retailer).where(Retailer.name_key == key))
    if r is None:
        r = Retailer(name=canonical, name_key=key)
        db.add(r)
        db.flush()
    return r


# --- outputs -----------------------------------------------------------------

def product_out(p: Product, db: Session) -> dict:
    mfr = db.get(Manufacturer, p.manufacturer_id) if p.manufacturer_id else None
    return {
        "id": p.id,
        "manufacturer": mfr.name if mfr else None,
        "brand": p.brand,
        "canonical_name": p.canonical_name,
        "category": p.category,
        "model_number": p.model_number,
        "sku": p.sku,
        "status": p.status.value,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def match_out(m: ProductMatch | None, db: Session | None = None) -> dict | None:
    if m is None:
        return None
    product = db.get(Product, m.product_id) if (db and m.product_id) else None
    return {
        "id": m.id,
        "product_id": m.product_id,
        "product": product_out(product, db) if (product and db) else None,
        "match_method": m.match_method.value,
        "match_confidence": m.match_confidence,
        "status": m.status.value,
        "product_name_raw": m.product_name_raw,
        "sku_raw": m.sku_raw,
        "model_raw": m.model_raw,
        "manufacturer_raw": m.manufacturer_raw,
        "retailer_raw": m.retailer_raw,
        "sku_normalized": m.sku_normalized,
        "model_normalized": m.model_normalized,
        "candidates": m.candidates or [],
        "evidence": m.evidence or [],
        "match_engine": _MATCHER_NAME,
        "match_engine_version": _MATCHER_VERSION,
        "confirmed_by": m.confirmed_by,
        "confirmed_at": m.confirmed_at.isoformat() if m.confirmed_at else None,
    }


# --- document resolution -----------------------------------------------------

def _fields_by_name(db: Session, document_id: int) -> dict[str, str | None]:
    rows = db.scalars(select(ExtractedField).where(ExtractedField.document_id == document_id)).all()
    out: dict[str, str | None] = {}
    for r in rows:
        out.setdefault(r.field_name, r.field_value)
    return out


def resolve_document_product(
    db: Session,
    document: Document,
    fields: dict[str, str | None] | None = None,
    *,
    force: bool = False,
) -> ProductMatch:
    """Run product resolution for a document and persist the record.

    Confirmed/rejected matches are never overwritten by reprocessing
    (user decisions win).
    """
    existing = db.scalar(select(ProductMatch).where(ProductMatch.document_id == document.id))
    if existing and existing.status in (ProductMatchStatus.CONFIRMED, ProductMatchStatus.REJECTED) and not force:
        return existing

    fields = fields if fields is not None else _fields_by_name(db, document.id)
    q = ProductQuery(
        product_name=fields.get("product_name"),
        sku=fields.get("sku"),
        model_number=fields.get("model_number"),
        manufacturer=fields.get("manufacturer"),
        retailer=fields.get("retailer"),
    )
    if not q.text() and not q.sku and not q.model_number:
        # Nothing to match against — record NO_MATCH, never guess.
        candidates: list[MatchCandidate] = []
        notes: list[str] = []
        status, top = ProductMatchStatus.NO_MATCH, None
    else:
        settings = get_settings()
        candidates, notes = generate_candidates(db, q)
        status, top = decide(candidates, settings.confidence_auto_accept, settings.confidence_user_review)

    if existing:
        m = existing
    else:
        m = ProductMatch(document_id=document.id)
        db.add(m)
    m.product_id = (
        top.product_id
        if (status == ProductMatchStatus.AUTO_MATCHED and top is not None)
        else (m.product_id or None)
    )
    m.match_method = top.method if top else ProductMatchMethod.NONE
    m.match_confidence = round(top.confidence, 4) if top else None
    m.status = status
    m.product_name_raw = q.product_name
    m.sku_raw = q.sku
    m.model_raw = q.model_number
    m.manufacturer_raw = q.manufacturer
    m.retailer_raw = q.retailer
    m.sku_normalized = normalize_sku(q.sku) or None
    m.model_normalized = normalize_model(q.model_number) or None
    m.candidates = [c.to_dict() for c in candidates]
    m.evidence = (top.evidence if top else []) + notes
    db.flush()
    return m


# --- user confirmation -------------------------------------------------------

def get_document_match(db: Session, user: User, document_id: int) -> ProductMatch:
    document = db.get(Document, document_id)
    if document is None or document.user_id != user.id:
        raise NotFoundError("Document not found")
    m = db.scalar(select(ProductMatch).where(ProductMatch.document_id == document.id))
    if m is None:
        # Resolve lazily if the pipeline predates product intelligence.
        m = resolve_document_product(db, document)
        db.commit()
    return m


def _set_field_verification(
    db: Session,
    document_id: int,
    field_name: str,
    status: VerificationStatus,
    value: str | None,
) -> None:
    row = db.scalar(
        select(ExtractedField).where(
            ExtractedField.document_id == document_id,
            ExtractedField.field_name == field_name,
        )
    )
    if row is None:
        return
    # Preserve the AI original (SRS FR-17 / ML-06): when the effective value
    # changes, the previous AI value moves to original_value — the receipt
    # value itself is also stored verbatim on the product match record.
    if value is not None and value != row.field_value:
        if row.verification_status == VerificationStatus.AI_EXTRACTED:
            row.original_value = row.field_value
        row.field_value = value
        row.normalized_value = value
    row.verification_status = status


def confirm_product_match(
    db: Session,
    *,
    user: User,
    document: Document,
    product_id: int | None,
    request: Request | None = None,
    request_id: str | None = None,
) -> ProductMatch:
    """User confirms a candidate product (or rejects all candidates)."""
    m = db.scalar(select(ProductMatch).where(ProductMatch.document_id == document.id))
    if m is None:
        m = resolve_document_product(db, document)

    if product_id is None:
        m.status = ProductMatchStatus.REJECTED
        m.product_id = None
        m.match_method = ProductMatchMethod.NONE
        m.match_confidence = None
    else:
        product = db.get(Product, product_id)
        if product is None:
            raise ValidationError("Product not found")
        m.product_id = product.id
        m.status = ProductMatchStatus.CONFIRMED
        # Confirming the top candidate keeps the discovery method/confidence
        # (honest provenance); choosing a different candidate is MANUAL.
        top_id = None
        if m.candidates:
            top_id = (m.candidates[0] or {}).get("product_id")
        if product_id != top_id:
            m.match_method = ProductMatchMethod.MANUAL
            m.match_confidence = 1.0
        m.evidence = list(m.evidence or []) + [f"user confirmed product '{product.canonical_name}'"]
        _set_field_verification(
            db, document.id, "product_name", VerificationStatus.USER_CONFIRMED, product.canonical_name
        )
    m.confirmed_by = user.id
    m.confirmed_at = utcnow()
    db.flush()

    # Shared re-routing: the document is COMPLETED only when every review
    # reason is resolved (fields, pages, AND this product match).
    from app.services.extraction_service import _refresh_document_status

    _refresh_document_status(db, document)

    audit_helper.audit(
        db,
        action=AuditAction.PRODUCT_CONFIRMED if product_id else AuditAction.PRODUCT_REJECTED,
        resource_type="document",
        resource_id=document.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=request.client.host if request and request.client else None,
        metadata={
            "product_id": product_id,
            "match_method": m.match_method.value,
            "previous_status": m.status.value,
        },
    )
    logger.info(
        "product_match_user_decision doc_id=%s product_id=%s status=%s user_id=%s",
        document.id,
        product_id,
        m.status.value,
        user.id,
    )
    return m


def create_product_from_receipt(
    db: Session,
    *,
    user: User,
    document: Document,
    request: Request | None = None,
    request_id: str | None = None,
) -> tuple[ProductMatch, Product]:
    """Create a catalog product from the document's receipt values (user
    action) and confirm it. The product is sourced from real document
    evidence — never invented."""
    m = db.scalar(select(ProductMatch).where(ProductMatch.document_id == document.id))
    if m is None:
        m = resolve_document_product(db, document)
    if not m.product_name_raw:
        raise ValidationError("Document has no product name to create a product from")

    manufacturer = get_or_create_manufacturer(db, m.manufacturer_raw)
    product = Product(
        manufacturer_id=manufacturer.id if manufacturer else None,
        brand=normalize_manufacturer(m.manufacturer_raw) if m.manufacturer_raw else None,
        canonical_name=m.product_name_raw.strip(),
        model_number=m.model_raw,
        sku=m.sku_raw,
    )
    db.add(product)
    db.flush()
    # The receipt's raw text IS the canonical name here (user-confirmed
    # truth); the verbatim original is preserved on the match record.
    confirm_product_match(
        db,
        user=user,
        document=document,
        product_id=product.id,
        request=request,
        request_id=request_id,
    )
    return m, product


# --- catalog API (TSD §20) ---------------------------------------------------

def search_products(
    db: Session, *, q: str | None = None, retailer: str | None = None, limit: int = 20
) -> list[dict]:
    stmt = select(Product).where(Product.status == EntityStatus.ACTIVE)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                Product.canonical_name.ilike(needle),
                Product.model_number.ilike(needle),
                Product.sku.ilike(needle),
                Product.brand.ilike(needle),
            )
        )
    rows = db.scalars(stmt.limit(limit)).all()
    out = []
    for p in rows:
        d = product_out(p, db)
        if retailer:
            rkey = make_name_key(normalize_retailer(retailer) or retailer)
            entries = db.scalars(
                select(RetailerCatalogEntry).where(RetailerCatalogEntry.product_id == p.id)
            ).all()
            for e in entries:
                r = db.get(Retailer, e.retailer_id)
                if r and make_name_key(r.name) == rkey:
                    d["catalog_listing"] = {
                        "retailer": r.name,
                        "catalog_sku": e.catalog_sku,
                        "catalog_model": e.catalog_model,
                        "catalog_name": e.catalog_name,
                    }
                    break
        out.append(d)
    return out


def get_product(db: Session, product_id: int) -> dict:
    p = db.get(Product, product_id)
    if p is None:
        raise NotFoundError("Product not found")
    aliases = db.scalars(select(ProductAlias).where(ProductAlias.product_id == product_id)).all()
    d = product_out(p, db)
    d["aliases"] = [
        {
            "alias_text": a.alias_text,
            "alias_type": a.alias_type.value,
            "source": a.source,
        }
        for a in aliases
    ]
    return d


def resolve_adhoc(db: Session, payload: dict) -> dict:
    """TSD §20 POST /products/resolve — ad-hoc resolution without a document."""
    settings = get_settings()
    q = ProductQuery(
        product_name=payload.get("description") or payload.get("product_name"),
        sku=payload.get("sku"),
        model_number=payload.get("model_number"),
        manufacturer=payload.get("manufacturer"),
        retailer=payload.get("retailer"),
        brand=payload.get("brand"),
    )
    candidates, notes = generate_candidates(db, q)
    status, top = decide(candidates, settings.confidence_auto_accept, settings.confidence_user_review)
    return {
        "candidate_product": top.to_dict() if top else None,
        "confidence": round(top.confidence, 4) if top else None,
        "match_method": top.method.value if top else "NONE",
        "supporting_evidence": (top.evidence if top else []) + notes,
        "requires_confirmation": status != ProductMatchStatus.AUTO_MATCHED,
        "status": status.value,
        "candidates": [c.to_dict() for c in candidates],
        "match_engine": _MATCHER_NAME,
        "match_engine_version": _MATCHER_VERSION,
    }
