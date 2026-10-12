"""Purchase endpoints (TSD §19) + search (SRS FR-14, TSD §55).

GET    /purchases            (search: q, manufacturer, retailer, dates, warranty_status)
POST   /purchases            (manual creation; AI-extracted ones retain provenance)
GET    /purchases/{id}
PATCH  /purchases/{id}
DELETE /purchases/{id}       (retention-hold aware)
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db, get_request_id
from app.models.document import Document
from app.models.product import Product, Retailer
from app.models.purchase import Purchase, PurchaseItem
from app.models.user import User
from app.models.warranty import WarrantyTimeline
from app.schemas.purchase import PurchaseCreate, PurchaseUpdate
from app.schemas.warranty import ExtendedWarrantyCreate
from app.services import purchase_service

router = APIRouter()


def _product_name(product_id: int | None, db: Session) -> tuple[str | None, str | None]:
    if product_id is None:
        return None, None
    product = db.get(Product, product_id)
    if product is None:
        return None, None
    from app.models.product import Manufacturer

    mfr = db.get(Manufacturer, product.manufacturer_id) if product.manufacturer_id else None
    return product.canonical_name, (mfr.name if mfr else product.brand)


def purchase_out(p: Purchase, db: Session) -> dict:
    document = db.get(Document, p.document_id) if p.document_id else None
    retailer = db.get(Retailer, p.retailer_id) if p.retailer_id else None
    items = []
    for item in p.items:
        canonical, mfr = _product_name(item.product_id, db)
        items.append(
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name_raw,
                "product_canonical_name": canonical,
                "manufacturer_name": mfr,
                "sku": item.sku_raw,
                "model_number": item.model_number_raw,
                "serial_number": item.serial_number,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price) if item.unit_price is not None else None,
                "line_total": float(item.line_total) if item.line_total is not None else None,
                "installation_date": item.installation_date.isoformat() if item.installation_date else None,
                "registration_date": item.registration_date.isoformat() if item.registration_date else None,
            }
        )
    from app.warranty.rules.coverage import (
        coverage_status_for_purchase,
        warranty_bucket_for_purchase,
    )

    return {
        "id": p.id,
        "uuid": p.uuid,
        "document_id": p.document_id,
        "document_status": document.status.value if document else None,
        "document_filename": document.original_filename if document else None,
        "retailer_name": retailer.name if retailer else None,
        "purchase_date": p.purchase_date,
        "warranty_status": coverage_status_for_purchase(db, p),
        "warranty_bucket": warranty_bucket_for_purchase(db, p),
        "invoice_number": p.invoice_number,
        "currency": p.currency,
        "subtotal": float(p.subtotal) if p.subtotal is not None else None,
        "tax": float(p.tax) if p.tax is not None else None,
        "total": float(p.total) if p.total is not None else None,
        "warranty_region": p.warranty_region,
        "warranty_region_resolution_method": p.warranty_region_resolution_method.value if p.warranty_region_resolution_method else None,
        "verification_status": p.verification_status.value,
        "items": items,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("")
def list_purchases(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = None,
    manufacturer: str | None = None,
    retailer: str | None = None,
    date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    warranty_status: str | None = None,
    document_id: int | None = Query(None, ge=1),
    sort: str = Query("created_desc", pattern=r"^(created_desc|date_desc|date_asc)$"),
):
    rows, total = purchase_service.list_purchases(
        db,
        user,
        page=page,
        page_size=page_size,
        q=q,
        manufacturer=manufacturer,
        retailer=retailer,
        date_from=date_from,
        date_to=date_to,
        warranty_status=warranty_status,
        document_id=document_id,
        sort=sort,
    )
    return envelope.ok(
        {"items": [purchase_out(p, db) for p in rows], "total": total, "page": page, "page_size": page_size},
        request,
    )


@router.post("", status_code=201)
def create_purchase(request: Request, body: PurchaseCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    purchase = purchase_service.create_purchase(
        db, user=user, data=body.model_dump(), request=request, request_id=get_request_id(request)
    )
    return envelope.ok({"purchase": purchase_out(purchase, db)}, request)


@router.get("/{purchase_id}")
def get_purchase(request: Request, purchase_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    return envelope.ok({"purchase": purchase_out(purchase, db)}, request)


@router.get("/{purchase_id}/warranty")
def purchase_warranty(request: Request, purchase_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Warranty records for an owned purchase (TSD §14/§15).

    Ownership is enforced via get_purchase (404 for other users' purchases).
    Returns the resolved policy + coverages + computed timeline per item.
    """
    from sqlalchemy import select

    from app.models.warranty import (
        WarrantyCoverage,
        WarrantyMapping,
        WarrantyPolicy,
        WarrantySource,
        WarrantyTimeline,
    )

    purchase = purchase_service.get_purchase(db, user, purchase_id)
    item_ids = [i.id for i in purchase.items]
    mappings = (
        db.scalars(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id.in_(item_ids))).all()
        if item_ids
        else []
    )
    policy_ids = {m.warranty_policy_id for m in mappings}
    policies = (
        {p.id: p for p in db.scalars(select(WarrantyPolicy).where(WarrantyPolicy.id.in_(policy_ids))).all()}
        if policy_ids
        else {}
    )
    coverage_rows = (
        db.scalars(select(WarrantyCoverage).where(WarrantyCoverage.policy_id.in_(policy_ids))).all()
        if policy_ids
        else []
    )
    timeline_rows = (
        db.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id.in_(item_ids))).all()
        if item_ids
        else []
    )

    def policy_out(policy: WarrantyPolicy) -> dict:
        source = db.get(WarrantySource, policy.source_id) if policy.source_id else None
        return {
            "id": policy.id,
            "policy_name": policy.policy_name,
            "policy_version": policy.policy_version,
            "region": policy.region,
            "effective_from": policy.effective_from.isoformat(),
            "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
            "status": policy.status.value,
            "source": (
                {
                    "source_type": source.source_type.value,
                    "source_url": source.source_url,
                    "source_title": source.source_title,
                    "trust_level": source.trust_level,
                    "retrieved_at": source.retrieved_at.isoformat() if source.retrieved_at else None,
                }
                if source
                else None
            ),
            "coverages": [
                {
                    "id": c.id,
                    "component": c.component,
                    "duration_value": c.duration_value,
                    "duration_unit": c.duration_unit.value,
                    "start_condition": c.start_condition.value,
                    "registration_required": c.registration_required,
                    "description": c.coverage_description,
                    "exclusions": c.exclusions,
                }
                for c in coverage_rows
                if c.policy_id == policy.id
            ],
        }

    def item_out(item_id: int) -> dict:
        item = db.get(PurchaseItem, item_id)
        m = next((mm for mm in mappings if mm.purchase_item_id == item_id), None)
        tl = [t for t in timeline_rows if t.purchase_item_id == item_id]
        return {
            "item_id": item_id,
            "product_name": item.product_name_raw if item else None,
            "serial_number": item.serial_number if item else None,
            "mapped": m is not None,
            "match_method": m.match_method.value if m else None,
            "match_confidence": m.match_confidence if m else None,
            "verification_status": m.verification_status.value if m else None,
            "policy": policy_out(policies[m.warranty_policy_id]) if (m and m.warranty_policy_id in policies) else None,
            "timelines": [
                {
                    "component": t.component,
                    "start_date": t.start_date.isoformat() if t.start_date else None,
                    "expiry_date": t.expiry_date.isoformat() if t.expiry_date else None,
                    "status": t.status.value,
                    "is_extended": t.extended_warranty_id is not None,
                }
                for t in tl
            ],
        }

    items = [item_out(i) for i in item_ids]
    return envelope.ok(
        {"purchase_id": purchase.id, "warranty_region": purchase.warranty_region, "items": items}, request
    )


