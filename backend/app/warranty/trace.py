"""Purchase traceability (TSD §42 — "a core product feature").

Builds the full auditable chain for a purchase:

    Purchase -> Original receipt -> Extracted data -> Product
              -> Warranty -> Source -> Expiry -> Claim

Every node carries a ``provenance`` label from a controlled vocabulary so
the UI can — and MUST — distinguish:

    AI_EXTRACTED      produced by the AI pipeline, not yet confirmed
    USER_CONFIRMED    confirmed, corrected, or directly provided by the user
    VERIFIED          deterministic, reproducible match (exact SKU/model)
    EXTERNAL_SOURCE   fact obtained from an external source (policy/site)
    INFERRED          heuristic derivation (fuzzy match, region fallback)
    COMPUTED          deterministic computation (expiry = anchor + duration)

AI-inferred data is NEVER labeled verified; INFERRED and COMPUTED are
explicitly non-verified categories.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.document import Document
from app.models.extraction import ExtractedField, VerificationStatus
from app.models.product import (
    Manufacturer,
    Product,
    ProductMatch,
    ProductMatchMethod,
    ProductMatchStatus,
    Retailer,
)
from app.models.purchase import Purchase
from app.models.warranty import (
    ExtendedWarrantySource,
    PurchaseExtendedWarranty,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantyTimeline,
)
from app.warranty.service import policy_out

PROVENANCE_LABELS: dict[str, str] = {
    "AI_EXTRACTED": "AI Extracted",
    "USER_CONFIRMED": "User Confirmed",
    "VERIFIED": "Verified",
    "EXTERNAL_SOURCE": "External Source",
    "INFERRED": "Inferred",
    "COMPUTED": "Computed",
}

_DETERMINISTIC_MATCHES = {
    ProductMatchMethod.EXACT_SKU,
    ProductMatchMethod.EXACT_MODEL,
    ProductMatchMethod.MANUFACTURER_MODEL,
    ProductMatchMethod.ALIAS_MATCH,
}

_REGION_PROVENANCE = {
    "USER_EXPLICIT": "USER_CONFIRMED",
    "RETAILER_COUNTRY": "INFERRED",
    "USER_ACCOUNT": "INFERRED",
    "UNRESOLVED": None,
}


def _field_out(f: ExtractedField) -> dict:
    provenance = {
        VerificationStatus.AI_EXTRACTED: "AI_EXTRACTED",
        VerificationStatus.USER_CONFIRMED: "USER_CONFIRMED",
        VerificationStatus.USER_CORRECTED: "USER_CONFIRMED",
        VerificationStatus.REJECTED: "REJECTED",
    }[f.verification_status]
    return {
        "field": f.field_name,
        "value": f.normalized_value or f.field_value,
        "original_value": f.original_value,
        "confidence": f.confidence,
        "verification_status": f.verification_status.value,
        "provenance": provenance,
        "provenance_label": PROVENANCE_LABELS.get(provenance, provenance),
        "model": f"{f.model_name} {f.model_version}",
    }


def _product_match_out(match: ProductMatch | None) -> dict | None:
    if match is None:
        return None
    method = match.match_method
    if match.status == ProductMatchStatus.CONFIRMED:
        provenance = "VERIFIED" if method in _DETERMINISTIC_MATCHES else "USER_CONFIRMED"
    elif match.status == ProductMatchStatus.AUTO_MATCHED:
        provenance = "VERIFIED" if method in _DETERMINISTIC_MATCHES else "AI_EXTRACTED"
    else:  # REVIEW_REQUIRED / NO_MATCH — a suggestion, not a linked fact
        provenance = "INFERRED"
    return {
        "method": method.value,
        "confidence": match.match_confidence,
        "status": match.status.value,
        "provenance": provenance,
        "provenance_label": PROVENANCE_LABELS[provenance],
    }


def _mapping_out(m: WarrantyMapping) -> dict:
    if m.verification_status.value in ("USER_CONFIRMED", "USER_CORRECTED"):
        provenance = "USER_CONFIRMED"
    elif m.match_method.value in ("FUZZY_MATCH", "SEMANTIC_MATCH"):
        provenance = "INFERRED"
    elif m.match_method == "MANUAL":
        provenance = "USER_CONFIRMED"
    else:
        provenance = "AI_EXTRACTED"
    return {
        "match_method": m.match_method.value,
        "match_confidence": m.match_confidence,
        "verification_status": m.verification_status.value,
        "mapped_at": m.mapped_at.isoformat() if m.mapped_at else None,
        "provenance": provenance,
        "provenance_label": PROVENANCE_LABELS[provenance],
    }


def _extended_out(ext: PurchaseExtendedWarranty, timelines: list[WarrantyTimeline]) -> dict:
    provenance = {
        ExtendedWarrantySource.USER_DECLARED: "USER_CONFIRMED",
        ExtendedWarrantySource.EXTRACTED: "AI_EXTRACTED",
        ExtendedWarrantySource.EXTERNAL_OFFER: "EXTERNAL_SOURCE",
    }[ext.source_type]
    return {
        "id": ext.id,
        "component": ext.component,
        "duration_value": ext.duration_value,
        "duration_unit": ext.duration_unit.value,
        "start_condition": ext.start_condition.value,
        "source_type": ext.source_type.value,
        "source_detail": ext.source_detail,
        "notes": ext.notes,
        "provenance": provenance,
        "provenance_label": PROVENANCE_LABELS[provenance],
        "timelines": [
            {
                "item_id": t.purchase_item_id,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "expiry_date": t.expiry_date.isoformat() if t.expiry_date else None,
                "status": t.status.value,
                "provenance": "COMPUTED",
                "provenance_label": PROVENANCE_LABELS["COMPUTED"],
            }
            for t in timelines
        ],
    }


def build_purchase_trace(db: Session, purchase: Purchase) -> dict:
    """Assemble the full TSD §42 chain for one purchase (ownership is the
    caller's responsibility)."""
    mfr_by_id: dict[int, str | None] = {}

    def _mfr_name(product: Product | None) -> str | None:
        if product is None or product.manufacturer_id is None:
            return None
        if product.manufacturer_id not in mfr_by_id:
            mfr = db.get(Manufacturer, product.manufacturer_id)
            mfr_by_id[product.manufacturer_id] = mfr.name if mfr else None
        return mfr_by_id[product.manufacturer_id]

    # 1) Purchase -----------------------------------------------------------
    region_method = purchase.warranty_region_resolution_method.value if purchase.warranty_region_resolution_method else None
    region_prov = _REGION_PROVENANCE.get(region_method or "")
    # Purchase data provenance: AI-extracted (from a receipt) until the user
    # confirms; manually created purchases are user-provided.
    if purchase.verification_status.value == "AI_EXTRACTED":
        purchase_prov = "AI_EXTRACTED"
    else:  # DRAFT / USER_CONFIRMED / PARTIALLY_VERIFIED
        purchase_prov = "USER_CONFIRMED"
    retailer = db.get(Retailer, purchase.retailer_id) if purchase.retailer_id else None
    purchase_node = {
        "id": purchase.id,
        "uuid": purchase.uuid,
        "purchase_date": purchase.purchase_date,
        "invoice_number": purchase.invoice_number,
        "retailer_name": retailer.name if retailer else None,
        "total": float(purchase.total) if purchase.total is not None else None,
        "currency": purchase.currency,
        "warranty_region": purchase.warranty_region,
        "warranty_region_resolution_method": region_method,
        "verification_status": purchase.verification_status.value,
        "provenance": purchase_prov,
        "provenance_label": PROVENANCE_LABELS[purchase_prov],
        "region_provenance": region_prov,
        "region_provenance_label": PROVENANCE_LABELS.get(region_prov) if region_prov else None,
        "created_at": purchase.created_at,
    }

    # 2) Original receipt ----------------------------------------------------
    document = db.get(Document, purchase.document_id) if purchase.document_id else None
    receipt_node = None
    if document is not None:
        receipt_node = {
            "document_id": document.id,
            "filename": document.original_filename,
            "mime_type": document.mime_type,
            "page_count": document.page_count,
            "sha256": document.sha256_hash,
            "status": document.status.value,
            "provenance": "USER_CONFIRMED",
            "provenance_label": PROVENANCE_LABELS["USER_CONFIRMED"],
            "created_at": document.created_at,
        }

    # 3) Extracted data (fields of the receipt) ------------------------------
    extracted: list[dict] = []
    if document is not None:
        fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == document.id).order_by(ExtractedField.id)).all()
        extracted = [_field_out(f) for f in fields]

    # Product match for the receipt (per-document resolution record).
    product_match = None
    if document is not None:
        product_match = db.scalar(select(ProductMatch).where(ProductMatch.document_id == document.id))

    # 4) Products (per item) --------------------------------------------------
    products: list[dict] = []
    for item in purchase.items:
        product = db.get(Product, item.product_id) if item.product_id else None
        products.append(
            {
                "item_id": item.id,
                "product_id": item.product_id,
                "raw_name": item.product_name_raw,
                "canonical_name": product.canonical_name if product else None,
                "manufacturer": _mfr_name(product),
                "model_number": item.model_number_raw or (product.model_number if product else None),
                "sku": item.sku_raw or (product.sku if product else None),
                "serial_number": item.serial_number,
                "installation_date": item.installation_date.isoformat() if item.installation_date else None,
                "registration_date": item.registration_date.isoformat() if item.registration_date else None,
                "match": _product_match_out(product_match) if item.product_id else None,
            }
        )

    # 5) Warranty (per item) + 6) Source --------------------------------------
    item_ids = [i.id for i in purchase.items]
    mappings = (
        {m.purchase_item_id: m for m in db.scalars(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id.in_(item_ids))).all()}
        if item_ids
        else {}
    )
    policy_ids = {m.warranty_policy_id for m in mappings.values()}
    policies = (
        {p.id: p for p in db.scalars(select(WarrantyPolicy).where(WarrantyPolicy.id.in_(policy_ids))).all()}
        if policy_ids
        else {}
    )
    exts = (
        list(
            db.scalars(
                select(PurchaseExtendedWarranty)
                .where(PurchaseExtendedWarranty.purchase_id == purchase.id)
                .order_by(PurchaseExtendedWarranty.id)
            ).all()
        )
        if item_ids
        else []
    )
    warranty_nodes: list[dict] = []
    for item in purchase.items:
        m = mappings.get(item.id)
        policy = policies.get(m.warranty_policy_id) if m else None
        warranty_nodes.append(
            {
                "item_id": item.id,
                "mapped": m is not None,
                "mapping": _mapping_out(m) if m else None,
                "policy": policy_out(db, policy) if policy else None,
                "policy_provenance": "EXTERNAL_SOURCE" if policy else None,
                "policy_provenance_label": PROVENANCE_LABELS["EXTERNAL_SOURCE"] if policy else None,
            }
        )

    # 7) Expiry (timelines, per item) ------------------------------------------
    timelines = (
        list(
            db.scalars(
                select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id.in_(item_ids)).order_by(WarrantyTimeline.purchase_item_id, WarrantyTimeline.id)
            ).all()
        )
        if item_ids
        else []
    )
    expiry_nodes: list[dict] = []
    for t in timelines:
        source_kind = "extended warranty" if t.extended_warranty_id is not None else "policy coverage"
        expiry_nodes.append(
            {
                "item_id": t.purchase_item_id,
                "component": t.component,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "expiry_date": t.expiry_date.isoformat() if t.expiry_date else None,
                "duration_value": t.duration_value,
                "duration_unit": t.duration_unit.value if t.duration_unit else None,
                "status": t.status.value,
                "origin": source_kind,
                "provenance": "COMPUTED",
                "provenance_label": PROVENANCE_LABELS["COMPUTED"],
            }
        )

    # 8) Claim (latest for this purchase) --------------------------------------
    claim = db.scalar(
        select(Claim).where(Claim.purchase_id == purchase.id, Claim.user_id == purchase.user_id).order_by(Claim.created_at.desc())
    )
    claim_node = None
    if claim is not None:
        claim_node = {
            "id": claim.id,
            "uuid": claim.uuid,
            "status": claim.status.value,
            "issue_description": claim.issue_description,
            "package_generated_at": claim.package_generated_at.isoformat() if claim.package_generated_at else None,
            "created_at": claim.created_at,
            "provenance": "USER_CONFIRMED",
            "provenance_label": PROVENANCE_LABELS["USER_CONFIRMED"],
        }

    return {
        "purchase": purchase_node,
        "receipt": receipt_node,
        "extracted_data": extracted,
        "products": products,
        "warranty": warranty_nodes,
        "extended_warranties": [_extended_out(e, [t for t in timelines if t.extended_warranty_id == e.id]) for e in exts],
        "expiry": expiry_nodes,
        "claim": claim_node,
        "provenance_legend": PROVENANCE_LABELS,
    }
