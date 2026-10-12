"""Warranty coverage timeline / status (TSD §15).

Phase 4: real rules-engine computation over the purchase's warranty
timelines. The aggregate status for a purchase is the most conservative
(earliest-expiring) status across its mapped components:
EXPIRED > EXPIRING > ACTIVE > NOT_STARTED > UNKNOWN.

A purchase with an unresolved warranty region, or with no mapped coverage
at all, reports UNKNOWN — never a silent default (SRS FR-11a).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.purchase import Purchase
from app.models.user import User
from app.models.warranty import WarrantyTimeline

# TSD §15 status vocabulary.
STATUSES = ("NOT_STARTED", "ACTIVE", "EXPIRING", "EXPIRED", "UNKNOWN")

# Most conservative first: the purchase-level status reflects the component
# closest to (or past) expiry.
_CONSERVATIVE = ("EXPIRED", "EXPIRING", "ACTIVE", "NOT_STARTED", "UNKNOWN")


def _purchase_timeline_rows(db: Session, purchase: Purchase) -> list[WarrantyTimeline]:
    item_ids = [i.id for i in purchase.items]
    if not item_ids:
        return []
    return list(
        db.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id.in_(item_ids))).all()
    )


def coverage_status_for_purchase(db: Session, purchase: Purchase) -> str:
    """Return the aggregate warranty status for a purchase (TSD §15).

    - region unresolved            -> UNKNOWN (FR-11a: never default)
    - no mapped coverage           -> UNKNOWN
    - otherwise                    -> most conservative component status
    """
    if purchase.warranty_region is None:
        return "UNKNOWN"
    timelines = _purchase_timeline_rows(db, purchase)
    if not timelines:
        return "UNKNOWN"
    statuses = {t.status.value for t in timelines}
    for s in _CONSERVATIVE:
        if s in statuses:
            return s
    return "UNKNOWN"


def coverage_timeline_for_purchase(db: Session, purchase: Purchase) -> list[dict]:
    """Per-component coverage timeline for the purchase (TSD §15)."""
    return [
        {
            "component": t.component,
            "start_date": t.start_date.isoformat() if t.start_date else None,
            "expiry_date": t.expiry_date.isoformat() if t.expiry_date else None,
            "status": t.status.value,
        }
        for t in _purchase_timeline_rows(db, purchase)
    ]


def warranty_bucket_for_purchase(db: Session, purchase: Purchase) -> str:
    """Dashboard bucket for one purchase (SRS FR-13).

    - region unresolved           -> unknown (FR-11a: cannot be determined)
    - region resolved, unmapped   -> incomplete (needs mapping/attention)
    - mapped                      -> most conservative component status
    """
    if purchase.warranty_region is None:
        return "unknown"
    timelines = _purchase_timeline_rows(db, purchase)
    if not timelines:
        return "incomplete"
    statuses = {t.status.value for t in timelines}
    for s in _CONSERVATIVE[:-1]:
        if s in statuses:
            return s.lower()
    return "unknown"


def warranty_buckets(db: Session, user: User) -> dict:
    """Counts for the dashboard warranty buckets (TSD §42, SRS FR-13)."""
    purchases = db.scalars(
        select(Purchase).where(Purchase.user_id == user.id, Purchase.deleted_at.is_(None))
    ).all()
    buckets = {"active": 0, "expiring": 0, "expired": 0, "not_started": 0, "unknown": 0, "incomplete": 0}
    for p in purchases:
        buckets[warranty_bucket_for_purchase(db, p)] += 1
    return buckets