@router.get("/{purchase_id}/trace")
def purchase_trace(request: Request, purchase_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Full traceability chain (TSD §42): purchase -> receipt -> extracted
    data -> product -> warranty -> source -> expiry -> claim, with a
    provenance label on every node (AI Extracted / User Confirmed /
    Verified / External Source / Inferred / Computed)."""
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    from app.warranty.trace import build_purchase_trace

    return envelope.ok(build_purchase_trace(db, purchase), request)


def _extended_out(db: Session, ext) -> dict:

    tl = (
        db.scalars(select(WarrantyTimeline).where(WarrantyTimeline.extended_warranty_id == ext.id)).all()
    )
    return {
        "id": ext.id,
        "purchase_item_id": ext.purchase_item_id,
        "component": ext.component,
        "duration_value": ext.duration_value,
        "duration_unit": ext.duration_unit.value,
        "start_condition": ext.start_condition.value,
        "source_type": ext.source_type.value,
        "source_detail": ext.source_detail,
        "notes": ext.notes,
        "created_at": ext.created_at,
        "timelines": [
            {
                "item_id": t.purchase_item_id,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "expiry_date": t.expiry_date.isoformat() if t.expiry_date else None,
                "status": t.status.value,
            }
            for t in tl
        ],
    }


@router.get("/{purchase_id}/extended-warranties")
def list_extended_warranties(request: Request, purchase_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    from app.models.warranty import PurchaseExtendedWarranty

    exts = db.scalars(
        select(PurchaseExtendedWarranty).where(PurchaseExtendedWarranty.purchase_id == purchase.id).order_by(
            PurchaseExtendedWarranty.id
        )
    ).all()
    return envelope.ok({"items": [_extended_out(db, e) for e in exts], "total": len(exts)}, request)


@router.post("/{purchase_id}/extended-warranties", status_code=201)
def add_extended_warranty_endpoint(
    request: Request,
    purchase_id: int,
    body: ExtendedWarrantyCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    from app.warranty.service import add_extended_warranty

    ext = add_extended_warranty(
        db,
        purchase,
        data=body.model_dump(exclude_unset=True),
        user=user,
        request_id=get_request_id(request),
    )
    return envelope.ok({"extended_warranty": _extended_out(db, ext)}, request)


@router.delete("/{purchase_id}/extended-warranties/{extended_warranty_id}")
def delete_extended_warranty_endpoint(
    request: Request,
    purchase_id: int,
    extended_warranty_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    from app.warranty.service import remove_extended_warranty

    remove_extended_warranty(
        db,
        purchase,
        extended_warranty_id,
        user=user,
        request_id=get_request_id(request),
    )
    return envelope.ok({"deleted": True}, request)


@router.patch("/{purchase_id}")
def update_purchase(request: Request, purchase_id: int, body: PurchaseUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    purchase = purchase_service.update_purchase(
        db, user=user, purchase_id=purchase_id, data=body.model_dump(exclude_unset=True), request=request, request_id=get_request_id(request)
    )
    return envelope.ok({"purchase": purchase_out(purchase, db)}, request)


@router.delete("/{purchase_id}")
def delete_purchase(request: Request, purchase_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = purchase_service.delete_purchase(
        db, user=user, purchase_id=purchase_id, request=request, request_id=get_request_id(request)
    )
    return envelope.ok(result, request)
