"""Document service (FR-02, TSD §18): upload, metadata, status, derivatives,
deletion with retention holds (SRS NFR-02, TSD §37).

Business logic lives here, not in the API routes (TSD §4).
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.claim import TERMINAL_STATUSES, Claim
from app.models.document import Document, DocumentStatus
from app.models.job import JobType
from app.models.purchase import Purchase
from app.models.user import User
from app.services import file_validation
from app.services import storage as storage_service
from app.workers.jobs import enqueue_job

logger = logging.getLogger(__name__)
settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


def upload_document(
    db: Session,
    *,
    user: User,
    filename: str,
    data: bytes,
    declared_mime: str | None,
    request: Request | None = None,
    request_id: str | None = None,
) -> Document:
    validated = file_validation.validate_upload(filename, data, declared_mime)

    object_uuid = uuid.uuid4().hex
    key = storage_service.storage_key("original", user.id, object_uuid, validated.extension)
    storage_service.get_storage().put(key, validated.data, validated.mime_type)

    document = Document(
        user_id=user.id,
        document_type=_initial_type(validated.mime_type),
        original_filename=validated.filename,
        mime_type=validated.mime_type,
        file_size=validated.size,
        storage_key=key,
        sha256_hash=validated.sha256,
        page_count=1,
        status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Enqueue async processing (the upload endpoint never does long-running
    # OCR synchronously — TSD §18).
    enqueue_job(
        db,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id=str(document.id),
        payload={"pipeline_version": settings.pipeline_version},
        idempotency_key=f"docproc:{document.id}:v{settings.pipeline_version}",
    )
    document.status = DocumentStatus.QUEUED
    db.commit()

    audit_helper.audit(
        db,
        action=AuditAction.DOCUMENT_UPLOADED,
        resource_type="document",
        resource_id=str(document.id),
        actor_id=user.id,
        request_id=request_id,
        ip_address=_ip(request),
        metadata={
            "filename": validated.filename,
            "mime": validated.mime_type,
            "size": validated.size,
            "sha256_prefix": validated.sha256[:12],
            "scanner": validated.scan.scanner,
        },
    )
    logger.info("document_uploaded doc_id=%s user_id=%s size=%s", document.id, user.id, validated.size)
    return document


def _initial_type(mime: str):
    from app.models.document import DocumentType

    return DocumentType.RECEIPT if mime in {"image/jpeg", "image/png", "image/webp"} else DocumentType.UNKNOWN


def get_document(db: Session, user: User, document_id: int) -> Document:
    document = db.get(Document, document_id)
    _assert_owner(user, document)
    if document is None:
        raise NotFoundError("Document not found")
    if document.deleted_at is not None:
        raise NotFoundError("Document not found")
    return document


def _assert_owner(user: User, document: Document | None) -> None:
    if document is None or document.user_id != user.id:
        # Do not leak existence of other users' resources (TSD §41 IDOR).
        raise NotFoundError("Document not found")


def get_document_or_404(db: Session, user: User, document_id: int) -> Document:
    return get_document(db, user, document_id)


def list_documents(db: Session, user: User, *, page: int, page_size: int, status: str | None, q: str | None) -> tuple[list[Document], int]:
    conds = [Document.user_id == user.id, Document.deleted_at.is_(None)]
    if status:
        conds.append(Document.status == status)
    if q:
        conds.append(Document.original_filename.ilike(f"%{q}%"))
    total = db.scalar(select(func.count(Document.id)).where(*conds)) or 0
    rows = db.scalars(select(Document).where(*conds).order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return list(rows), total


def delete_document(
    db: Session,
    *,
    user: User,
    document_id: int,
    request: Request | None = None,
    request_id: str | None = None,
) -> dict:
    """Delete a document per retention policy.

    - If any NON-TERMINAL claim references the document (directly or via a
      purchase), deletion is a soft hold: the API returns an explicit response
      describing the hold and the condition under which deletion proceeds
      (TSD §37). The original is not silently ignored.
    - Otherwise: soft-delete (status DELETED + deleted_at), storage objects
      queued for purge by the CLEANUP worker, audit recorded.
    """
    document = get_document(db, user, document_id)

    hold = _retention_hold(db, document)
    if hold is not None:
        return {
            "status": "HOLD",
            "deleted": False,
            "hold": hold,
        }

    # Cascade: purchases that point at this document are soft-deleted too.
    purchases = db.scalars(select(Purchase).where(Purchase.document_id == document.id, Purchase.deleted_at.is_(None))).all()
    for p in purchases:
        p.deleted_at = utcnow()

    document.status = DocumentStatus.DELETED
    document.deleted_at = utcnow()
    db.commit()

    # Retention hook: the CLEANUP worker hard-purges rows + storage objects
    # after the grace period (re-checking the open-claim hold at purge time).
    from app.services.data_retention import schedule_document_purge

    schedule_document_purge(db, document_id=document.id)

    audit_helper.audit(
        db,
        action=AuditAction.DOCUMENT_DELETED,
        resource_type="document",
        resource_id=str(document.id),
        actor_id=user.id,
        request_id=request_id,
        ip_address=_ip(request),
        metadata={"purchases_soft_deleted": [p.id for p in purchases]},
    )
    logger.info("document_deleted doc_id=%s user_id=%s", document.id, user.id)
    return {"status": "DELETED", "deleted": True, "hold": None}


def _retention_hold(db: Session, document: Document) -> dict | None:
    """Return hold info if a non-terminal claim references this document."""
    # Direct claims referencing the document's purchases.
    purchase_ids = [
        p.id
        for p in db.scalars(select(Purchase).where(Purchase.document_id == document.id, Purchase.deleted_at.is_(None))).all()
    ]
    if not purchase_ids:
        return None
    claims = db.scalars(
        select(Claim).where(Claim.purchase_id.in_(purchase_ids), Claim.status.notin_(list(TERMINAL_STATUSES)))
    ).all()
    if not claims:
        return None
    return {
        "reason": "document_referenced_by_open_claim",
        "claim_ids": [c.id for c in claims],
        "condition": "Deletion will proceed once the referenced claim(s) reach RESOLVED or CLOSED.",
    }


def document_status_out(document: Document) -> dict:
    return {
        "id": document.id,
        "status": document.status.value,
        "processing": document.status in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING),
        "current_stage": _stage_for(document.status),
        "pipeline_version": settings.pipeline_version,
        "error": None
        if document.error_code is None
        else {"code": document.error_code, "message": document.error_message},
        "updated_at": document.updated_at,
    }


def _stage_for(status: DocumentStatus) -> str | None:
    return {
        DocumentStatus.UPLOADED: "validation",
        DocumentStatus.QUEUED: "queued",
        DocumentStatus.PROCESSING: "processing",
        DocumentStatus.REVIEW_REQUIRED: "human_review",
        DocumentStatus.COMPLETED: "completed",
        DocumentStatus.FAILED: "failed",
        DocumentStatus.DELETED: "deleted",
    }.get(status)
