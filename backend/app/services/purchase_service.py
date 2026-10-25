"""Purchase service (TSD §19) including warranty region resolution (SRS FR-11a).

Region resolution order (recorded for auditability):
  1. USER_EXPLICIT       — region explicitly set for this purchase
  2. RETAILER_COUNTRY    — the retailer's known country of operation
  3. USER_ACCOUNT        — the user's account-level country
If none resolves with confidence, the purchase's warranty status must be
UNKNOWN (SRS FR-11a) — we store warranty_region=None + method=UNRESOLVED and
never silently default to a region.
"""

import logging
from datetime import UTC, date, datetime

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.exceptions import NotFoundError, ValidationError
from app.models.document import Document
from app.models.product import Product, Retailer
from app.models.purchase import (
    Purchase,
    PurchaseItem,
    PurchaseVerification,
    RegionResolution,
)
from app.models.user import User

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def resolve_region(
    *,
    explicit_region: str | None,
    retailer: Retailer | None,
    user: User,
) -> tuple[str | None, RegionResolution]:
    """Deterministic region resolution per SRS FR-11a."""
    if explicit_region:
        return explicit_region.upper(), RegionResolution.USER_EXPLICIT
    if retailer is not None and retailer.country:
        return retailer.country.upper(), RegionResolution.RETAILER_COUNTRY
    if user.country:
        return user.country.upper(), RegionResolution.USER_ACCOUNT
    return None, RegionResolution.UNRESOLVED


def get_purchase(db: Session, user: User, purchase_id: int) -> Purchase:
    purchase = db.get(Purchase, purchase_id)
    if purchase is None or purchase.user_id != user.id or purchase.deleted_at is not None:
        raise NotFoundError("Purchase not found")
    return purchase


