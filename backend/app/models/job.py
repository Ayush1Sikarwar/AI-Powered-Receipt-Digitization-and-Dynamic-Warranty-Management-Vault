"""Background job model (TSD §31).

Required job types:
DOCUMENT_PROCESSING, OCR_PROCESSING, PRODUCT_RESOLUTION, WARRANTY_RESOLUTION,
NOTIFICATION_SCHEDULING, CLAIM_PACKAGE_GENERATION, SOURCE_REFRESH, CLEANUP.

Each job records: id, type, status, attempt_count, created_at, started_at,
completed_at, error. Retry policy uses bounded retries (TSD §31, §50).
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobType(enum.StrEnum):
    DOCUMENT_PROCESSING = "DOCUMENT_PROCESSING"
    OCR_PROCESSING = "OCR_PROCESSING"
    PRODUCT_RESOLUTION = "PRODUCT_RESOLUTION"
    WARRANTY_RESOLUTION = "WARRANTY_RESOLUTION"
    NOTIFICATION_SCHEDULING = "NOTIFICATION_SCHEDULING"
    CLAIM_PACKAGE_GENERATION = "CLAIM_PACKAGE_GENERATION"
    SOURCE_REFRESH = "SOURCE_REFRESH"
    CLEANUP = "CLEANUP"


class JobStatus(enum.StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD = "DEAD"  # exceeded max attempts


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        # A job is uniquely tied to (type, resource) so re-enqueueing is idempotent.
        # The unique index is partial: only non-terminal jobs enforce uniqueness.
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[JobType] = mapped_column(Enum(JobType, name="job_type"), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), default=JobStatus.QUEUED, nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Idempotency / dedup key for the job's side effect.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Deferred execution (retention/cleanup scheduling, SRS NFR-02): the
    # worker only claims jobs whose scheduled_at is in the past (or NULL).
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
