"""Administrator endpoints (TSD §43).

Every admin route is separately authorized via `require_admin`, which checks
the stored user role (not the token claim) to resist privilege escalation.
Admin actions are audited (TSD §38 ADMIN_ACTION).

Coverage (TSD §43 Admin Technical Contract):
- manufacturer / retailer / product catalog / product alias management
- warranty source management (incl. TSD §44 tracking fields)
- warranty policy management with versioning (history preserved)
- source refresh status (full §44 field set per source)
- failed processing review (+ retry/requeue action)
- AI evaluation results (ai_eval/RESULTS.json)
- model/pipeline registry (TSD §45 — model versions live ONLY in the
  registry / config, never in business logic)
- audit logs, system observability
"""

import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import get_db, require_admin
from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.models.job import BackgroundJob, JobStatus, JobType
from app.models.product import EntityStatus, Manufacturer, Product, ProductAlias, Retailer
from app.models.user import User
from app.models.warranty import (
    DurationUnit,
    PolicyStatus,
    RetrievalStatus,
    SourceType,
    StartCondition,
    WarrantyCoverage,
    WarrantyPolicy,
    WarrantySource,
)
from app.schemas.catalog import (
    ManufacturerCreate,
    ManufacturerUpdate,
    ProductAliasCreate,
    ProductCreate,
    ProductUpdate,
    RetailerCreate,
    RetailerUpdate,
)
from app.schemas.warranty import (
    WarrantyPolicyCreate,
    WarrantyPolicyUpdate,
    WarrantySourceCreate,
    WarrantySourceUpdate,
)
from app.warranty.sources import source_out
from app.warranty.versioning import latest_version_row, upsert_policy_versioned

router = APIRouter()


