"""Dashboard summary (SRS FR-13, TSD §42).

Aggregates the counts the dashboard renders. Warranty buckets are computed
from the coverage timeline once Phase 4 maps warranties; until then they
report 0 (no coverage exists yet — an honest zero, not a fabricated count).
"Review required" reflects documents in the REVIEW_REQUIRED state plus
purchases awaiting verification.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db
from app.models.document import Document, DocumentStatus
from app.models.purchase import Purchase
from app.models.user import User

router = APIRouter()


@router.get("/dashboard/summary")
def dashboard_summary(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    total_purchases = (
        db.scalar(select(func.count(Purchase.id)).where(Purchase.user_id == user.id, Purchase.deleted_at.is_(None))) or 0
    )
    total_documents = (
        db.scalar(select(func.count(Document.id)).where(Document.user_id == user.id, Document.deleted_at.is_(None))) or 0
    )
    processing_documents = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.user_id == user.id,
                Document.deleted_at.is_(None),
                Document.status.in_([DocumentStatus.QUEUED, DocumentStatus.PROCESSING]),
            )
        )
        or 0
    )
    review_required_documents = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.user_id == user.id,
                Document.deleted_at.is_(None),
                Document.status == DocumentStatus.REVIEW_REQUIRED,
            )
        )
        or 0
    )

    # Warranty buckets (Phase 4 populates these from the rules engine).
    from app.warranty.rules.coverage import warranty_buckets

    buckets = warranty_buckets(db, user)

    recent_documents = db.scalars(
        select(Document)
        .where(Document.user_id == user.id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
        .limit(5)
    ).all()

    return envelope.ok(
        {
            "total_purchases": total_purchases,
            "total_documents": total_documents,
            "processing_documents": processing_documents,
            "review_required_documents": review_required_documents,
            "warranties": buckets,
            "recent_documents": [
                {
                    "id": d.id,
                    "original_filename": d.original_filename,
                    "status": d.status.value,
                    "document_type": d.document_type.value,
                    "created_at": d.created_at,
                }
                for d in recent_documents
            ],
        },
        request,
    )
