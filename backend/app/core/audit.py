"""Audit logging helper (TSD §38).

Call `audit(...)` from service code; it commits independently of the caller's
transaction so an audit record always survives, and it never raises into the
business flow (an audit failure is logged, not fatal).
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


class AuditAction:
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    REGISTER = "REGISTER"
    PASSWORD_RESET = "PASSWORD_RESET"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_DELETED = "DOCUMENT_DELETED"
    AI_EXTRACTION_COMPLETED = "AI_EXTRACTION_COMPLETED"
    FIELD_CORRECTED = "FIELD_CORRECTED"
    PRODUCT_CONFIRMED = "PRODUCT_CONFIRMED"
    PRODUCT_REJECTED = "PRODUCT_REJECTED"
    WARRANTY_MAPPED = "WARRANTY_MAPPED"
    WARRANTY_SOURCE_UPDATED = "WARRANTY_SOURCE_UPDATED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    CLAIM_CREATED = "CLAIM_CREATED"
    CLAIM_UPDATED = "CLAIM_UPDATED"
    CLAIM_PACKAGE_GENERATED = "CLAIM_PACKAGE_GENERATED"
    CLAIM_DOCUMENT_ATTACHED = "CLAIM_DOCUMENT_ATTACHED"
    ADMIN_ACTION = "ADMIN_ACTION"
    PURCHASE_CREATED = "PURCHASE_CREATED"
    PURCHASE_UPDATED = "PURCHASE_UPDATED"
    PURCHASE_DELETED = "PURCHASE_DELETED"
    ACCOUNT_DELETED = "ACCOUNT_DELETED"
    ACCOUNT_PURGED = "ACCOUNT_PURGED"
    DOCUMENT_PURGED = "DOCUMENT_PURGED"
    EVIDENCE_UPLOADED = "EVIDENCE_UPLOADED"
    MANUFACTURER_UPDATED = "MANUFACTURER_UPDATED"
    RETAILER_UPDATED = "RETAILER_UPDATED"
    PRODUCT_UPDATED = "PRODUCT_UPDATED"
    PRODUCT_ALIAS_UPDATED = "PRODUCT_ALIAS_UPDATED"
    DOCUMENT_RETRY_REQUESTED = "DOCUMENT_RETRY_REQUESTED"


def audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: int | str,
    actor_id: int | None = None,
    actor_type: str = "USER",
    request_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    record = AuditLog(
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        request_id=request_id,
        ip_address=ip_address,
        metadata_=metadata or {},
    )
    try:
        db.add(record)
        db.commit()
    except Exception:  # noqa: BLE001 - audit must never break business flow
        db.rollback()
        logger.exception("audit_write_failed action=%s", action)
    return record
