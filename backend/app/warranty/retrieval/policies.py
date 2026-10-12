"""Warranty policy retrieval (SRS FR-09, TSD §13/§14).

Retrieval priority per SDD §5: 1. official manufacturer API,
2. official documentation, 3. approved partner, 4. curated dataset.
Implemented as a source-type priority + trust-level tiebreak so that a
policy sourced from an official page always outranks a curated entry for
the same scope.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.warranty import PolicyStatus, SourceType, WarrantyPolicy

SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.OFFICIAL_API: 0,
    SourceType.OFFICIAL_WEB: 1,
    SourceType.OFFICIAL_DOCUMENT: 2,
    SourceType.APPROVED_PARTNER: 3,
    SourceType.CURATED_DATASET: 4,
    SourceType.OTHER: 5,
}


def active_policies(
    db: Session,
    *,
    manufacturer_id: int | None = None,
    region: str | None = None,
    category: str | None = None,
    purchase_date: date | None = None,
) -> list[WarrantyPolicy]:
    """Active policies within their effective window for the given scope.

    Region handling: a policy with region=None is worldwide and matches
    any (or no) region. An exact-region policy matches only that region.
    """
    stmt = select(WarrantyPolicy).where(WarrantyPolicy.status == PolicyStatus.ACTIVE)
    if manufacturer_id is not None:
        # A policy with no manufacturer is a generic policy and may still
        # apply; manufacturer-specific ones must match exactly.
        stmt = stmt.where(
            (WarrantyPolicy.manufacturer_id == manufacturer_id) | (WarrantyPolicy.manufacturer_id.is_(None))
        )
    if region:
        stmt = stmt.where(
            (WarrantyPolicy.region == region) | (WarrantyPolicy.region.is_(None))
        )
    else:
        # Region unknown: only worldwide policies are applicable.
        stmt = stmt.where(WarrantyPolicy.region.is_(None))
    if purchase_date is not None:
        stmt = stmt.where(WarrantyPolicy.effective_from <= purchase_date)
        stmt = stmt.where((WarrantyPolicy.effective_to.is_(None)) | (WarrantyPolicy.effective_to >= purchase_date))
    return list(db.scalars(stmt).all())
