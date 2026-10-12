"""Job handlers, one per job type (TSD §31).

Phase 1 wires DOCUMENT_PROCESSING. Later phases add:
OCR_PROCESSING, PRODUCT_RESOLUTION, WARRANTY_RESOLUTION,
NOTIFICATION_SCHEDULING, CLAIM_PACKAGE_GENERATION, SOURCE_REFRESH, CLEANUP.
"""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.job import BackgroundJob, JobStatus, JobType

logger = logging.getLogger(__name__)


def _handle_document_processing(db: Session, job: BackgroundJob) -> None:
    from app.ai.pipeline import run_document_pipeline

    document = db.get(Document, int(job.resource_id))
    if document is None:
        raise ValueError(f"document {job.resource_id} not found")
    run_document_pipeline(db, document)


def _handle_cleanup(db: Session, job: BackgroundJob) -> None:
    """Retention/cleanup handler (SRS NFR-02).

    Resource types:
    - ``document``: hard-purge a user-deleted document (re-checks holds).
    - ``user``: hard-purge a closed account's personal data (re-checks holds).

    On a retention hold the job is NOT failed — it is re-queued with a
    +24h backoff so the purge proceeds once the claim resolves.
    """
    from datetime import timedelta

    from app.services import data_retention

    try:
        if job.resource_type == "document":
            data_retention.purge_document(db, document_id=int(job.resource_id))
        elif job.resource_type == "user":
            data_retention.purge_user_data(db, user_id=int(job.resource_id))
        else:
            raise ValueError(f"unknown cleanup resource_type {job.resource_type}")
    except data_retention.RetentionHold as hold:
        job.status = JobStatus.QUEUED
        job.scheduled_at = data_retention.utcnow() + timedelta(hours=24)
        job.attempt_count = max(0, job.attempt_count - 1)  # a hold is not a failure
        job.error = f"retention_hold: {hold.reason} claims={hold.claim_ids}"
        db.commit()
        logger.info(
            "cleanup_requeued job_id=%s resource=%s:%s reason=%s",
            job.id, job.resource_type, job.resource_id, hold.reason,
        )


HANDLERS: dict[JobType, "Callable[[Session, BackgroundJob], None]"] = {
    JobType.DOCUMENT_PROCESSING: _handle_document_processing,
    JobType.CLEANUP: _handle_cleanup,
}