def list_purchases(
    db: Session,
    user: User,
    *,
    page: int,
    page_size: int,
    q: str | None = None,
    manufacturer: str | None = None,
    retailer: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    warranty_status: str | None = None,
    document_id: int | None = None,
    sort: str = "created_desc",
) -> tuple[list[Purchase], int]:
    """Search endpoint (SRS FR-14, TSD §55).

    Text `q` matches product name / model / SKU / invoice number across the
    purchase's items. `warranty_status` joins the coverage table once warranty
    mapping exists (Phase 4); until then it is accepted and matched against
    the computed coverage timeline when present.
    """
    from app.warranty.rules.coverage import (  # lazy: Phase 4 module
        coverage_status_for_purchase,
        warranty_bucket_for_purchase,
    )

    base = select(Purchase).where(Purchase.user_id == user.id, Purchase.deleted_at.is_(None))
    if manufacturer:
        base = base.join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id).join(
            Product, Product.id == PurchaseItem.product_id
        )
        base = base.where(func.lower(Product.brand).like(f"%{manufacturer.lower()}%"))
    if retailer:
        from app.models.product import Retailer as R

        base = base.join(R, R.id == Purchase.retailer_id, isouter=True).where(
            func.lower(R.name).like(f"%{retailer.lower()}%")
        )
    if date_from:
        base = base.where(Purchase.purchase_date >= date_from)
    if date_to:
        base = base.where(Purchase.purchase_date <= date_to)
    if document_id is not None:
        base = base.where(Purchase.document_id == document_id)

    # Free-text search across item fields (and raw product name).
    if q:
        like = f"%{q}%"
        item_match = (
            PurchaseItem.product_name_raw.ilike(like)
            | PurchaseItem.sku_raw.ilike(like)
            | PurchaseItem.model_number_raw.ilike(like)
        )
        base = base.join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id).where(item_match)

    if sort == "date_desc":
        order: tuple = (Purchase.purchase_date.desc().nullslast(), Purchase.created_at.desc())
    elif sort == "date_asc":
        order = (Purchase.purchase_date.asc().nullsfirst(), Purchase.created_at.asc())
    else:
        order = (Purchase.created_at.desc(),)

    if warranty_status:
        # Warranty status is computed from the coverage timelines (TSD §15),
        # so it is applied in memory; total must reflect the filtered set.
        # "NEEDS_REVIEW" is a meta-filter for incomplete / unknown warranties.
        all_rows = db.scalars(base.distinct().order_by(*order)).all()
        wanted = warranty_status.upper()

        def _matches(p: Purchase) -> bool:
            if wanted == "NEEDS_REVIEW":
                return warranty_bucket_for_purchase(db, p) in ("incomplete", "unknown")
            return (coverage_status_for_purchase(db, p) or "UNKNOWN") == wanted

        purchases = [p for p in all_rows if _matches(p)]
        total = len(purchases)
        purchases = purchases[(page - 1) * page_size : page * page_size]
    else:
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(base.distinct().order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
        purchases = list(rows)

    return purchases, total


def create_purchase(
    db: Session,
    *,
    user: User,
    data: dict,
    request: Request | None = None,
    request_id: str | None = None,
) -> Purchase:
    retailer = None
    if data.get("retailer_id"):
        retailer = db.get(Retailer, data["retailer_id"])
        if retailer is None:
            raise ValidationError("Retailer not found")
    elif data.get("retailer_name"):
        # Free-text retailer (e.g. a confirmed document field) — resolve to
        # an existing Retailer or create one, rather than silently dropping
        # it and leaving the purchase with no retailer at all.
        from app.services.product_service import get_or_create_retailer

        retailer = get_or_create_retailer(db, data["retailer_name"])
    document = None
    if data.get("document_id"):
        document = db.get(Document, data["document_id"])
        if document is None or document.user_id != user.id:
            raise ValidationError("Document not found or not owned by you")

    region, method = resolve_region(
        explicit_region=data.get("warranty_region"), retailer=retailer, user=user
    )

    purchase = Purchase(
        user_id=user.id,
        document_id=document.id if document else None,
        retailer_id=retailer.id if retailer else None,
        purchase_date=data.get("purchase_date"),
        invoice_number=data.get("invoice_number"),
        currency=(data.get("currency") or "").upper() or None,
        subtotal=data.get("subtotal"),
        tax=data.get("tax"),
        total=data.get("total"),
        warranty_region=region,
        warranty_region_resolution_method=method,
        verification_status=PurchaseVerification.DRAFT,
    )
    db.add(purchase)
    db.flush()

    for item_data in data.get("items") or []:
        _apply_item(db, purchase, item_data)

    db.commit()
    db.refresh(purchase)

    # Warranty intelligence (Phase 4): map items to warranty policies and
    # compute coverage timelines. Best-effort — a warranty failure must not
    # fail the purchase itself; the user can still see & fix the mapping.
    # Warranty changes are committed independently so an alert-scheduling
    # failure cannot roll them back, and a warranty failure cannot leave
    # partial mapping/timeline records (db.rollback() undoes the flush).
    try:
        _sync_warranties(db, purchase, user, request_id=request_id, reason="purchase_created")
        db.commit()  # commit warranty mappings/timelines independently
    except Exception:  # noqa: BLE001
        logger.exception("warranty_sync_failed purchase_id=%s reason=purchase_created", purchase.id)
        db.rollback()  # roll back uncommitted warranty changes only; purchase is safe

    # Alert scheduling (SRS FR-15): best-effort; never fails the purchase or
    # the warranty mapping that was just committed. `_schedule_alerts_best_effort`
    # rolls back its own uncommitted notification changes on failure.
    _schedule_alerts_best_effort(db, purchase.id)

    audit_helper.audit(
        db,
        action=AuditAction.PURCHASE_CREATED,
        resource_type="purchase",
        resource_id=purchase.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=request.client.host if request and request.client else None,
        metadata={
            "region": region,
            "region_method": method.value,
            "items": len(purchase.items),
        },
    )
    logger.info("purchase_created purchase_id=%s user_id=%s", purchase.id, user.id)
    return purchase


def _sync_warranties(db: Session, purchase: Purchase, user: User, *, request_id: str | None, reason: str) -> dict:
    from app.warranty.service import sync_purchase_warranties

    return sync_purchase_warranties(db, purchase, actor=user, request_id=request_id, reason=reason)


def _schedule_alerts_best_effort(db: Session, purchase_id: int) -> None:
    """SRS FR-15: schedule expiry reminders right after a warranty sync.

    Never fails the purchase — the worker sweep is the canonical path.
    """
    try:
        from app.services.notification_service import schedule_warranty_reminders

        schedule_warranty_reminders(db)
    except Exception:  # noqa: BLE001
        logger.exception("alert_scheduling_failed purchase_id=%s", purchase_id)
        db.rollback()


def _apply_item(db: Session, purchase: Purchase, item_data: dict) -> None:
    # A user-confirmed (or unambiguously auto-matched) product link on the
    # document wins over whatever product_id the client sends — the match
    # record is the auditable source of truth (SRS ML-06/ML-07).
    product_id = item_data.get("product_id")
    if purchase.document_id is not None:
        from app.models.product import ProductMatch, ProductMatchStatus

        match = db.scalar(
            select(ProductMatch)
            .where(ProductMatch.document_id == purchase.document_id)
            .where(
                ProductMatch.status.in_([ProductMatchStatus.CONFIRMED, ProductMatchStatus.AUTO_MATCHED])
            )
        )
        if match is not None and match.product_id is not None:
            product_id = match.product_id
    product = None
    if product_id:
        product = db.get(Product, product_id)
        if product is None:
            raise ValidationError("Product not found")
    item = PurchaseItem(
        purchase_id=purchase.id,
        product_id=product.id if product else None,
        product_name_raw=item_data.get("product_name") or (product.canonical_name if product else None),
        sku_raw=item_data.get("sku") or (product.sku if product else None),
        model_number_raw=item_data.get("model_number") or (product.model_number if product else None),
        serial_number=item_data.get("serial_number"),
        quantity=item_data.get("quantity"),
        unit_price=item_data.get("unit_price"),
        line_total=item_data.get("line_total"),
        installation_date=_parse_iso_date(item_data.get("installation_date")),
        registration_date=_parse_iso_date(item_data.get("registration_date")),
    )
    db.add(item)


def _parse_iso_date(value: object) -> date | None:
    """Parse an optional ISO YYYY-MM-DD string (None / invalid -> None)."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def update_purchase(
    db: Session,
    *,
    user: User,
    purchase_id: int,
    data: dict,
    request: Request | None = None,
    request_id: str | None = None,
) -> Purchase:
    purchase = get_purchase(db, user, purchase_id)
    changed: dict = {}

    if "purchase_date" in data and data["purchase_date"] is not None:
        purchase.purchase_date = data["purchase_date"]
        changed["purchase_date"] = data["purchase_date"]
    if "invoice_number" in data and data["invoice_number"] is not None:
        purchase.invoice_number = data["invoice_number"]
    if "currency" in data and data["currency"] is not None:
        purchase.currency = data["currency"].upper()
    for money in ("subtotal", "tax", "total"):
        if data.get(money) is not None:
            setattr(purchase, money, data[money])
    if data.get("retailer_id"):
        retailer = db.get(Retailer, data["retailer_id"])
        if retailer is None:
            raise ValidationError("Retailer not found")
        purchase.retailer_id = retailer.id
    elif data.get("retailer_name"):
        from app.services.product_service import get_or_create_retailer

        retailer = get_or_create_retailer(db, data["retailer_name"])
        if retailer is not None:
            purchase.retailer_id = retailer.id

    if "warranty_region" in data and data["warranty_region"] is not None:
        region, method = resolve_region(
            explicit_region=data["warranty_region"],
            retailer=db.get(Retailer, purchase.retailer_id) if purchase.retailer_id else None,
            user=user,
        )
        purchase.warranty_region = region
        purchase.warranty_region_resolution_method = method
        changed["warranty_region"] = region
        changed["region_method"] = method.value

    if data.get("items") is not None:
        for item in list(purchase.items):
            db.delete(item)
        db.flush()
        for item_data in data["items"]:
            _apply_item(db, purchase, item_data)
        changed["items"] = len(data["items"])

    purchase.verification_status = PurchaseVerification.USER_CONFIRMED if changed else purchase.verification_status
    db.commit()
    db.refresh(purchase)

    # Warranty re-sync: item/product/region/date changes can change the
    # applicable policy and the computed coverage windows. Committed
    # independently so an alert-scheduling failure cannot roll back
    # successfully committed warranty synchronization (same pattern as
    # create_purchase).
    if changed:
        try:
            _sync_warranties(db, purchase, user, request_id=request_id, reason="purchase_updated")
            db.commit()  # commit warranty mappings/timelines independently
        except Exception:  # noqa: BLE001
            logger.exception("warranty_sync_failed purchase_id=%s reason=purchase_updated", purchase.id)
            db.rollback()  # roll back uncommitted warranty changes only; purchase is safe

    # Alert scheduling (SRS FR-15): best-effort; never fails the purchase or
    # the warranty mapping that was just committed.
    if changed:
        _schedule_alerts_best_effort(db, purchase.id)

    audit_helper.audit(
        db,
        action=AuditAction.PURCHASE_UPDATED,
        resource_type="purchase",
        resource_id=purchase.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=request.client.host if request and request.client else None,
        metadata={"fields": sorted(changed.keys())},
    )
    return purchase


def delete_purchase(
    db: Session,
    *,
    user: User,
    purchase_id: int,
    request: Request | None = None,
    request_id: str | None = None,
) -> dict:
    purchase = get_purchase(db, user, purchase_id)

    from app.models.claim import TERMINAL_STATUSES, Claim

    open_claims = db.scalars(
        select(Claim).where(Claim.purchase_id == purchase.id, Claim.status.notin_(list(TERMINAL_STATUSES)))
    ).all()
    if open_claims:
        return {
            "status": "HOLD",
            "deleted": False,
            "hold": {
                "reason": "purchase_referenced_by_open_claim",
                "claim_ids": [c.id for c in open_claims],
                "condition": "Deletion will proceed once the referenced claim(s) reach RESOLVED or CLOSED.",
            },
        }

    purchase.deleted_at = utcnow()
    db.commit()
    audit_helper.audit(
        db,
        action=AuditAction.PURCHASE_DELETED,
        resource_type="purchase",
        resource_id=purchase.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=request.client.host if request and request.client else None,
    )
    return {"status": "DELETED", "deleted": True, "hold": None}