"""Job queue helpers (TSD §31).

The worker polls the background_jobs table (Postgres is the source of truth,
which keeps job state observable and durable). Claiming uses
`SELECT ... FOR UPDATE SKIP LOCKED` so multiple worker replicas never double
process. Retries are bounded (`max_attempts`) and a dedup key makes
re-enqueue idempotent (TSD §31: bounded retries, avoid duplicate side
effects).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.job import BackgroundJob, JobStatus, JobType

logger = logging.getLogger(__name__)


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
    """Mark a job failed; requeue if attempts remain, else DEAD."""
    job.error = error[:2000]
    job.error_code = error_code
    if job.attempt_count >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.completed_at = utcnow()
        logger.error("job_dead job_id=%s type=%s error=%s", job.id, job.type, error)
    else:
        job.status = JobStatus.QUEUED
        logger.warning("job_retry job_id=%s attempt=%s error=%s", job.id, job.attempt_count, error)
    db.commit()