def _audit(db: Session, admin: User, action: str, resource_type: str, resource_id: int | str, metadata: dict | None = None) -> None:
    audit_helper.audit(
        db,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=admin.id,
        metadata=metadata or {"op": "admin"},
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "x"


def _enum(value: str, enum_cls, label: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid {label}: {value!r}") from exc


# --- Audit logs (Phase 0) ----------------------------------------------------


@router.get("/audit-logs")
def list_audit_logs(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    actor_id: int | None = None,
    resource_type: str | None = None,
):
    total_stmt = select(func.count(AuditLog.id))
    query = select(AuditLog)
    if action:
        total_stmt = total_stmt.where(AuditLog.action == action)
        query = query.where(AuditLog.action == action)
    if actor_id is not None:
        total_stmt = total_stmt.where(AuditLog.actor_id == actor_id)
        query = query.where(AuditLog.actor_id == actor_id)
    if resource_type:
        total_stmt = total_stmt.where(AuditLog.resource_type == resource_type)
        query = query.where(AuditLog.resource_type == resource_type)
    total = db.scalar(total_stmt) or 0
    rows = db.scalars(
        query.order_by(desc(AuditLog.id)).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return envelope.ok(
        {
            "items": [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "actor_id": r.actor_id,
                    "actor_type": r.actor_type,
                    "action": r.action,
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "request_id": r.request_id,
                    "metadata": r.metadata_,
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        request,
    )


# --- Manufacturer management ---------------------------------------------------


def _manufacturer_out(m: Manufacturer) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "name_key": m.name_key,
        "website": m.website,
        "country": m.country,
        "status": m.status.value,
    }


@router.get("/manufacturers")
def list_manufacturers(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    q: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
):
    stmt = select(Manufacturer)
    if not include_inactive:
        stmt = stmt.where(Manufacturer.status == EntityStatus.ACTIVE)
    if q:
        stmt = stmt.where(Manufacturer.name.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(Manufacturer.name)).all()
    return envelope.ok({"items": [_manufacturer_out(m) for m in rows], "total": len(rows)}, request)


@router.post("/manufacturers", status_code=201)
def create_manufacturer(
    body: ManufacturerCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    key = _slug(body.name)
    if db.scalar(select(Manufacturer).where(Manufacturer.name_key == key)) is not None:
        raise ConflictError(f"Manufacturer with name key '{key}' already exists")
    m = Manufacturer(name=body.name, name_key=key, website=body.website, country=body.country)
    db.add(m)
    db.commit()
    db.refresh(m)
    _audit(db, admin, AuditAction.MANUFACTURER_UPDATED, "manufacturer", m.id, {"op": "create", "name": m.name})
    return envelope.ok({"manufacturer": _manufacturer_out(m)}, request)


@router.patch("/manufacturers/{manufacturer_id}")
def update_manufacturer(
    manufacturer_id: int,
    body: ManufacturerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    m = db.get(Manufacturer, manufacturer_id)
    if m is None:
        raise NotFoundError("Manufacturer not found")
    changes: dict = {}
    if body.name is not None and body.name != m.name:
        key = _slug(body.name)
        clash = db.scalar(select(Manufacturer).where(Manufacturer.name_key == key, Manufacturer.id != m.id))
        if clash is not None:
            raise ConflictError(f"Manufacturer with name key '{key}' already exists")
        m.name = body.name
        m.name_key = key
        changes["name"] = body.name
    for field in ("website", "country"):
        value = getattr(body, field)
        if value is not None and value != getattr(m, field):
            setattr(m, field, value)
            changes[field] = value
    if body.status is not None and body.status != m.status.value:
        m.status = _enum(body.status, EntityStatus, "status")
        changes["status"] = body.status
    db.commit()
    db.refresh(m)
    _audit(db, admin, AuditAction.MANUFACTURER_UPDATED, "manufacturer", m.id, changes or {"op": "no-op"})
    return envelope.ok({"manufacturer": _manufacturer_out(m)}, request)


# --- Retailer management ---------------------------------------------------------


def _retailer_out(r: Retailer) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "name_key": r.name_key,
        "website": r.website,
        "country": r.country,
        "status": r.status.value,
    }


@router.get("/retailers")
def list_retailers(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    q: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
):
    stmt = select(Retailer)
    if not include_inactive:
        stmt = stmt.where(Retailer.status == EntityStatus.ACTIVE)
    if q:
        stmt = stmt.where(Retailer.name.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(Retailer.name)).all()
    return envelope.ok({"items": [_retailer_out(r) for r in rows], "total": len(rows)}, request)


@router.post("/retailers", status_code=201)
def create_retailer(
    body: RetailerCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    key = _slug(body.name)
    if db.scalar(select(Retailer).where(Retailer.name_key == key)) is not None:
        raise ConflictError(f"Retailer with name key '{key}' already exists")
    r = Retailer(name=body.name, name_key=key, website=body.website, country=body.country)
    db.add(r)
    db.commit()
    db.refresh(r)
    _audit(db, admin, AuditAction.RETAILER_UPDATED, "retailer", r.id, {"op": "create", "name": r.name})
    return envelope.ok({"retailer": _retailer_out(r)}, request)


@router.patch("/retailers/{retailer_id}")
def update_retailer(
    retailer_id: int,
    body: RetailerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    r = db.get(Retailer, retailer_id)
    if r is None:
        raise NotFoundError("Retailer not found")
    changes: dict = {}
    if body.name is not None and body.name != r.name:
        key = _slug(body.name)
        clash = db.scalar(select(Retailer).where(Retailer.name_key == key, Retailer.id != r.id))
        if clash is not None:
            raise ConflictError(f"Retailer with name key '{key}' already exists")
        r.name = body.name
        r.name_key = key
        changes["name"] = body.name
    for field in ("website", "country"):
        value = getattr(body, field)
        if value is not None and value != getattr(r, field):
            setattr(r, field, value)
            changes[field] = value
    if body.status is not None and body.status != r.status.value:
        r.status = _enum(body.status, EntityStatus, "status")
        changes["status"] = body.status
    db.commit()
    db.refresh(r)
    _audit(db, admin, AuditAction.RETAILER_UPDATED, "retailer", r.id, changes or {"op": "no-op"})
    return envelope.ok({"retailer": _retailer_out(r)}, request)


# --- Product catalog management ----------------------------------------------------


def _product_out(p: Product, db: Session) -> dict:
    mfr = db.get(Manufacturer, p.manufacturer_id) if p.manufacturer_id else None
    return {
        "id": p.id,
        "manufacturer_id": p.manufacturer_id,
        "manufacturer": mfr.name if mfr else None,
        "brand": p.brand,
        "canonical_name": p.canonical_name,
        "category": p.category,
        "model_number": p.model_number,
        "sku": p.sku,
        "serial_number_pattern": p.serial_number_pattern,
        "status": p.status.value,
        "aliases": [
            {
                "id": a.id,
                "alias_text": a.alias_text,
                "alias_type": a.alias_type.value,
                "retailer_id": a.retailer_id,
                "source": a.source,
            }
            for a in sorted(p.aliases, key=lambda a: a.id)
        ],
    }


@router.get("/products")
def list_products(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    q: str | None = Query(default=None),
    manufacturer_id: int | None = None,
    include_inactive: bool = Query(default=False),
):
    stmt = select(Product)
    if not include_inactive:
        stmt = stmt.where(Product.status == EntityStatus.ACTIVE)
    if q:
        stmt = stmt.where(Product.canonical_name.ilike(f"%{q}%"))
    if manufacturer_id is not None:
        stmt = stmt.where(Product.manufacturer_id == manufacturer_id)
    rows = db.scalars(stmt.order_by(Product.canonical_name)).all()
    items = [_product_out(p, db) for p in rows]
    return envelope.ok({"items": items, "total": len(items)}, request)


@router.post("/products", status_code=201)
def create_product(
    body: ProductCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if body.manufacturer_id is not None and db.get(Manufacturer, body.manufacturer_id) is None:
        raise NotFoundError("Manufacturer not found")
    p = Product(
        manufacturer_id=body.manufacturer_id,
        brand=body.brand,
        canonical_name=body.canonical_name,
        category=body.category,
        model_number=body.model_number,
        sku=body.sku,
        serial_number_pattern=body.serial_number_pattern,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    _audit(db, admin, AuditAction.PRODUCT_UPDATED, "product", p.id, {"op": "create", "name": p.canonical_name})
    return envelope.ok({"product": _product_out(p, db)}, request)


@router.patch("/products/{product_id}")
def update_product(
    product_id: int,
    body: ProductUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    p = db.get(Product, product_id)
    if p is None:
        raise NotFoundError("Product not found")
    changes: dict = {}
    if body.manufacturer_id is not None:
        if db.get(Manufacturer, body.manufacturer_id) is None:
            raise NotFoundError("Manufacturer not found")
        p.manufacturer_id = body.manufacturer_id
        changes["manufacturer_id"] = body.manufacturer_id
    for field in ("brand", "canonical_name", "category", "model_number", "sku", "serial_number_pattern"):
        value = getattr(body, field)
        if value is not None and value != getattr(p, field):
            setattr(p, field, value)
            changes[field] = value
    if body.status is not None and body.status != p.status.value:
        p.status = _enum(body.status, EntityStatus, "status")
        changes["status"] = body.status
    db.commit()
    db.refresh(p)
    _audit(db, admin, AuditAction.PRODUCT_UPDATED, "product", p.id, changes or {"op": "no-op"})
    return envelope.ok({"product": _product_out(p, db)}, request)


# --- Product alias management ------------------------------------------------------


@router.post("/products/{product_id}/aliases", status_code=201)
def add_product_alias(
    product_id: int,
    body: ProductAliasCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    p = db.get(Product, product_id)
    if p is None:
        raise NotFoundError("Product not found")
    if body.retailer_id is not None and db.get(Retailer, body.retailer_id) is None:
        raise NotFoundError("Retailer not found")
    existing = db.scalar(
        select(ProductAlias).where(ProductAlias.product_id == p.id, ProductAlias.alias_text == body.alias_text)
    )
    if existing is not None:
        raise ConflictError("Alias already exists for this product")
    a = ProductAlias(product_id=p.id, alias_text=body.alias_text, retailer_id=body.retailer_id, source="ADMIN")
    db.add(a)
    db.commit()
    db.refresh(a)
    _audit(db, admin, AuditAction.PRODUCT_ALIAS_UPDATED, "product_alias", a.id, {"op": "create", "alias": a.alias_text})
    return envelope.ok(
        {"alias": {"id": a.id, "alias_text": a.alias_text, "retailer_id": a.retailer_id, "source": a.source}},
        request,
    )


@router.delete("/products/{product_id}/aliases/{alias_id}")
def delete_product_alias(
    product_id: int,
    alias_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    a = db.get(ProductAlias, alias_id)
    if a is None or a.product_id != product_id:
        raise NotFoundError("Alias not found for this product")
    text = a.alias_text
    db.delete(a)
    db.commit()
    _audit(db, admin, AuditAction.PRODUCT_ALIAS_UPDATED, "product_alias", a.id, {"op": "delete", "alias": text})
    return envelope.ok({"deleted": True}, request)


# --- Warranty source management (TSD §43/§44) ----------------------------------------


@router.get("/warranty/sources")
def list_warranty_sources(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Source refresh status: the full TSD §44 tracking set per source."""
    rows = db.scalars(select(WarrantySource).order_by(WarrantySource.id)).all()
    return envelope.ok({"items": [source_out(db, s) for s in rows], "total": len(rows)}, request)


@router.get("/warranty/sources/{source_id}")
def get_warranty_source(
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    s = db.get(WarrantySource, source_id)
    if s is None:
        raise NotFoundError("Warranty source not found")
    return envelope.ok({"source": source_out(db, s)}, request)


@router.post("/warranty/sources", status_code=201)
def create_warranty_source(
    body: WarrantySourceCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if body.manufacturer_id is not None and db.get(Manufacturer, body.manufacturer_id) is None:
        raise NotFoundError("Manufacturer not found")
    source = WarrantySource(
        manufacturer_id=body.manufacturer_id,
        source_type=_enum(body.source_type, SourceType, "source_type"),
        source_url=body.source_url,
        source_title=body.source_title,
        source_version=body.source_version,
        region=body.region,
        trust_level=body.trust_level,
        owner=body.owner,
        retrieval_method=body.retrieval_method,
        retrieved_at=datetime.now(UTC),
        last_success_at=datetime.now(UTC),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    _audit(db, admin, AuditAction.WARRANTY_SOURCE_UPDATED, "warranty_source", source.id, {"op": "create"})
    return envelope.ok({"source": source_out(db, source)}, request)


@router.patch("/warranty/sources/{source_id}")
def update_warranty_source(
    source_id: int,
    body: WarrantySourceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    s = db.get(WarrantySource, source_id)
    if s is None:
        raise NotFoundError("Warranty source not found")
    changes: dict = {}
    if body.source_type is not None:
        s.source_type = _enum(body.source_type, SourceType, "source_type")
        changes["source_type"] = body.source_type
    for field in ("source_url", "source_title", "source_version", "region", "owner", "retrieval_method"):
        value = getattr(body, field)
        if value is not None and value != getattr(s, field):
            setattr(s, field, value)
            changes[field] = value
    if body.trust_level is not None and body.trust_level != s.trust_level:
        s.trust_level = body.trust_level
        changes["trust_level"] = body.trust_level
    if body.manufacturer_id is not None:
        if db.get(Manufacturer, body.manufacturer_id) is None:
            raise NotFoundError("Manufacturer not found")
        s.manufacturer_id = body.manufacturer_id
        changes["manufacturer_id"] = body.manufacturer_id
    if body.effective_date is not None:
        s.effective_date = date.fromisoformat(body.effective_date)
        changes["effective_date"] = body.effective_date
    if body.retrieval_status is not None:
        s.retrieval_status = _enum(body.retrieval_status, RetrievalStatus, "retrieval_status")
        changes["retrieval_status"] = body.retrieval_status
    db.commit()
    db.refresh(s)
    _audit(db, admin, AuditAction.WARRANTY_SOURCE_UPDATED, "warranty_source", s.id, changes or {"op": "no-op"})
    return envelope.ok({"source": source_out(db, s)}, request)


@router.delete("/warranty/sources/{source_id}")
def delete_warranty_source(
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    s = db.get(WarrantySource, source_id)
    if s is None:
        raise NotFoundError("Warranty source not found")
    n_policies = db.scalar(
        select(func.count(WarrantyPolicy.id)).where(WarrantyPolicy.source_id == s.id)
    ) or 0
    if n_policies:
        raise ConflictError(
            f"Source is referenced by {n_policies} policies; re-point or delete the policies first"
        )
    db.delete(s)
    db.commit()
    _audit(db, admin, AuditAction.WARRANTY_SOURCE_UPDATED, "warranty_source", s.id, {"op": "delete"})
    return envelope.ok({"deleted": True}, request)


# --- Warranty policy management (versioning, TSD §44) ---------------------------------


def _policy_admin_out(db: Session, p: WarrantyPolicy) -> dict:
    d: dict = {
        "id": p.id,
        "manufacturer_id": p.manufacturer_id,
        "product_category": p.product_category,
        "product_model_pattern": p.product_model_pattern,
        "region": p.region,
        "policy_name": p.policy_name,
        "policy_version": p.policy_version,
        "effective_from": p.effective_from.isoformat(),
        "effective_to": p.effective_to.isoformat() if p.effective_to else None,
        "source_id": p.source_id,
        "status": p.status.value,
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
            for c in db.scalars(select(WarrantyCoverage).where(WarrantyCoverage.policy_id == p.id)).all()
        ],
    }
    return d


@router.get("/warranty/policies")
def list_warranty_policies_admin(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    include_archived: bool = Query(default=False),
):
    stmt = select(WarrantyPolicy)
    if not include_archived:
        stmt = stmt.where(WarrantyPolicy.status != PolicyStatus.ARCHIVED)
    rows = db.scalars(stmt.order_by(WarrantyPolicy.id)).all()
    items = [_policy_admin_out(db, p) for p in rows]
    return envelope.ok({"items": items, "total": len(items)}, request)


@router.post("/warranty/policies", status_code=201)
def create_warranty_policy(
    body: WarrantyPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    source_id = None
    if body.source is not None:
        s = WarrantySource(
            manufacturer_id=body.source.manufacturer_id,
            source_type=_enum(body.source.source_type, SourceType, "source_type"),
            source_url=body.source.source_url,
            source_title=body.source.source_title,
            source_version=body.source.source_version,
            region=body.source.region,
            trust_level=body.source.trust_level,
            owner=body.source.owner,
            retrieval_method=body.source.retrieval_method,
            retrieved_at=datetime.now(UTC),
            last_success_at=datetime.now(UTC),
        )
        db.add(s)
        db.flush()
        source_id = s.id
    policy = WarrantyPolicy(
        manufacturer_id=body.manufacturer_id,
        product_category=body.product_category,
        product_model_pattern=body.product_model_pattern,
        region=body.region,
        policy_name=body.policy_name,
        policy_version=body.policy_version,
        effective_from=datetime.fromisoformat(body.effective_from).date(),
        effective_to=datetime.fromisoformat(body.effective_to).date() if body.effective_to else None,
        source_id=source_id,
        status=PolicyStatus.ACTIVE,
    )
    db.add(policy)
    db.flush()
    for c in body.coverages:
        db.add(
            WarrantyCoverage(
                policy_id=policy.id,
                component=c.component,
                duration_value=c.duration_value,
                duration_unit=DurationUnit(c.duration_unit),
                start_condition=StartCondition(c.start_condition),
                registration_required=c.registration_required,
                coverage_description=c.coverage_description,
                exclusions=c.exclusions,
            )
        )
    db.commit()
    db.refresh(policy)
    _audit(
        db,
        admin,
        AuditAction.WARRANTY_SOURCE_UPDATED,
        "warranty_policy",
        policy.id,
        {"op": "create", "policy_version": policy.policy_version, "source_id": source_id},
    )
    return envelope.ok({"policy_id": policy.id, "source_id": source_id}, request)


def _next_version(current: str) -> str:
    """1.0 -> 1.1 · 1 -> 1.1 · 2 -> 3 · unparsable -> append .1"""
    m = re.match(r"^(\d+)(?:\.(\d+))?$", current.strip())
    if m:
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) is not None else None
        if minor is None:
            return f"{major}.1"
        return f"{major}.{minor + 1}"
    return f"{current.strip()}.1"


@router.patch("/warranty/policies/{policy_id}")
def update_warranty_policy(
    policy_id: int,
    body: WarrantyPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Policy management with versioning.

    Status-only updates transition the row. Any content update goes through
    `upsert_policy_versioned`: a NEW version row is created (auto-incremented
    when `policy_version` is omitted) and the previous version is archived —
    historical versions are never overwritten (TSD §44).
    """
    policy = db.get(WarrantyPolicy, policy_id)
    if policy is None:
        raise NotFoundError("Warranty policy not found")

    content_fields = (
        body.policy_name,
        body.region,
        body.product_category,
        body.product_model_pattern,
        body.effective_from,
        body.effective_to,
        body.coverages,
    )
    has_content = any(v is not None for v in content_fields)

    if body.status is not None and not has_content:
        new_status = _enum(body.status, PolicyStatus, "policy status")
        policy.status = new_status
        db.commit()
        db.refresh(policy)
        _audit(db, admin, AuditAction.WARRANTY_SOURCE_UPDATED, "warranty_policy", policy.id, {"op": "status", "status": new_status.value})
        return envelope.ok({"policy": _policy_admin_out(db, policy)}, request)

    if not has_content:
        raise ValidationError("Nothing to update: supply content fields and/or a status")

    # --- content update -> new version (history preserved) ---
    region = body.region if body.region is not None else policy.region
    latest = latest_version_row(
        db, manufacturer_id=policy.manufacturer_id, policy_name=policy.policy_name, region=region
    )
    version = body.policy_version or (
        _next_version(latest.policy_version) if latest is not None else "1.0"
    )
    # Base coverage set: the same-version row if it exists, else the target
    # policy's coverages (kept when the body does not supply new ones).
    base = db.scalar(
        select(WarrantyPolicy).where(
            WarrantyPolicy.manufacturer_id == policy.manufacturer_id,
            WarrantyPolicy.policy_name == policy.policy_name,
            WarrantyPolicy.policy_version == version,
        )
    )
    base_row = base or policy
    if body.coverages is not None:
        coverages = [c.model_dump() for c in body.coverages]
    else:
        coverages = [
            {
                "component": c.component,
                "duration_value": c.duration_value,
                "duration_unit": c.duration_unit.value,
                "start_condition": c.start_condition.value,
                "registration_required": c.registration_required,
                "coverage_description": c.coverage_description,
                "exclusions": c.exclusions,
            }
            for c in db.scalars(select(WarrantyCoverage).where(WarrantyCoverage.policy_id == base_row.id)).all()
        ]
    try:
        out = upsert_policy_versioned(
            db,
            manufacturer_id=policy.manufacturer_id,
            policy_name=body.policy_name or policy.policy_name,
            region=body.region if body.region is not None else policy.region,
            policy_version=version,
            effective_from=date.fromisoformat(body.effective_from) if body.effective_from else policy.effective_from,
            effective_to=date.fromisoformat(body.effective_to) if body.effective_to else policy.effective_to,
            product_category=body.product_category if body.product_category is not None else policy.product_category,
            product_model_pattern=(
                body.product_model_pattern if body.product_model_pattern is not None else policy.product_model_pattern
            ),
            source_id=policy.source_id,
            coverages=coverages,
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    db.commit()
    new_policy = out["policy"]
    db.refresh(new_policy)
    _audit(
        db,
        admin,
        AuditAction.WARRANTY_SOURCE_UPDATED,
        "warranty_policy",
        new_policy.id,
        {"op": "version", "policy_version": new_policy.policy_version, "superseded": out["superseded"], "created": out["created"]},
    )
    return envelope.ok(
        {
            "policy": _policy_admin_out(db, new_policy),
            "created": out["created"],
            "superseded": out["superseded"],
        },
        request,
    )


# --- Failed processing review (TSD §43) ----------------------------------------------


def _document_failed_out(d: Document) -> dict:
    return {
        "id": d.id,
        "uuid": d.uuid,
        "user_id": d.user_id,
        "original_filename": d.original_filename,
        "document_type": d.document_type.value,
        "status": d.status.value,
        "error_code": d.error_code,
        "error_message": d.error_message,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _job_failed_out(j: BackgroundJob) -> dict:
    return {
        "id": j.id,
        "type": j.type.value,
        "status": j.status.value,
        "resource_type": j.resource_type,
        "resource_id": j.resource_id,
        "error_code": j.error_code,
        "error": j.error,
        "attempt_count": j.attempt_count,
        "max_attempts": j.max_attempts,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
    }


@router.get("/failed-processing")
def failed_processing(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Failed documents + failed background jobs for operational review."""
    docs = db.scalars(
        select(Document).where(Document.status == DocumentStatus.FAILED).order_by(desc(Document.id)).limit(limit)
    ).all()
    jobs = db.scalars(
        select(BackgroundJob).where(BackgroundJob.status.in_([JobStatus.FAILED, JobStatus.DEAD])).order_by(desc(BackgroundJob.id)).limit(limit)
    ).all()
    counts = {
        "failed_documents": db.scalar(
            select(func.count(Document.id)).where(Document.status == DocumentStatus.FAILED)
        )
        or 0,
        "review_required_documents": db.scalar(
            select(func.count(Document.id)).where(Document.status == DocumentStatus.REVIEW_REQUIRED)
        )
        or 0,
        "failed_jobs": db.scalar(
            select(func.count(BackgroundJob.id)).where(BackgroundJob.status.in_([JobStatus.FAILED, JobStatus.DEAD]))
        )
        or 0,
    }
    return envelope.ok(
        {
            "documents": [_document_failed_out(d) for d in docs],
            "jobs": [_job_failed_out(j) for j in jobs],
            "counts": counts,
        },
        request,
    )


@router.post("/failed-processing/documents/{document_id}/retry")
def retry_failed_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Re-queue a failed document for processing (new attempt, new job)."""
    from app.workers.jobs import enqueue_job

    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFoundError("Document not found")
    if doc.status != DocumentStatus.FAILED:
        raise ValidationError(f"Document is {doc.status.value}, not FAILED; nothing to retry")
    settings = get_settings()
    doc.status = DocumentStatus.QUEUED
    doc.error_code = None
    doc.error_message = None
    enqueue_job(
        db,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id=str(doc.id),
        payload={"pipeline_version": settings.pipeline_version, "retry": True},
        idempotency_key=f"docproc:{doc.id}:retry:{int(time.time())}",
    )
    db.commit()
    db.refresh(doc)
    _audit(db, admin, AuditAction.DOCUMENT_RETRY_REQUESTED, "document", doc.id, {"op": "retry"})
    return envelope.ok({"document": _document_failed_out(doc)}, request)


# --- AI evaluation results (TSD §41/§43) -----------------------------------------------


@router.get("/ai-evaluation")
def ai_evaluation_results(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Latest committed AI evaluation results (ai_eval/RESULTS.json)."""
    # admin.py = backend/app/api/v1/admin.py -> backend = parents[3]
    out = Path(__file__).resolve().parents[3] / "ai_eval" / "RESULTS.json"
    if not out.exists():
        return envelope.ok(
            {
                "available": False,
                "note": "No evaluation results on this host. Run: python -m ai_eval.run_evaluation",
            },
            request,
        )
    data = json.loads(out.read_text(encoding="utf-8"))
    return envelope.ok(
        {
            "available": True,
            "file": "backend/ai_eval/RESULTS.json",
            "updated_at": datetime.fromtimestamp(out.stat().st_mtime, tz=UTC).isoformat(),
            **data,
        },
        request,
    )


# --- Model/pipeline registry (TSD §45) ---------------------------------------------------


@router.get("/model-registry")
def model_registry(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Every production AI component's identity (TSD §45).

    Model versions are defined ONLY in app.ai.registry (+ config) — business
    logic imports them from there instead of hard-coding versions.
    """
    from app.ai import registry as model_registry_module

    settings = get_settings()
    return envelope.ok(
        {
            "pipeline_version": settings.pipeline_version,
            "components": [info.as_dict() for info in model_registry_module.registry()],
        },
        request,
    )


# --- System observability (Phase 6) -----------------------------------------------------

from sqlalchemy import text  # noqa: E402


def collect_observability(db: Session) -> dict:
    """Gather operational health metrics for the admin monitoring view.

    Covers: database reachability/latency, background-job queue depth,
    per-job-type counts, recent job failures, storage usage, and uptime.
    Read-only; safe to poll.
    """
    t0 = time.monotonic()
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_error = None
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = f"{exc.__class__.__name__}"
    db_latency_ms = round((time.monotonic() - t0) * 1000, 2)

    # Queue depth by status.
    by_status = {row[0]: row[1] for row in db.execute(text(
        "SELECT status::text, COUNT(*) FROM background_jobs GROUP BY status"
    )).all()} if db_ok else {}

    # Non-terminal (in-flight / waiting) jobs — the "queue depth".
    queue_depth = 0
    if db_ok:
        queue_depth = db.execute(text(
            "SELECT COUNT(*) FROM background_jobs WHERE status IN ('PENDING','QUEUED','RUNNING')"
        )).scalar() or 0

    # Per-job-type breakdown.
    by_type = {}
    if db_ok:
        for name, count in db.execute(text(
            "SELECT type::text, COUNT(*) FROM background_jobs GROUP BY type ORDER BY COUNT(*) DESC"
        )).all():
            by_type[name] = count

    # Recent failures (last 10) for the failed-processing review view.
    recent_failures = []
    if db_ok:
        rows = db.execute(text(
            "SELECT id, type::text, status::text, error_code, LEFT(error, 300), created_at "
            "FROM background_jobs WHERE status IN ('FAILED','DEAD') ORDER BY id DESC LIMIT 10"
        )).all()
        recent_failures = [
            {
                "id": r[0],
                "type": r[1],
                "status": r[2],
                "error_code": r[3],
                "error": r[4],
                "created_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]

    # Storage usage (local backend only; S3 not introspected here).
    storage = {"backend": "unknown", "total_bytes": 0, "file_count": 0}
    try:
        settings = get_settings()
        if settings.storage_backend == "local":
            root = Path(settings.storage_local_root)
            if not root.is_absolute():
                root = Path(__file__).resolve().parents[3] / settings.storage_local_root
            if root.is_dir():
                total = 0
                count = 0
                for p in root.rglob("*"):
                    if p.is_file():
                        total += p.stat().st_size
                        count += 1
                storage = {"backend": "local", "total_bytes": total, "file_count": count, "root": str(root)}
            else:
                storage = {"backend": "local", "total_bytes": 0, "file_count": 0, "root": str(root), "missing": True}
        else:
            storage = {"backend": settings.storage_backend, "note": "remote storage not introspected"}
    except Exception:  # noqa: BLE001
        storage = {"backend": "unknown"}

    # Uptime (best-effort; main.BOOT_TIME is set at process start).
    uptime_seconds = None
    try:
        from app.main import BOOT_TIME

        uptime_seconds = max(0, int(time.time() - BOOT_TIME))
    except Exception:  # noqa: BLE001
        uptime_seconds = None

    return {
        "database": {
            "reachable": db_ok,
            "latency_ms": db_latency_ms,
            "error": db_error,
        },
        "jobs": {
            "by_status": {k: v for k, v in by_status.items()},
            "queue_depth": queue_depth,
            "by_type": by_type,
            "recent_failures": recent_failures,
        },
        "storage": storage,
        "uptime_seconds": uptime_seconds,
    }


@router.get("/observability")
def observability(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """System health + queue + storage metrics (admin only)."""
    return envelope.ok(collect_observability(db), request)
