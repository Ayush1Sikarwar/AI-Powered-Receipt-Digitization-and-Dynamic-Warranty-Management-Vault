"""Warranty policy versioning (task: historical versions; SRS FR-09/FR-12).

When a manufacturer's terms change:
- a NEW policy version row is created (its own version, effective window);
- the previous version is preserved: its ``effective_to`` is closed at the
  day before the new version starts and it is marked ARCHIVED — never
  deleted;
- historical ``WarrantyMapping`` rows keep pointing at the old version
  (the FK is RESTRICT), so old purchases keep their original coverage
  windows and provenance;
- new purchases resolve against whichever version's effective window
  covers their purchase date (retrieval already filters on it).

Identity of a policy "line" is (manufacturer, policy_name, region);
``policy_version`` distinguishes generations of that line.
"""

import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.warranty import (
    DurationUnit,
    PolicyStatus,
    StartCondition,
    WarrantyCoverage,
    WarrantyPolicy,
)

logger = logging.getLogger(__name__)


def _version_key(version: str) -> tuple:
    """'2.10' -> (2, 10) for ordering; unparseable -> (0, version)."""
    parts: list[int] = []
    for chunk in version.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if digits:
            parts.append(int(digits))
    if not parts:
        return (0, version)
    return tuple(parts)


def latest_version_row(db: Session, *, manufacturer_id: int | None, policy_name: str, region: str | None) -> WarrantyPolicy | None:
    rows = list(
        db.scalars(
            select(WarrantyPolicy).where(
                WarrantyPolicy.manufacturer_id == manufacturer_id,
                WarrantyPolicy.policy_name == policy_name,
            )
        ).all()
    )
    if region is None:
        rows = [r for r in rows if r.region is None]
    else:
        rows = [r for r in rows if r.region == region]
    if not rows:
        return None
    return max(rows, key=lambda r: (_version_key(r.policy_version), r.effective_from, r.id))


def _coverage_signature(c: WarrantyCoverage) -> tuple:
    return (
        c.component,
        c.duration_value,
        c.duration_unit.value,
        c.start_condition.value,
        c.registration_required,
        c.coverage_description or "",
        c.exclusions or "",
    )


def coverage_spec_signature(specs: list[dict]) -> list[tuple]:
    return [
        (
            s["component"],
            s.get("duration_value"),
            s.get("duration_unit", "YEAR").upper(),
            s.get("start_condition", "PURCHASE_DATE").upper(),
            bool(s.get("registration_required", False)),
            s.get("description") or "",
            s.get("exclusions") or "",
        )
        for s in specs
    ]


def _specs_unchanged(db: Session, policy: WarrantyPolicy, specs: list[dict]) -> bool:
    current = sorted(
        _coverage_signature(c)
        for c in db.scalars(select(WarrantyCoverage).where(WarrantyCoverage.policy_id == policy.id)).all()
    )
    return current == sorted(coverage_spec_signature(specs))


def _apply_coverages(db: Session, policy: WarrantyPolicy, specs: list[dict]) -> None:
    for s in specs:
        existing = db.scalar(
            select(WarrantyCoverage).where(
                WarrantyCoverage.policy_id == policy.id,
                WarrantyCoverage.component == s["component"],
            )
        )
        values = {
            "duration_value": s.get("duration_value"),
            "duration_unit": DurationUnit(s.get("duration_unit", "YEAR").upper()),
            "start_condition": StartCondition(s.get("start_condition", "PURCHASE_DATE").upper()),
            "registration_required": bool(s.get("registration_required", False)),
            "coverage_description": s.get("description"),
            "exclusions": s.get("exclusions"),
        }
        if existing is None:
            db.add(WarrantyCoverage(policy_id=policy.id, component=s["component"], **values))
        else:
            for k, v in values.items():
                setattr(existing, k, v)
    # Remove coverages that no longer exist in the spec (version content).
    for c in list(
        db.scalars(select(WarrantyCoverage).where(WarrantyCoverage.policy_id == policy.id)).all()
    ):
        if c.component not in {s["component"] for s in specs}:
            db.delete(c)
    db.flush()


def upsert_policy_versioned(
    db: Session,
    *,
    manufacturer_id: int | None,
    policy_name: str,
    region: str | None,
    policy_version: str,
    effective_from: date,
    effective_to: date | None = None,
    product_category: str | None = None,
    product_model_pattern: str | None = None,
    source_id: int | None = None,
    coverages: list[dict] | None = None,
) -> dict:
    """Upsert one policy version with history preserved.

    Returns {"policy": row, "created": bool, "superseded": [version, ...]}.
    """
    coverages = coverages or []
    existing = db.scalar(
        select(WarrantyPolicy).where(
            WarrantyPolicy.manufacturer_id == manufacturer_id,
            WarrantyPolicy.policy_name == policy_name,
            WarrantyPolicy.policy_version == policy_version,
        )
    )
    if existing is not None and ((existing.region or None) == (region or None)):
        # Same version row already exists: idempotent re-import.
        if _specs_unchanged(db, existing, coverages):
            return {"policy": existing, "created": False, "superseded": []}
        # Same version, different content: a correction of that version —
        # update in place (history of the *generation* is still preserved).
        existing.product_category = product_category
        existing.product_model_pattern = product_model_pattern
        existing.effective_from = effective_from
        existing.effective_to = effective_to
        if source_id is not None:
            existing.source_id = source_id
        _apply_coverages(db, existing, coverages)
        db.flush()
        return {"policy": existing, "created": False, "superseded": []}

    superseded: list[str] = []
    latest = latest_version_row(db, manufacturer_id=manufacturer_id, policy_name=policy_name, region=region)
    if latest is not None and _version_key(latest.policy_version) >= _version_key(policy_version):
        # Importing a version that is not newer than the current latest:
        # treat as a correction of the given version (or a stale re-import).
        if _version_key(latest.policy_version) == _version_key(policy_version):
            raise ValueError(
                f"policy '{policy_name}' already exists at version {policy_version}; "
                "supply a newer policy_version for changed terms"
            )
        logger.warning(
            "upsert_policy_versioned: version %s is older than latest %s for %s; treating as correction",
            policy_version,
            latest.policy_version,
            policy_name,
        )
    if latest is not None and _version_key(policy_version) > _version_key(latest.policy_version):
        # New generation: close the previous version's window and archive
        # it. It is NEVER deleted; historical mappings keep referencing it.
        if latest.effective_to is None and effective_from > latest.effective_from:
            latest.effective_to = effective_from - relativedelta(days=1)
        latest.status = PolicyStatus.ARCHIVED
        superseded.append(latest.policy_version)

    policy = WarrantyPolicy(
        manufacturer_id=manufacturer_id,
        product_category=product_category,
        product_model_pattern=product_model_pattern,
        region=region,
        policy_name=policy_name,
        policy_version=policy_version,
        effective_from=effective_from,
        effective_to=effective_to,
        source_id=source_id,
        status=PolicyStatus.ACTIVE,
    )
    db.add(policy)
    db.flush()
    if coverages:
        _apply_coverages(db, policy, coverages)
    logger.info(
        "policy_version_created name=%s version=%s superseded=%s",
        policy_name,
        policy_version,
        ",".join(superseded) or "-",
    )
    return {"policy": policy, "created": True, "superseded": superseded}
