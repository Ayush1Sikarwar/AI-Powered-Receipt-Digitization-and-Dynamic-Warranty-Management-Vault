"""Job queue helpers (TSD §31).

The worker polls the background_jobs table (Postgres is the source of truth,
which keeps job state observable and durable). Claiming uses
`SELECT ... FOR UPDATE SKIP LOCKED` so multiple worker replicas never double
process. Retries are bounded (`max_attempts`) and a dedup key makes
re-enqueue idempotent (TSD §31: bounded retries, avoid duplicate side
effects).

Stale-job recovery: if a worker crashes after claiming a job (status RUNNING)
but before completing or failing it, the job is unreclaimable because
claim_next_job only selects QUEUED jobs. recover_stale_jobs() resets such
jobs back to QUEUED so another worker can pick them up. A crash is not a
processing failure, so attempt_count is not burned by recovery.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.job import BackgroundJob, JobStatus, JobType

logger = logging.getLogger(__name__)

# Jobs stuck in RUNNING longer than this are considered stale (worker crash).
STALE_JOB_THRESHOLD_MINUTES = 10

# Exponential backoff base (minutes) for retry re-queues: 2^attempt * base,
# capped at RETRY_BACKOFF_MAX_MINUTES.
RETRY_BACKOFF_BASE_MINUTES = 2
RETRY_BACKOFF_MAX_MINUTES = 60


def utcnow() -> datetime:
    return datetime.now(UTC)


def enqueue_job(
    db: Session,
    *,
    type: JobType,
    resource_type: str,
    resource_id: str,
    payload: dict | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    scheduled_at: datetime | None = None,
) -> BackgroundJob:
    """Idempotent enqueue: if a non-terminal job with the same idempotency key
    exists, return it instead of creating a duplicate.

    `scheduled_at` defers execution (retention/cleanup jobs, SRS NFR-02);
    the worker only claims jobs due now or in the past.
    """
    if idempotency_key:
        existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key))
        if existing is not None and existing.status not in (JobStatus.COMPLETED, JobStatus.DEAD):
            return existing
    job = BackgroundJob(
        type=type,
        status=JobStatus.QUEUED,
        resource_type=resource_type,
        resource_id=str(resource_id),
        payload=payload or {},
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        scheduled_at=scheduled_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_job(db: Session, types: list[JobType] | None = None) -> BackgroundJob | None:
    """Atomically claim one queued job that is due for execution."""

    stmt = select(BackgroundJob.id).where(
        BackgroundJob.status == JobStatus.QUEUED,
        or_(BackgroundJob.scheduled_at.is_(None), BackgroundJob.scheduled_at <= utcnow()),
    )
    if types:
        stmt = stmt.where(BackgroundJob.type.in_(types))
    stmt = stmt.order_by(BackgroundJob.id).limit(1)
    # For UPDATE SKIP LOCKED via a CTE-like manual step:
    job_id = db.execute(stmt.with_for_update(skip_locked=True)).scalars().first()
    if job_id is None:
        return None
    job = db.get(BackgroundJob, job_id, with_for_update=True)
    if job is None or job.status != JobStatus.QUEUED:
        return None
    job.status = JobStatus.RUNNING
    job.attempt_count += 1
    job.started_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def complete_job(db: Session, job: BackgroundJob) -> None:
    job.status = JobStatus.COMPLETED
    job.completed_at = utcnow()
    job.error = None
    job.error_code = None
    db.commit()


def fail_job(db: Session, job: BackgroundJob, *, error: str, error_code: str | None = None) -> None:
    """Mark a job failed; requeue with backoff if attempts remain, else DEAD.

    When re-queuing for retry, scheduled_at is set with exponential backoff
    so a repeatedly failing job does not spin in a tight loop. When the job
    goes DEAD and it is a DOCUMENT_PROCESSING job, the associated document is
    marked FAILED so it does not remain stuck in QUEUED/PROCESSING.
    """
    job.error = error[:2000]
    job.error_code = error_code

    if job.attempt_count >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.completed_at = utcnow()
        _mark_document_failed_on_dead(db, job)
        logger.error("job_dead job_id=%s type=%s error=%s", job.id, job.type, error)
    else:
        backoff = min(
            RETRY_BACKOFF_MAX_MINUTES,
            RETRY_BACKOFF_BASE_MINUTES * (2 ** (job.attempt_count - 1)),
        )
        job.status = JobStatus.QUEUED
        job.scheduled_at = utcnow() + timedelta(minutes=backoff)
        logger.warning(
            "job_retry job_id=%s attempt=%s error=%s backoff=%sm",
            job.id,
            job.attempt_count,
            error,
            backoff,
        )
    db.commit()


def _mark_document_failed_on_dead(db: Session, job: BackgroundJob) -> None:
    """Mark the associated document as FAILED when a processing job goes DEAD.

    Prevents a document from being stuck in QUEUED/PROCESSING when its
    processing job has exhausted all attempts.
    """
    if job.type != JobType.DOCUMENT_PROCESSING or job.resource_type != "document":
        return
    try:
        doc_id = int(job.resource_id)
    except (TypeError, ValueError):
        return
    from app.models.document import Document, DocumentStatus

    document = db.get(Document, doc_id)
    if document is None:
        return
    if document.status in (DocumentStatus.COMPLETED, DocumentStatus.FAILED, DocumentStatus.DELETED):
        return
    document.status = DocumentStatus.FAILED
    document.error_code = job.error_code or "PROCESSING_ERROR"
    document.error_message = job.error or "Processing job exhausted all attempts"
    db.add(document)


def recover_stale_jobs(
    db: Session,
    *,
    stale_threshold_minutes: int | None = None,
    types: list[JobType] | None = None,
) -> int:
    """Re-queue jobs stuck in RUNNING state beyond the threshold.

    A worker crash after claim_next_job sets status RUNNING leaves the job
    unreclaimable (claim_next_job only selects QUEUED). This resets stale
    RUNNING jobs back to QUEUED so another worker can pick them up.

    attempt_count is NOT incremented — a crash is not a processing failure.
    started_at is cleared and scheduled_at is set to now so the job is
    immediately eligible for re-claiming.

    Returns the number of jobs recovered.
    """
    threshold = stale_threshold_minutes or STALE_JOB_THRESHOLD_MINUTES
    cutoff = utcnow() - timedelta(minutes=threshold)

    stmt = select(BackgroundJob.id).where(
        BackgroundJob.status == JobStatus.RUNNING,
        BackgroundJob.started_at < cutoff,
    )
    if types:
        stmt = stmt.where(BackgroundJob.type.in_(types))
    stale_ids = db.execute(stmt).scalars().all()

    count = 0
    for job_id in stale_ids:
        job = db.get(BackgroundJob, job_id)
        if job is None or job.status != JobStatus.RUNNING:
            continue
        job.status = JobStatus.QUEUED
        job.started_at = None
        job.scheduled_at = utcnow()
        job.error = "recovered_from_stale_running"
        job.error_code = "STALE_JOB_RECOVERED"
        count += 1

    if count:
        db.commit()
        logger.info("stale_jobs_recovered count=%s", count)
    return count