"""Account data controls (SRS FR-19/FR-20, NFR-02).

GET /account/export — the user's complete personal data as a portable JSON
(payload: profile, documents + extracted fields + classification, purchases +
warranty, claims + evidence, notifications, own audit trail).

GET /account/export/bundle — the same payload as `manifest.json` plus the
actual file bytes (documents + claim evidence) in a ZIP, so warranty records
AND supporting evidence export in one portable, self-describing archive.

Account closure lives in app.api.v1.system (POST /account/close); the
closure schedules the deferred personal-data purge via the retention engine.
"""

import enum
import io
import json
import logging
import re
import zipfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db
from app.api.v1.documents import document_out
from app.api.v1.notifications import notification_out
from app.api.v1.purchases import purchase_out
from app.models.claim import Claim, ClaimFile
from app.models.document import Document
from app.models.extraction import ExtractedField, ExtractionJob
from app.models.notification import Notification
from app.models.purchase import Purchase
from app.models.user import User
from app.services import extraction_service
from app.services import storage as storage_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _latest_job(db: Session, document_id: int) -> ExtractionJob | None:
    return db.scalar(
        select(ExtractionJob).where(ExtractionJob.document_id == document_id).order_by(ExtractionJob.id.desc())
    )


def _export_payload(db: Session, user: User) -> dict:
    documents = []
    docs = db.scalars(
        select(Document).where(Document.user_id == user.id, Document.deleted_at.is_(None)).order_by(Document.id)
    ).all()
    for doc in docs:
        out = document_out(doc)
        fields = db.scalars(
            select(ExtractedField).where(ExtractedField.document_id == doc.id).order_by(ExtractedField.id)
        ).all()
        out["fields"] = [extraction_service.field_out(f) for f in fields]
        job = _latest_job(db, doc.id)
        out["classification"] = job.classification if job else None
        out["pipeline_version"] = job.pipeline_version if job else None
        documents.append(out)

    purchases = []
    for p in db.scalars(
        select(Purchase).where(Purchase.user_id == user.id, Purchase.deleted_at.is_(None)).order_by(Purchase.id)
    ).all():
        out = purchase_out(p, db)
        purchases.append(out)

    claims = []
    for c in db.scalars(select(Claim).where(Claim.user_id == user.id).order_by(Claim.id)).all():
        files = db.scalars(select(ClaimFile).where(ClaimFile.claim_id == c.id).order_by(ClaimFile.id)).all()
        claims.append(
            {
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
                "evidence": [
                    {
                        "id": f.id,
                        "original_filename": f.original_filename,
                        "mime_type": f.mime_type,
                        "file_size": f.file_size,
                        "sha256": f.sha256,
                        "created_at": f.created_at.isoformat() if f.created_at else None,
                    }
                    for f in files
                ],
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
        )

    notifications = [
        notification_out(n)
        for n in db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id)).all()
    ]

    from app.models.audit import AuditLog

    audit_rows = db.scalars(
        select(AuditLog).where(AuditLog.actor_id == user.id).order_by(AuditLog.id.desc()).limit(500)
    ).all()
    audit_trail = [
        {
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": a.resource_id,
            "request_id": a.request_id,
        }
        for a in audit_rows
    ]

    return {
        "exported_at": _utcnow_iso(),
        "user": envelope.user_out(user),
        "documents": documents,
        "purchases": purchases,
        "claims": claims,
        "notifications": notifications,
        "audit_trail": audit_trail,
    }


@router.get("/account/export")
def export_account(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Portable JSON export of everything the user owns (SRS FR-19)."""
    return envelope.ok(_export_payload(db, user), request)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "file"


def _json_default(o: object) -> object:
    """json.dumps fallback: the *_out helpers rely on FastAPI's encoder, which
    knows datetimes/decimals — the portable manifest must not."""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, enum.Enum):
        return o.value
    return str(o)


@router.get("/account/export/bundle")
def export_account_bundle(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Portable ZIP export: manifest.json + document files + claim evidence (SRS FR-19).

    The manifest mirrors the JSON export; every file carries its sha256 in the
    manifest so the archive is self-verifying.
    """
    payload = _export_payload(db, user)
    storage = storage_service.get_storage()

    docs = db.scalars(
        select(Document).where(Document.user_id == user.id, Document.deleted_at.is_(None)).order_by(Document.id)
    ).all()
    claim_files = db.scalars(
        select(ClaimFile).where(ClaimFile.user_id == user.id).order_by(ClaimFile.id)
    ).all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
        for d in docs:
            try:
                data = storage.get(d.storage_key)
                zf.writestr(f"documents/{d.id:04d}-{_safe_name(d.original_filename)}", data)
            except Exception:  # noqa: BLE001 - a missing file must not break the export
                logger.warning("export_missing_document doc_id=%s", d.id)
        for f in claim_files:
            try:
                data = storage.get(f.storage_key)
                zf.writestr(f"claims/{f.claim_id:04d}-{f.id:04d}-{_safe_name(f.original_filename)}", data)
            except Exception:  # noqa: BLE001
                logger.warning("export_missing_evidence file_id=%s", f.id)
        zf.writestr(
            "README.txt",
            (
                "WarrantyVault AI — personal data export\n"
                f"Exported at: {payload['exported_at']}\n"
                "\n"
                "manifest.json holds the complete structured record (documents with\n"
                "extracted fields, purchases with warranty coverage, claims with\n"
                "evidence, notifications, audit trail). File bytes are stored under\n"
                "documents/ and claims/. sha256 values in the manifest let you verify\n"
                "every file.\n"
            ),
        )
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="warrantyvault-export-{user.id}.zip"'},
    )


def _utcnow_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
