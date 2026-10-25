"""Warranty Intelligence API (SRS FR-09/FR-10/FR-12, TSD §14/§15).

- Browse the warranty policy registry (retrieval service surface).
- Review/override/confirm/reject a purchase item's warranty mapping.
- Manual re-sync of a purchase's warranties (e.g. after catalog changes).
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db, get_request_id, require_admin
from app.models.user import User
from app.models.warranty import WarrantyPolicy, WarrantySource
from app.schemas.warranty import WarrantyReviewRequest
from app.services import purchase_service
from app.warranty.service import policy_out, review_mapping, sync_purchase_warranties
from app.warranty.sources import refresh_source, source_out

router = APIRouter(tags=["warranty"])
logger = logging.getLogger(__name__)


# Reuse the shared serializer (TSD §14): keeps the API and claim packages
# emitting the exact same policy/coverage/source shape.
_policy_out = policy_out


@router.get("/warranty/policies")
def list_warranty_policies(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    manufacturer: str | None = None,
    region: str | None = None,
    category: str | None = None,
):
    """Browse the warranty policy registry (SRS FR-09)."""
    stmt = select(WarrantyPolicy)
    if manufacturer:
        stmt = stmt.where(WarrantyPolicy.manufacturer_id.is_not(None))
    rows = db.scalars(stmt.order_by(WarrantyPolicy.id)).all()
    out = []
    for p in rows:
        if manufacturer and p.manufacturer_id is not None:
            from app.models.product import Manufacturer

            mfr = db.get(Manufacturer, p.manufacturer_id)
            if mfr is None or manufacturer.lower() not in mfr.name.lower():
                continue
        if region and p.region is not None and p.region.lower() != region.lower():
            continue
        if category and (p.product_category or "").lower() != category.lower():
            continue
        out.append(_policy_out(db, p))
    return envelope.ok({"items": out, "total": len(out)}, request)


@router.post("/warranty/policies/{purchase_id}/sync")
def sync_warranty(
    purchase_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Re-run the warranty rules engine for an owned purchase."""
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    summary = sync_purchase_warranties(
        db, purchase, actor=user, request_id=get_request_id(request), reason="manual_sync"
    )
    db.commit()
    return envelope.ok({"summary": summary}, request)


@router.post("/purchases/{purchase_id}/warranty/review")
def review_warranty(
    purchase_id: int,
    body: WarrantyReviewRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Confirm / reject / override a purchase item's warranty mapping."""
    purchase = purchase_service.get_purchase(db, user, purchase_id)
    mapping = review_mapping(
        db,
        purchase,
        item_id=body.item_id,
        action=body.action,
        policy_id=body.policy_id,
        user=user,
        request_id=get_request_id(request),
    )
    db.commit()  # commit the warranty review — this is the transaction boundary

    # SRS FR-15: schedule reminders immediately (best-effort; the worker
    # sweeps too). Runs in a separate try block WITHOUT rollback: a
    # scheduling failure must never roll back the already-committed
    # warranty review.
    try:
        from app.services.notification_service import schedule_warranty_reminders

        schedule_warranty_reminders(db)
    except Exception:
        logger.exception("alert_scheduling_failed purchase_id=%s", purchase_id)
        # No rollback: the review is already committed. Scheduling is
        # best-effort and will be retried by the worker's notification sweep.
    return envelope.ok(
        {
            "item_id": body.item_id,
            "action": body.action,
            "mapped": mapping is not None,
            "mapping": (
                {
                    "id": mapping.id,
                    "policy_id": mapping.warranty_policy_id,
                    "match_method": mapping.match_method.value,
                    "match_confidence": mapping.match_confidence,
                    "verification_status": mapping.verification_status.value,
                }
                if mapping
                else None
            ),
        },
        request,
    )


@router.get("/warranty/sources")
def list_warranty_sources(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Browse the warranty source registry with retrieval status (SRS FR-12)."""
    rows = db.scalars(select(WarrantySource).order_by(WarrantySource.id)).all()
    return envelope.ok({"items": [source_out(db, s) for s in rows], "total": len(rows)}, request)


@router.post("/warranty/sources/{source_id}/refresh")
def refresh_warranty_source(
    request: Request,
    source_id: int,
    body: dict | None = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Re-retrieve a warranty source (admin).

    A failed retrieval records FAILED + error and never touches the
    previous valid policy. A successful retrieval with changed content
    applies policy versioning when a structured `policies` list is
    supplied (new version + archived predecessor, history preserved).
    """
    policies = (body or {}).get("policies")
    if policies is not None and not isinstance(policies, list):
        from app.core.exceptions import ValidationError

        raise ValidationError("'policies' must be a list")
    result = refresh_source(
        db,
        source_id,
        policies=policies,
        actor_id=user.id,
        request_id=get_request_id(request),
    )
    # Include the source's full TSD §44 tracking state after the refresh so
    # the admin can verify content hash / last success / last failure / status.
    source = db.get(WarrantySource, source_id)
    if source is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Warranty source not found")
    return envelope.ok({**result, "source": source_out(db, source)}, request)