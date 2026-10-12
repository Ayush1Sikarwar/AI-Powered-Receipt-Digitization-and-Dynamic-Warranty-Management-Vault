"""Extraction field review & correction (SRS FR-07, FR-17, TSD §46).

Rules:
- Corrections never modify the original uploaded document.
- A user correction preserves the AI's original value in `original_value`
  and sets verification_status to USER_CONFIRMED / USER_CORRECTED / REJECTED.
- Every correction is audited (FIELD_CORRECTED).
- After review, the document's status moves to COMPLETED when all critical
  fields are confirmed/corrected/rejected (or stay REVIEW_REQUIRED otherwise).
"""

import logging

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.exceptions import NotFoundError, ValidationError
from app.models.document import Document, DocumentStatus
from app.models.extraction import (
    CRITICAL_FIELDS,
    SUPPORTED_FIELDS,
    ExtractedField,
    VerificationStatus,
)
from app.models.user import User

logger = logging.getLogger(__name__)


def _ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


def list_fields(db: Session, user: User, document_id: int) -> list[dict]:
    document = _get_owned_document(db, user, document_id)
    rows = db.scalars(select(ExtractedField).where(ExtractedField.document_id == document.id).order_by(ExtractedField.id)).all()
    return [field_out(f) for f in rows]


def field_out(f: ExtractedField) -> dict:
    return {
        "id": f.id,
        "field_name": f.field_name,
        "value": f.field_value,
        "normalized_value": f.normalized_value,
        "original_value": f.original_value,
        "confidence": f.confidence,
        "source_page": f.source_page,
        "source_region": f.source_region,
        "extraction_method": f.extraction_method,
        "model_name": f.model_name,
        "model_version": f.model_version,
        "verification_status": f.verification_status.value,
        "is_critical": f.field_name in CRITICAL_FIELDS,
        "requires_review": f.verification_status == VerificationStatus.AI_EXTRACTED
        and (f.confidence is not None and f.confidence < 0.90),
        "created_at": f.created_at,
        "updated_at": f.updated_at,
    }


def correct_field(
    db: Session,
    *,
    user: User,
    document_id: int,
    field_name: str,
    action: str,
    value: str | None,
    request: Request | None = None,
    request_id: str | None = None,
) -> dict:
    if field_name not in SUPPORTED_FIELDS:
        raise ValidationError(f"Unsupported field: {field_name}")
    if action not in {"confirm", "correct", "reject"}:
        raise ValidationError("action must be one of confirm|correct|reject")
    if action == "correct" and not (value is not None and str(value).strip()):
        raise ValidationError("A value is required to correct a field")

    document = _get_owned_document(db, user, document_id)
    row = db.scalar(
        select(ExtractedField).where(
            ExtractedField.document_id == document.id, ExtractedField.field_name == field_name
        )
    )
    if row is None:
        if action == "correct":
            # Creating a new field from scratch (user adds a missing value).
            row = ExtractedField(
                document_id=document.id,
                field_name=field_name,
                extraction_method="user",
                model_name="user",
                model_version="n/a",
            )
            db.add(row)
            db.flush()
        else:
            raise NotFoundError(f"Field {field_name} was not extracted for this document")

    was_ai = row.verification_status == VerificationStatus.AI_EXTRACTED
    if was_ai and row.field_value is not None:
        row.original_value = row.field_value  # preserve the AI result (FR-17)

    if action == "confirm":
        row.verification_status = VerificationStatus.USER_CONFIRMED
    elif action == "correct":
        cleaned = str(value).strip()
        row.field_value = cleaned
        row.normalized_value = _normalize(field_name, cleaned)
        row.confidence = 1.0
        row.extraction_method = "user"
        row.model_name = "user"
        row.model_version = "n/a"
        row.verification_status = VerificationStatus.USER_CORRECTED
    else:  # reject
        row.verification_status = VerificationStatus.REJECTED

    db.commit()
    db.refresh(row)

    _refresh_document_status(db, document)

    audit_helper.audit(
        db,
        action=AuditAction.FIELD_CORRECTED,
        resource_type="document",
        resource_id=document.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=_ip(request),
        metadata={
            "field": field_name,
            "action": action,
            "was_ai_value": row.original_value if was_ai else None,
            "new_value": row.field_value if action == "correct" else None,
        },
    )
    logger.info("field_reviewed doc_id=%s field=%s action=%s", document.id, field_name, action)
    return field_out(row)


def _normalize(field_name: str, value: str) -> str:
    if field_name == "currency":
        return value.upper()
    if field_name in ("quantity",):
        return value.strip()
    if field_name in ("unit_price", "total_price", "tax"):
        return value.strip().replace(",", "")
    return value


def _refresh_document_status(db: Session, document: Document) -> None:
    """REVIEW_REQUIRED until every critical field has a user decision
    (confirmed/corrected/rejected), no page is flagged low-confidence, and
    no product match is awaiting user confirmation (SRS ML-07)."""
    from app.models.document import DocumentPage
    from app.models.product import ProductMatch, ProductMatchStatus

    rows = db.scalars(select(ExtractedField).where(ExtractedField.document_id == document.id)).all()
    pending_critical = [
        f.field_name
        for f in rows
        if f.field_name in CRITICAL_FIELDS and f.verification_status == VerificationStatus.AI_EXTRACTED
    ]
    pages = db.scalars(select(DocumentPage).where(DocumentPage.document_id == document.id)).all()
    low_pages = [p for p in pages if p.ocr_confidence is not None and p.ocr_confidence < 60.0]
    product_match = db.scalar(select(ProductMatch).where(ProductMatch.document_id == document.id))
    product_pending = product_match is not None and product_match.status == ProductMatchStatus.REVIEW_REQUIRED

    if document.status in (DocumentStatus.REVIEW_REQUIRED, DocumentStatus.COMPLETED):
        if pending_critical or low_pages or product_pending:
            if document.status != DocumentStatus.REVIEW_REQUIRED:
                document.status = DocumentStatus.REVIEW_REQUIRED
        else:
            document.status = DocumentStatus.COMPLETED
    db.commit()


def _get_owned_document(db: Session, user: User, document_id: int) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.user_id != user.id or document.deleted_at is not None:
        raise NotFoundError("Document not found")
    return document
