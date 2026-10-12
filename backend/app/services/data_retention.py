"""Retention policy engine (SRS NFR-02, TSD §37).

Real purge operations executed by the CLEANUP worker:

- ``schedule_document_purge``: enqueues a deferred hard-purge for a
  user-deleted document (grace period, then re-checks the open-claim hold).
- ``schedule_user_purge``: enqueues the closed-account purge due
  ``retention_closed_account_days`` after closure.
- ``purge_document`` / ``purge_user_data``: idempotent hard deletes of
  personal data (rows + storage objects).
- ``retention_sweep``: finds closed accounts whose purge window has elapsed
  and enqueues their cleanup (the periodic retention-policy hook).

Audit logs are NEVER purged here: NFR-02 retains them >= 1 year
independently of document/account deletion.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import AuditAction, audit
from app.core.config import get_settings
from app.models.auth_token import PasswordResetToken, RefreshToken
from app.models.claim import Claim, ClaimFile
from app.models.document import Document, DocumentDerivative, DocumentPage
from app.models.extraction import ExtractedField, ExtractionJob
from app.models.job import BackgroundJob, JobType
from app.models.notification import Notification
from app.models.purchase import Purchase, PurchaseItem
from app.models.user import User, UserStatus
from app.services import storage as storage_service
from app.workers.jobs import enqueue_job, utcnow

logger = logging.getLogger(__name__)
settings = get_settings()


class RetentionHold(Exception):
    """The purge must wait: an open (non-terminal) claim references the data."""

    def __init__(self, reason: str, claim_ids: list[int]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.claim_ids = claim_ids


def open_claims_for_purchases(db: Session, purchase_ids: list[int]) -> list[Claim]:
    if not purchase_ids:
        return []
    from app.models.claim import TERMINAL_STATUSES

    return list(
        db.scalars(
            select(Claim).where(Claim.purchase_id.in_(purchase_ids), Claim.status.notin_(list(TERMINAL_STATUSES)))
        ).all()
    )


def user_has_open_claims(db: Session, user_id: int) -> list[Claim]:
    from app.models.claim import TERMINAL_STATUSES

    return list(
        db.scalars(
            select(Claim).where(Claim.user_id == user_id, Claim.status.notin_(list(TERMINAL_STATUSES)))
        ).all()
    )


def _delete_storage(keys: list[str]) -> int:
    deleted = 0
    store = storage_service.get_storage()
    for key in keys:
        try:
            if store.exists(key):
                store.delete(key)
                deleted += 1
        except Exception as exc:  # noqa: BLE001 - storage failure must not wedge the DB purge
            logger.warning("storage_delete_failed key=%s err=%s", key, exc)
    return deleted


def purge_document(db: Session, *, document_id: int) -> dict:
    """Hard-delete a document and everything derived from it.

    Idempotent: if the document row is already gone, this is a no-op.
    Re-checks the open-claim hold at purge time, so a claim opened during
    the deletion grace period still blocks the purge (the handler re-queues).
    """
    document = db.get(Document, document_id)
    if document is None:
        return {"purged": False, "reason": "already_purged"}

    purchase_ids = [
        p.id
        for p in db.scalars(
            select(Purchase).where(Purchase.document_id == document_id, Purchase.deleted_at.is_(None))
        ).all()
    ]
    hold_claims = open_claims_for_purchases(db, purchase_ids)
    if hold_claims:
        raise RetentionHold("document_referenced_by_open_claim", [c.id for c in hold_claims])

    fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == document_id)).all()
    jobs = db.scalars(select(ExtractionJob).where(ExtractionJob.document_id == document_id)).all()
    pages = db.scalars(select(DocumentPage).where(DocumentPage.document_id == document_id)).all()
    derivatives = db.scalars(
        select(DocumentDerivative).where(DocumentDerivative.document_id == document_id)
    ).all()

    storage_keys = [document.storage_key] + [d.storage_key for d in derivatives]
    deleted_files = _delete_storage(storage_keys)

    for row in [*fields, *jobs, *pages, *derivatives]:
        db.delete(row)
    # Purchase.document_id is ON DELETE SET NULL; the back-reference clears
    # automatically when the document row is removed.
    db.delete(document)
    db.commit()

    audit(
        db,
        action=AuditAction.DOCUMENT_PURGED,
        resource_type="document",
        resource_id=str(document_id),
        metadata={"storage_objects_deleted": deleted_files},
    )
    db.commit()
    logger.info("document_purged doc_id=%s storage_objects=%s", document_id, deleted_files)
    return {"purged": True, "storage_objects_deleted": deleted_files}


def purge_user_data(db: Session, *, user_id: int) -> dict:
    """Hard-delete all personal data for a closed account (SRS NFR-02).

    Order: storage objects first, then claims + evidence, purchases (+ items
    cascade warranty mappings/timelines), documents, notifications, auth
    artifacts, finally the user row (FK cascades catch anything left).
    Audit logs are retained (>= 365 days) and intentionally not touched.
    """
    user = db.get(User, user_id)
    if user is None:
        return {"purged": False, "reason": "already_purged"}

    open_claims = user_has_open_claims(db, user_id)
    if open_claims:
        raise RetentionHold("open_claims_exist", [c.id for c in open_claims])

    # --- storage objects first (documents, derivatives, claim evidence) ---
    documents = list(db.scalars(select(Document).where(Document.user_id == user_id)).all())
    doc_ids = [d.id for d in documents]
    storage_keys: list[str] = [d.storage_key for d in documents]
    if doc_ids:
        storage_keys += [
            d.storage_key
            for d in db.scalars(
                select(DocumentDerivative).where(DocumentDerivative.document_id.in_(doc_ids))
            ).all()
        ]
    claim_files = list(db.scalars(select(ClaimFile).where(ClaimFile.user_id == user_id)).all())
    storage_keys += [f.storage_key for f in claim_files]
    deleted_files = _delete_storage(storage_keys)

    # --- derived extraction data ---
    if doc_ids:
        for ef in db.scalars(select(ExtractedField).where(ExtractedField.document_id.in_(doc_ids))).all():
            db.delete(ef)
        for ej in db.scalars(select(ExtractionJob).where(ExtractionJob.document_id.in_(doc_ids))).all():
            db.delete(ej)
        for pg in db.scalars(select(DocumentPage).where(DocumentPage.document_id.in_(doc_ids))).all():
            db.delete(pg)
        for der in db.scalars(
            select(DocumentDerivative).where(DocumentDerivative.document_id.in_(doc_ids))
        ).all():
            db.delete(der)

    # --- claims + evidence ---
    claim_ids = [c.id for c in db.scalars(select(Claim).where(Claim.user_id == user_id)).all()]
    for f in claim_files:
        db.delete(f)
    for c in db.scalars(select(Claim).where(Claim.user_id == user_id)).all():
        db.delete(c)

    # --- purchases (+ items cascade warranty mappings/timelines) ---
    purchase_ids = [
        p.id for p in db.scalars(select(Purchase).where(Purchase.user_id == user_id)).all()
    ]
    if purchase_ids:
        for i in db.scalars(select(PurchaseItem).where(PurchaseItem.purchase_id.in_(purchase_ids))).all():
            db.delete(i)
        for p in db.scalars(select(Purchase).where(Purchase.id.in_(purchase_ids))).all():
            db.delete(p)

    # --- documents ---
    for d in documents:
        db.delete(d)

    # --- notifications + auth artifacts + dev outbox addressed to the user ---
    for n in db.scalars(select(Notification).where(Notification.user_id == user_id)).all():
        db.delete(n)
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)).all():
        db.delete(rt)
    for prt in db.scalars(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)).all():
        db.delete(prt)

    db.delete(user)
    db.commit()

    # Outbox rows addressed to the user's address (personal data, log provider).
    from app.models.email_outbox import EmailOutbox

    for ob in db.scalars(select(EmailOutbox).where(EmailOutbox.to_address == user.email)).all():
        db.delete(ob)
    audit(
        db,
        action=AuditAction.ACCOUNT_PURGED,
        resource_type="user",
        resource_id=str(user_id),
        metadata={
            "documents": len(doc_ids),
            "claims": len(claim_ids),
            "purchases": len(purchase_ids),
            "storage_objects_deleted": deleted_files,
            "audit_retention_days_min": settings.retention_audit_min_days,
        },
    )
    db.commit()
    logger.info(
        "account_purged user_id=%s docs=%s claims=%s purchases=%s storage=%s",
        user_id, len(doc_ids), len(claim_ids), len(purchase_ids), deleted_files,
    )
    return {
        "purged": True,
        "documents": len(doc_ids),
        "claims": len(claim_ids),
        "purchases": len(purchase_ids),
        "storage_objects_deleted": deleted_files,
    }


def schedule_document_purge(db: Session, *, document_id: int) -> None:
    """Deferred hard-purge after the configured grace period (SRS NFR-02)."""
    enqueue_job(
        db,
        type=JobType.CLEANUP,
        resource_type="document",
        resource_id=str(document_id),
        idempotency_key=f"doc_purge:{document_id}",
        scheduled_at=utcnow() + timedelta(days=settings.retention_deleted_document_days),
        max_attempts=5,
    )


def schedule_user_purge(db: Session, *, user_id: int) -> None:
    """Closed-account purge due `retention_closed_account_days` after closure."""
    enqueue_job(
        db,
        type=JobType.CLEANUP,
        resource_type="user",
        resource_id=str(user_id),
        idempotency_key=f"user_purge:{user_id}",
        scheduled_at=utcnow() + timedelta(days=settings.retention_closed_account_days),
        max_attempts=5,
    )


def retention_sweep(db: Session) -> int:
    """Periodic retention-policy hook: enqueue purges for closed accounts
    whose retention window has elapsed and that have no open claims.

    Returns how many NEW purge jobs were enqueued (a repeat sweep over
    already-scheduled accounts returns 0).
    """
    deadline = utcnow() - timedelta(days=settings.retention_closed_account_days)
    due_users = db.scalars(
        select(User).where(
            User.status == UserStatus.CLOSED,
            User.closed_at.is_not(None),
            User.closed_at <= deadline,
        )
    ).all()
    new_jobs = 0
    for user in due_users:
        if user_has_open_claims(db, user.id):
            continue
        key = f"user_purge:{user.id}"
        if db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key)) is not None:
            continue
        enqueue_job(
            db,
            type=JobType.CLEANUP,
            resource_type="user",
            resource_id=str(user.id),
            idempotency_key=key,
        )
        new_jobs += 1
    if new_jobs:
        logger.info("retention_sweep new_purge_jobs=%s", new_jobs)
    return new_jobs
