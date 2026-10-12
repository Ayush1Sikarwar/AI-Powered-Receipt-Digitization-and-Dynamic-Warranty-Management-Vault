"""Warranty intelligence service (SRS FR-10/FR-11, TSD §14/§15).

Orchestrates mapping (rules engine) + timeline computation for a purchase,
keeps user-verified mappings authoritative, and exposes the review actions
(confirm / reject / override) so low-confidence mappings stay reviewable.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.models.product import Manufacturer, Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.user import User
from app.models.warranty import (
    MatchMethod,
    PurchaseExtendedWarranty,
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantySource,
    WarrantyTimeline,
)
from app.warranty.rules.matching import find_warranty_policy
from app.warranty.rules.timeline import compute_extended_timeline, compute_timeline

logger = logging.getLogger(__name__)


def _parse_purchase_date(purchase: Purchase) -> date | None:
    if not purchase.purchase_date:
        return None
    try:
        return date.fromisoformat(purchase.purchase_date)
    except ValueError:
        return None


def _drop_item_mapping(db: Session, item_id: int) -> None:
    # Bulk deletes (issued immediately) so a subsequent re-insert of the same
    # unique keys in the same transaction cannot collide. Extended-warranty
    # timelines are separate records and are NOT dropped here.
    db.query(WarrantyTimeline).filter(
        WarrantyTimeline.purchase_item_id == item_id,
        WarrantyTimeline.coverage_id.is_not(None),
    ).delete(synchronize_session=False)
    db.query(WarrantyMapping).filter(WarrantyMapping.purchase_item_id == item_id).delete(synchronize_session=False)


def _drop_mapping_keep_extended(db: Session, item_id: int, purchase: Purchase, today: date) -> None:
    """Drop the policy mapping + its timelines, but recompute the
    extended-warranty timelines (which do not depend on the mapping)."""
    _drop_item_mapping(db, item_id)
    _recompute_item_timelines(db, item_id, purchase, None, today)


def _sync_item(
    db: Session,
    purchase: Purchase,
    item: PurchaseItem,
    today: date,
) -> dict:
    """Map one purchase item to a warranty policy (+ timelines).

    User-verified mappings (USER_CONFIRMED / USER_CORRECTED) are kept even
    if re-resolution would pick a different policy — the user's decision
    wins (SRS FR-17 spirit). Re-sync only refreshes their timelines.
    """
    product = db.get(Product, item.product_id) if item.product_id else None
    if product is None:
        _drop_mapping_keep_extended(db, item.id, purchase, today)
        return {"item_id": item.id, "mapped": False, "reason": "no catalog product"}

    manufacturer = db.get(Manufacturer, product.manufacturer_id) if product.manufacturer_id else None
    region = purchase.warranty_region  # None => unresolved (FR-11a)
    purchase_date = _parse_purchase_date(purchase)

    existing = db.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == item.id))
    user_verified = existing is not None and existing.verification_status.value in ("USER_CONFIRMED", "USER_CORRECTED")

    if region is None:
        # FR-11a: unresolved region -> no silent default. Worldwide policies
        # are still applicable; a region-specific one would be a guess.
        if user_verified and existing is not None:
            _recompute_timelines(db, existing, purchase, today)
            return {"item_id": item.id, "mapped": True, "policy_id": existing.warranty_policy_id, "kept": True}
        _drop_mapping_keep_extended(db, item.id, purchase, today)
        return {"item_id": item.id, "mapped": False, "reason": "region unresolved"}

    match, notes = find_warranty_policy(
        db,
        product=product,
        manufacturer=manufacturer,
        region=region,
        purchase_date=purchase_date,
        item_model=item.model_number_raw,
        item_sku=item.sku_raw,
    )

    if match is None:
        if user_verified and existing is not None:
            _recompute_timelines(db, existing, purchase, today)
            return {"item_id": item.id, "mapped": True, "policy_id": existing.warranty_policy_id, "kept": True}
        _drop_mapping_keep_extended(db, item.id, purchase, today)
        return {"item_id": item.id, "mapped": False, "reason": "; ".join(notes) or "no policy match"}

    if existing is not None and existing.warranty_policy_id == match.policy.id and not user_verified:
        # Same policy, still AI-mapped: just refresh timelines (idempotent).
        _recompute_timelines(db, existing, purchase, today)
        return {"item_id": item.id, "mapped": True, "policy_id": match.policy.id, "notes": notes}

    if existing is None:
        mapping = WarrantyMapping(purchase_item_id=item.id)
        db.add(mapping)
    else:
        mapping = existing
        # Policy changed under an AI mapping: re-verify as AI-mapped.
    mapping.warranty_policy_id = match.policy.id
    mapping.match_method = match.method
    mapping.match_confidence = match.confidence
    if not user_verified:
        mapping.verification_status = _verification(match.confidence)
    mapping.mapped_at = datetime.now(UTC)
    db.flush()
    _recompute_timelines(db, mapping, purchase, today)
    return {
        "item_id": item.id,
        "mapped": True,
        "policy_id": match.policy.id,
        "method": match.method.value,
        "confidence": match.confidence,
        "notes": notes,
    }


def _verification(confidence: float):
    from app.models.extraction import VerificationStatus

    # All automatic mappings are AI-derived and remain reviewable; the
    # review endpoint is the only path to USER_CONFIRMED/USER_CORRECTED.
    return VerificationStatus.AI_EXTRACTED


def _recompute_timelines(db: Session, mapping: WarrantyMapping, purchase: Purchase, today: date) -> None:
    """Recompute ALL timelines for the mapped item: policy coverages plus
    any extended-warranty records attached to this purchase/item."""
    _recompute_item_timelines(db, mapping.purchase_item_id, purchase, mapping, today)


def _recompute_item_timelines(
    db: Session,
    item_id: int,
    purchase: Purchase,
    mapping: WarrantyMapping | None,
    today: date,
) -> None:
    """Compute coverage + extended-warranty timelines for one item.

    ``mapping`` may be None (e.g. region unresolved) — extended warranties
    still get their own timelines from the anchors the user recorded.
    """
    from app.models.warranty import StartCondition

    purchase_date = _parse_purchase_date(purchase)
    item = db.get(PurchaseItem, item_id)
    anchors = {
        StartCondition.PURCHASE_DATE: purchase_date,
        StartCondition.INSTALLATION_DATE: item.installation_date if item else None,
        StartCondition.REGISTRATION_DATE: item.registration_date if item else None,
    }
    # Bulk delete (issued immediately) so the re-insert of the same
    # (purchase_item, coverage / extended_warranty) pairs does not hit the
    # unique constraints.
    db.query(WarrantyTimeline).filter(WarrantyTimeline.purchase_item_id == item_id).delete(
        synchronize_session=False
    )

    # 1) Policy coverage timelines (only when a mapping exists).
    if mapping is not None:
        coverages = db.scalars(
            select(WarrantyCoverage).where(WarrantyCoverage.policy_id == mapping.warranty_policy_id)
        ).all()
        for c in coverages:
            comp = compute_timeline(c, purchase_date, today, anchors=anchors)
            db.add(
                WarrantyTimeline(
                    purchase_item_id=item_id,
                    coverage_id=c.id,
                    component=c.component,
                    purchase_date=purchase_date,
                    start_date=comp["start_date"],
                    expiry_date=comp["expiry_date"],
                    duration_value=c.duration_value,
                    duration_unit=c.duration_unit,
                    status=comp["status"],
                )
            )

    # 2) Extended-warranty timelines (purchase-level or item-specific).
    exts = _extended_for_item(db, purchase, item_id)
    for ext in exts:
        comp = compute_extended_timeline(
            start_condition=ext.start_condition,
            duration_value=ext.duration_value,
            duration_unit=ext.duration_unit,
            purchase_date=purchase_date,
            today=today,
            anchors=anchors,
        )
        db.add(
            WarrantyTimeline(
                purchase_item_id=item_id,
                extended_warranty_id=ext.id,
                component=ext.component,
                purchase_date=purchase_date,
                start_date=comp["start_date"],
                expiry_date=comp["expiry_date"],
                duration_value=ext.duration_value,
                duration_unit=ext.duration_unit,
                status=comp["status"],
            )
        )
    db.flush()


def _extended_for_item(db: Session, purchase: Purchase, item_id: int) -> list:
    """Extended-warranty records applicable to an item: item-specific ones
    plus purchase-level ones (purchase_item_id IS NULL)."""
    return list(
        db.scalars(
            select(PurchaseExtendedWarranty).where(
                PurchaseExtendedWarranty.purchase_id == purchase.id,
                (PurchaseExtendedWarranty.purchase_item_id.is_(None))
                | (PurchaseExtendedWarranty.purchase_item_id == item_id),
            )
        ).all()
    )


def sync_purchase_warranties(
    db: Session,
    purchase: Purchase,
    *,
    actor: User | None = None,
    request: object | None = None,
    request_id: str | None = None,
    reason: str = "purchase_changed",
) -> dict:
    """Re-run the rules engine over all items of a purchase (idempotent)."""
    today = date.today()
    results = []
    for item in purchase.items:
        results.append(_sync_item(db, purchase, item, today))
    mapped = sum(1 for r in results if r.get("mapped"))
    summary = {"purchase_id": purchase.id, "items": len(results), "mapped": mapped, "results": results}
    if actor is not None:
        audit_helper.audit(
            db,
            action=AuditAction.WARRANTY_MAPPED,
            resource_type="purchase",
            resource_id=purchase.id,
            actor_id=actor.id,
            request_id=request_id,
            metadata={"reason": reason, "mapped": mapped, "items": len(results)},
        )
    logger.info("warranty_sync purchase_id=%s mapped=%s/%s reason=%s", purchase.id, mapped, len(results), reason)
    return summary


def review_mapping(
    db: Session,
    purchase: Purchase,
    *,
    item_id: int,
    action: str,
    policy_id: int | None = None,
    user: User,
    request: object | None = None,
    request_id: str | None = None,
) -> WarrantyMapping | None:
    """User decision on a warranty mapping (TSD §14 reviewable mappings)."""
    today = date.today()
    item = next((i for i in purchase.items if i.id == item_id), None)
    if item is None:
        from app.core.exceptions import ValidationError

        raise ValidationError("Item not found on this purchase")

    mapping = db.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == item_id))

    if action == "reject":
        _drop_mapping_keep_extended(db, item.id, purchase, today)
        db.flush()
        audit_helper.audit(
            db,
            action=AuditAction.WARRANTY_MAPPED,
            resource_type="warranty_mapping",
            resource_id=item_id,
            actor_id=user.id,
            request_id=request_id,
            metadata={"decision": "reject"},
        )
        return None

    if action == "override":
        if policy_id is None:
            from app.core.exceptions import ValidationError

            raise ValidationError("policy_id is required to override a warranty mapping")
        policy = db.get(WarrantyPolicy, policy_id)
        if policy is None:
            from app.core.exceptions import NotFoundError

            raise NotFoundError("Warranty policy not found")
        if mapping is None:
            mapping = WarrantyMapping(purchase_item_id=item.id)
            db.add(mapping)
        mapping.warranty_policy_id = policy.id
        mapping.match_method = MatchMethod.MANUAL
        mapping.match_confidence = 1.0
        mapping.verification_status = _user_verification("USER_CORRECTED")
        mapping.mapped_at = datetime.now(UTC)
        db.flush()
    elif action == "confirm":
        if mapping is None:
            # Nothing mapped yet: resolve fresh so there is something to confirm.
            from app.models.product import Product as _P

            product = db.get(_P, item.product_id) if item.product_id else None
            if product is None:
                from app.core.exceptions import NotFoundError

                raise NotFoundError("No warranty mapping to confirm")
            mfr = db.get(Manufacturer, product.manufacturer_id) if product.manufacturer_id else None
            match, _ = find_warranty_policy(
                db,
                product=product,
                manufacturer=mfr,
                region=purchase.warranty_region,
                purchase_date=_parse_purchase_date(purchase),
                item_model=item.model_number_raw,
                item_sku=item.sku_raw,
            )
            if match is None:
                from app.core.exceptions import NotFoundError

                raise NotFoundError("No warranty mapping to confirm")
            mapping = WarrantyMapping(
                purchase_item_id=item.id,
                warranty_policy_id=match.policy.id,
                match_method=match.method,
                match_confidence=match.confidence,
            )
            db.add(mapping)
        mapping.verification_status = _user_verification("USER_CONFIRMED")
        mapping.mapped_at = datetime.now(UTC)
        db.flush()
    else:
        from app.core.exceptions import ValidationError

        raise ValidationError(f"Unknown action: {action}")

    if mapping is not None:
        _recompute_timelines(db, mapping, purchase, today)
    audit_helper.audit(
        db,
        action=AuditAction.WARRANTY_MAPPED,
        resource_type="warranty_mapping",
        resource_id=item_id,
        actor_id=user.id,
        request_id=request_id,
        metadata={"decision": action, "policy_id": policy_id, "mapping_id": mapping.id if mapping else None},
    )
    return mapping


def _user_verification(value: str):
    from app.models.extraction import VerificationStatus

    return VerificationStatus(value)


def add_extended_warranty(
    db: Session,
    purchase: Purchase,
    *,
    data: dict,
    user: User,
    request_id: str | None = None,
) -> PurchaseExtendedWarranty:
    """Record an extended warranty for a purchase (or one of its items).

    The rules engine recomputes the affected item(s)' timelines so the new
    coverage period shows up immediately. A missing duration/anchor yields
    an UNKNOWN timeline — the record is stored, the date is not guessed.
    """
    from app.core.exceptions import ValidationError
    from app.models.warranty import DurationUnit, ExtendedWarrantySource, StartCondition

    item_id = data.get("purchase_item_id")
    if item_id is not None and not any(i.id == item_id for i in purchase.items):
        raise ValidationError("purchase_item_id does not belong to this purchase")
    try:
        ext = PurchaseExtendedWarranty(
            purchase_id=purchase.id,
            purchase_item_id=item_id,
            component=data["component"],
            duration_value=data.get("duration_value"),
            duration_unit=DurationUnit(data.get("duration_unit") or "YEAR"),
            start_condition=StartCondition(data.get("start_condition") or "PURCHASE_DATE"),
            source_type=ExtendedWarrantySource(data.get("source_type") or "USER_DECLARED"),
            source_detail=data.get("source_detail"),
            notes=data.get("notes"),
        )
    except ValueError as exc:
        raise ValidationError(f"invalid duration/start/source value: {exc}") from exc
    db.add(ext)
    db.flush()

    today = date.today()
    affected = [item_id] if item_id is not None else [i.id for i in purchase.items]
    for i_id in affected:
        mapping = db.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == i_id))
        _recompute_item_timelines(db, i_id, purchase, mapping, today)
    db.commit()
    db.refresh(ext)

    audit_helper.audit(
        db,
        action=AuditAction.WARRANTY_MAPPED,
        resource_type="purchase_extended_warranty",
        resource_id=ext.id,
        actor_id=user.id,
        request_id=request_id,
        metadata={
            "op": "add",
            "purchase_id": purchase.id,
            "component": ext.component,
            "source_type": ext.source_type.value,
        },
    )
    return ext


def remove_extended_warranty(
    db: Session,
    purchase: Purchase,
    extended_warranty_id: int,
    *,
    user: User,
    request_id: str | None = None,
) -> None:
    """Delete an extended-warranty record and recompute the item timelines."""
    from app.core.exceptions import NotFoundError

    ext = db.get(PurchaseExtendedWarranty, extended_warranty_id)
    if ext is None or ext.purchase_id != purchase.id:
        raise NotFoundError("Extended warranty not found")
    item_id = ext.purchase_item_id
    affected = [item_id] if item_id is not None else [i.id for i in purchase.items]
    db.delete(ext)
    db.flush()

    today = date.today()
    for i_id in affected:
        mapping = db.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == i_id))
        _recompute_item_timelines(db, i_id, purchase, mapping, today)
    db.commit()

    audit_helper.audit(
        db,
        action=AuditAction.WARRANTY_MAPPED,
        resource_type="purchase_extended_warranty",
        resource_id=ext.id,
        actor_id=user.id,
        request_id=request_id,
        metadata={"op": "remove", "purchase_id": purchase.id, "component": ext.component},
    )


def policy_out(db: Session, policy: WarrantyPolicy) -> dict:
    """Serializable warranty policy (with coverages + source) for APIs/packages."""
    mfr = db.get(Manufacturer, policy.manufacturer_id) if policy.manufacturer_id else None
    coverages = db.scalars(select(WarrantyCoverage).where(WarrantyCoverage.policy_id == policy.id)).all()
    return {
        "id": policy.id,
        "manufacturer": mfr.name if mfr else None,
        "product_category": policy.product_category,
        "product_model_pattern": policy.product_model_pattern,
        "region": policy.region,
        "policy_name": policy.policy_name,
        "policy_version": policy.policy_version,
        "effective_from": policy.effective_from.isoformat(),
        "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
        "status": policy.status.value,
        "source": source_out(db, policy.source_id),
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
            for c in coverages
        ],
    }


def source_out(db: Session, source_id: int | None) -> dict | None:
    source = db.get(WarrantySource, source_id) if source_id else None
    if source is None:
        return None
    return {
        "id": source.id,
        "source_type": source.source_type.value,
        "source_url": source.source_url,
        "source_title": source.source_title,
        "source_version": source.source_version,
        "trust_level": source.trust_level,
        "retrieved_at": source.retrieved_at.isoformat() if source.retrieved_at else None,
        "retrieval_status": source.retrieval_status.value,
        "effective_date": source.effective_date.isoformat() if source.effective_date else None,
        "region": source.region,
    }
