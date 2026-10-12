"""Claim endpoints with evidence files (SRS FR-16, TSD §23).

All rows are user-owned; foreign claims are invisible (404). Evidence files
are stored through the same object-storage layer as documents and are
purged with the claim/account by the retention engine.
"""

import json
import uuid

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db
from app.core.audit import AuditAction, audit
from app.core.config import get_settings
from app.core.exceptions import ClaimError, NotFoundError
from app.models.claim import Claim, ClaimDocument, ClaimDocumentRole, ClaimFile, ClaimStatus
from app.models.document import Document
from app.models.user import User
from app.schemas.claim import ClaimCreate, ClaimDocumentCreate, ClaimUpdate
from app.services import file_validation, purchase_service
from app.services import storage as storage_service

router = APIRouter()
settings = get_settings()


def evidence_out(f: ClaimFile) -> dict:
    return {
        "id": f.id,
        "uuid": f.uuid,
        "original_filename": f.original_filename,
        "mime_type": f.mime_type,
        "file_size": f.file_size,
        "sha256": f.sha256,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def claim_out(c: Claim) -> dict:
    out = {
        "id": c.id,
        "uuid": c.uuid,
        "purchase_id": c.purchase_id,
        "status": c.status.value,
        "issue_description": c.issue_description,
        "issue_category": c.issue_category,
        "contact_name": c.contact_name,
        "contact_email": c.contact_email,
        "contact_phone": c.contact_phone,
        "notes": c.notes,
        "package_generated_at": c.package_generated_at.isoformat() if c.package_generated_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    return out


def _get_owned_claim(db: Session, user: User, claim_id: int) -> Claim:
    row = db.scalar(select(Claim).where(Claim.id == claim_id, Claim.user_id == user.id))
    if row is None:
        raise NotFoundError("Claim not found")
    return row


@router.post("", status_code=201)
def create_claim(
    body: ClaimCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    # The purchase must belong to this user (404 otherwise).
    purchase = purchase_service.get_purchase(db, user, body.purchase_id)
    claim = Claim(
        user_id=user.id,
        purchase_id=purchase.id,
        status=ClaimStatus.DRAFT,
        issue_description=body.issue_description,
        issue_category=body.issue_category,
        contact_name=body.contact_name,
        contact_email=body.contact_email,
        contact_phone=body.contact_phone,
        notes=body.notes,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    audit(
        db,
        action=AuditAction.CLAIM_CREATED,
        resource_type="claim",
        resource_id=str(claim.id),
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"purchase_id": purchase.id},
    )
    db.commit()
    return envelope.ok({"claim": claim_out(claim)}, request)


@router.get("")
def list_claims(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(Claim).where(Claim.user_id == user.id).order_by(Claim.id.desc())
    if status:
        try:
            stmt = stmt.where(Claim.status == ClaimStatus(status))
        except ValueError:
            stmt = stmt.where(Claim.id.is_(None))  # no rows for an unknown status
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return envelope.ok({"items": [claim_out(c) for c in rows], "total": total}, request)


@router.get("/{claim_id}")
def get_claim(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    evidence = db.scalars(
        select(ClaimFile).where(ClaimFile.claim_id == claim.id).order_by(ClaimFile.id)
    ).all()
    out = claim_out(claim)
    out["evidence"] = [evidence_out(f) for f in evidence]
    return envelope.ok({"claim": out}, request)


@router.post("/{claim_id}/package")
def generate_package(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) the pre-filled claim package (SRS FR-16)."""
    claim = _get_owned_claim(db, user, claim_id)
    from app.services.claim_service import generate_claim_package

    claim = generate_claim_package(
        db, claim, user=user, request_id=getattr(request.state, "request_id", None)
    )
    return envelope.ok({"claim": claim_out(claim)}, request)


@router.get("/{claim_id}/package")
def get_package(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    if claim.package is None:
        raise ClaimError("Claim package has not been generated yet")
    return envelope.ok(
        {"package": claim.package, "generated_at": claim.package_generated_at},
        request,
    )


@router.get("/{claim_id}/package/download")
def download_package(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Download the generated package as a JSON file."""
    claim = _get_owned_claim(db, user, claim_id)
    if claim.package is None:
        raise ClaimError("Claim package has not been generated yet")
    body = json.dumps(claim.package, indent=2, ensure_ascii=False)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="claim-{claim.id}-package.json"'},
    )


@router.post("/{claim_id}/evidence", status_code=201)
async def upload_evidence(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    file: UploadFile | None = None,
):
    claim = _get_owned_claim(db, user, claim_id)
    if file is None:
        from app.core.exceptions import ValidationError

        raise ValidationError("A file is required")
    data = await file.read()
    validated = file_validation.validate_upload(file.filename or "evidence", data, file.content_type)

    object_uuid = uuid.uuid4().hex
    # Claim evidence lives under the `claims` storage category (TSD §35).
    key = storage_service.storage_key("claims", user.id, object_uuid, validated.extension)
    storage_service.get_storage().put(key, validated.data, validated.mime_type)

    row = ClaimFile(
        claim_id=claim.id,
        user_id=user.id,
        original_filename=validated.filename,
        mime_type=validated.mime_type,
        file_size=validated.size,
        storage_key=key,
        sha256=validated.sha256,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit(
        db,
        action=AuditAction.EVIDENCE_UPLOADED,
        resource_type="claim_file",
        resource_id=str(row.id),
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"claim_id": claim.id, "file_size": row.file_size},
    )
    db.commit()
    return envelope.ok({"evidence": evidence_out(row)}, request)


@router.get("/{claim_id}/evidence")
def list_evidence(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    rows = db.scalars(select(ClaimFile).where(ClaimFile.claim_id == claim.id).order_by(ClaimFile.id)).all()
    return envelope.ok({"items": [evidence_out(f) for f in rows]}, request)


@router.get("/{claim_id}/evidence/{file_id}/download")
def download_evidence(
    claim_id: int,
    file_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    row = db.scalar(
        select(ClaimFile).where(ClaimFile.id == file_id, ClaimFile.claim_id == claim.id)
    )
    if row is None:
        raise NotFoundError("Evidence file not found")
    data = storage_service.get_storage().get(row.storage_key)
    return Response(content=data, media_type=row.mime_type, headers={
        "Content-Disposition": f'attachment; filename="{row.original_filename}"',
        "X-Content-Sha256": row.sha256,
    })


# --- Stored-document evidence (selection from the vault) -------------------


def _owned_document(db: Session, user: User, document_id: int) -> Document:
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if doc is None or doc.deleted_at is not None:
        raise NotFoundError("Document not found")
    return doc


def document_attachment_out(a: ClaimDocument, doc: Document) -> dict:
    return {
        "id": a.id,
        "uuid": a.uuid,
        "document_id": a.document_id,
        "role": a.role.value,
        "original_filename": doc.original_filename,
        "document_type": doc.document_type.value,
        "mime_type": doc.mime_type,
        "sha256": doc.sha256_hash,
        "file_size": doc.file_size,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/{claim_id}/documents")
def list_claim_documents(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    rows = db.scalars(
        select(ClaimDocument).where(ClaimDocument.claim_id == claim.id).order_by(ClaimDocument.id)
    ).all()
    items = []
    for a in rows:
        doc = db.get(Document, a.document_id)
        if doc is not None:
            items.append(document_attachment_out(a, doc))
    return envelope.ok({"items": items}, request)


@router.post("/{claim_id}/documents", status_code=201)
def attach_claim_document(
    body: ClaimDocumentCreate,
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Select an already-stored document as claim evidence (SRS FR-16)."""
    claim = _get_owned_claim(db, user, claim_id)
    doc = _owned_document(db, user, body.document_id)
    existing = db.scalar(
        select(ClaimDocument).where(
            ClaimDocument.claim_id == claim.id, ClaimDocument.document_id == doc.id
        )
    )
    if existing is not None:
        # Re-attach updates the role (idempotent).
        existing.role = ClaimDocumentRole(body.role.value)
        db.commit()
        db.refresh(existing)
        row, created = existing, False
    else:
        row = ClaimDocument(
            claim_id=claim.id,
            document_id=doc.id,
            user_id=user.id,
            role=ClaimDocumentRole(body.role.value),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        created = True
    audit(
        db,
        action=AuditAction.CLAIM_DOCUMENT_ATTACHED,
        resource_type="claim_document",
        resource_id=str(row.id),
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"claim_id": claim.id, "document_id": doc.id, "role": body.role.value},
    )
    db.commit()
    return envelope.ok({"document": document_attachment_out(row, doc), "created": created}, request)


@router.delete("/{claim_id}/documents/{document_id}")
def detach_claim_document(
    request: Request,
    claim_id: int,
    document_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    row = db.scalar(
        select(ClaimDocument).where(
            ClaimDocument.claim_id == claim.id,
            ClaimDocument.document_id == document_id,
            ClaimDocument.user_id == user.id,
        )
    )
    if row is None:
        raise NotFoundError("Document attachment not found")
    db.delete(row)
    db.commit()
    return envelope.ok({"deleted": True}, request)


# --- Claim edits (no status field — MVP boundary, TSD §57) -----------------


@router.patch("/{claim_id}")
def update_claim(
    body: ClaimUpdate,
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(db, user, claim_id)
    changed = {}
    for field in ("issue_description", "issue_category", "contact_name", "contact_email", "contact_phone", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(claim, field, value)
            changed[field] = value
    db.commit()
    db.refresh(claim)
    audit(
        db,
        action=AuditAction.CLAIM_UPDATED,
        resource_type="claim",
        resource_id=str(claim.id),
        actor_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"fields": sorted(changed.keys())},
    )
    db.commit()
    return envelope.ok({"claim": claim_out(claim)}, request)


# --- Claim-ready report + portable evidence bundle (TSD §33) ---------------


def _package_or_409(claim: Claim) -> dict:
    if claim.package is None:
        raise ClaimError("Claim package has not been generated yet")
    return claim.package


@router.get("/{claim_id}/package/report")
def get_package_report(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The claim-ready report as Markdown (TSD §33)."""
    claim = _get_owned_claim(db, user, claim_id)
    package = _package_or_409(claim)
    from app.services.claim_service import render_claim_report

    report = package.get("report") or render_claim_report(package)
    return Response(
        content=report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="claim-{claim.id}-report.md"'},
    )


@router.get("/{claim_id}/package/bundle")
def download_package_bundle(
    request: Request,
    claim_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The portable evidence bundle: package + report + evidence + documents (ZIP)."""
    claim = _get_owned_claim(db, user, claim_id)
    package = _package_or_409(claim)
    from app.services.claim_service import build_evidence_bundle

    data = build_evidence_bundle(db, claim, package)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="claim-{claim.id}-bundle.zip"'},
    )
