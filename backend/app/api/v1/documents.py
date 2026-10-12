"""Document endpoints (TSD §18).

POST   /documents          (multipart upload; rate limited; async processing)
GET    /documents          (list, paginated + filters)
GET    /documents/{id}
GET    /documents/{id}/status
GET    /documents/{id}/derivatives
DELETE /documents/{id}     (retention-hold aware)
GET    /documents/{id}/download  (authorized original/derivative streaming)
"""

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db, get_request_id, rate_limit
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.document import DocumentDerivative
from app.models.user import User
from app.schemas.extraction import FieldCorrection
from app.schemas.product import ProductMatchDecision
from app.services import document_service, extraction_service
from app.services import storage as storage_service

router = APIRouter()
settings = get_settings()


async def _read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Stream the upload in chunks, aborting as soon as the size cap is
    exceeded — an oversized body is rejected without buffering the whole
    request (memory-DoS guard)."""
    from app.core.exceptions import FileError

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FileError(
                "File exceeds the maximum upload size",
                details={"max_bytes": max_bytes, "actual_bytes": total},
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", status_code=201, dependencies=[Depends(rate_limit("upload", settings.rl_upload_per_hour, 3600))])
async def upload_document(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    data = await _read_upload_capped(file, settings.upload_max_bytes)
    document = document_service.upload_document(
        db,
        user=user,
        filename=file.filename or "document",
        data=data,
        declared_mime=file.content_type,
        request=request,
        request_id=get_request_id(request),
    )
    return envelope.ok({"document": document_out(document)}, request)


def document_out(document) -> dict:
    return {
        "id": document.id,
        "uuid": document.uuid,
        "document_type": document.document_type.value,
        "original_filename": document.original_filename,
        "mime_type": document.mime_type,
        "file_size": document.file_size,
        "page_count": document.page_count,
        "status": document.status.value,
        "sha256": document.sha256_hash,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "error": document.error_message,
    }


@router.get("")
def list_documents(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    q: str | None = None,
):
    rows, total = document_service.list_documents(db, user, page=page, page_size=page_size, status=status, q=q)
    return envelope.ok(
        {"items": [document_out(d) for d in rows], "total": total, "page": page, "page_size": page_size},
        request,
    )


@router.get("/{document_id}")
def get_document(request: Request, document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.models.document import DocumentPage

    document = document_service.get_document_or_404(db, user, document_id)
    pages = db.scalars(select(DocumentPage).where(DocumentPage.document_id == document.id).order_by(DocumentPage.page_number)).all()
    return envelope.ok(
        {
            **document_out(document),
            "pages": [
                {
                    "page_number": p.page_number,
                    "width": p.width,
                    "height": p.height,
                    "quality_score": p.quality_score,
                    "ocr_engine": p.ocr_engine,
                    "ocr_confidence": p.ocr_confidence,
                }
                for p in pages
            ],
        },
        request,
    )


@router.get("/{document_id}/status")
def document_status(request: Request, document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    document = document_service.get_document_or_404(db, user, document_id)
    return envelope.ok(document_service.document_status_out(document), request)


@router.get("/{document_id}/derivatives")
def document_derivatives(request: Request, document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    document = document_service.get_document_or_404(db, user, document_id)
    rows = db.scalars(select(DocumentDerivative).where(DocumentDerivative.document_id == document.id)).all()
    return envelope.ok(
        {
            "items": [
                {
                    "id": r.id,
                    "derivative_type": r.derivative_type.value,
                    "page_number": r.page_number,
                    "processing_version": r.processing_version,
                    "mime_type": r.mime_type,
                    "metadata": r.metadata_,
                }
                for r in rows
            ]
        },
        request,
    )


@router.delete("/{document_id}")
def delete_document(request: Request, document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = document_service.delete_document(
        db, user=user, document_id=document_id, request=request, request_id=get_request_id(request)
    )
    return envelope.ok(result, request)


@router.get("/{document_id}/download")
def download_document(
    request: Request,
    document_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    kind: str = Query("original"),
):
    """Authorized streaming of the original or a derivative. Ownership is
    verified; the storage key is never exposed to the client (TSD §35)."""
    document = document_service.get_document_or_404(db, user, document_id)
    storage = storage_service.get_storage()
    if kind == "original":
        key, mime, filename = document.storage_key, document.mime_type, document.original_filename
    else:
        d = db.scalar(select(DocumentDerivative).where(DocumentDerivative.document_id == document.id, DocumentDerivative.derivative_type == kind.upper()))
        if d is None:
            raise NotFoundError(f"Derivative {kind} not found")
        key, mime, filename = d.storage_key, d.mime_type, f"{document.original_filename}.{d.derivative_type.value.lower()}"
    data = storage.get(key)
    return StreamingResponse(iter([data]), media_type=mime, headers={"Content-Disposition": f'inline; filename="{filename}"'})


@router.get("/{document_id}/fields")
def list_fields(request: Request, document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    fields = extraction_service.list_fields(db, user, document_id)
    return envelope.ok({"items": fields}, request)


@router.patch("/{document_id}/fields")
def correct_field(
    request: Request,
    document_id: int,
    body: FieldCorrection,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    field = extraction_service.correct_field(
        db,
        user=user,
        document_id=document_id,
        field_name=body.field_name,
        action=body.action,
        value=body.value,
        request=request,
        request_id=get_request_id(request),
    )
    document = document_service.get_document_or_404(db, user, document_id)
    return envelope.ok({"field": field, "document_status": document.status.value}, request)


@router.get("/{document_id}/product-match")
def get_product_match(
    request: Request,
    document_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.services import product_service

    match = product_service.get_document_match(db, user, document_id)
    return envelope.ok({"match": product_service.match_out(match, db)}, request)


@router.post("/{document_id}/product-match")
def decide_product_match(
    request: Request,
    document_id: int,
    body: ProductMatchDecision,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.services import product_service

    document = document_service.get_document_or_404(db, user, document_id)
    if body.action == "reject":
        match = product_service.confirm_product_match(
            db,
            user=user,
            document=document,
            product_id=None,
            request=request,
            request_id=get_request_id(request),
        )
    elif body.action == "create_confirm":
        match, product = product_service.create_product_from_receipt(
            db,
            user=user,
            document=document,
            request=request,
            request_id=get_request_id(request),
        )
        return envelope.ok(
            {
                "match": product_service.match_out(match, db),
                "product": product_service.product_out(product, db),
                "document_status": document.status.value,
            },
            request,
        )
    else:  # confirm
        product_id = body.product_id
        if product_id is None:
            # No product_id: confirm the top candidate from the match record.
            existing = product_service.get_document_match(db, user, document_id)
            if not existing.candidates:
                raise NotFoundError("No product candidates to confirm")
            product_id = existing.candidates[0]["product_id"]
        match = product_service.confirm_product_match(
            db,
            user=user,
            document=document,
            product_id=product_id,
            request=request,
            request_id=get_request_id(request),
        )
    db.commit()
    return envelope.ok(
        {"match": product_service.match_out(match, db), "document_status": document.status.value},
        request,
    )


@router.get("/{document_id}/classification")
def document_classification(request: Request, document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.models.extraction import ExtractionJob

    document = document_service.get_document_or_404(db, user, document_id)
    job = db.scalar(
        select(ExtractionJob)
        .where(ExtractionJob.document_id == document.id)
        .order_by(ExtractionJob.id.desc())
    )
    return envelope.ok(
        {
            "classification": job.classification if job else None,
            "ocr_report": job.ocr_report if job else None,
            "pipeline_version": job.pipeline_version if job else None,
        },
        request,
    )
