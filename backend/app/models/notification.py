"""Notification model (TSD §22/§32, SRS FR-15).

`notification_key` is the idempotency key required by TSD §32:
``purchase_id + coverage_id + expiry_date_snapshot + reminder_type``.
A changed expiry date produces a distinct key; stale pending rows are
cancelled (status=CANCELLED) rather than reused, so a user correction
that moves the expiry date creates the new correct reminder instead of
suppressing it.

Delivery (worker): SCHEDULED -> SENT (all enabled channels delivered) /
FAILED (max_attempts exhausted) / SUPPRESSED (user disabled every
channel at dispatch time) / CANCELLED (superseded). RETRY keeps the row
SCHEDULED with attempts + next_retry_at (exponential backoff); a retry
only re-sends channels without a sent marker (channel-level idempotency).
"""

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class NotificationCategory(enum.StrEnum):
    WARRANTY_EXPIRING = "WARRANTY_EXPIRING"
    PROCESSING_COMPLETE = "PROCESSING_COMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CLAIM_READY = "CLAIM_READY"
    SYSTEM = "SYSTEM"


class NotificationStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    READ = "READ"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_sched", "user_id", "scheduled_at"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    category: Mapped[NotificationCategory] = mapped_column(
        Enum(NotificationCategory, name="notification_category"),
        default=NotificationCategory.SYSTEM,
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    purchase_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchases.id", ondelete="SET NULL"), index=True, nullable=True
    )
    claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("claims.id", ondelete="SET NULL"), index=True, nullable=True
    )
    warranty_mapping_id: Mapped[int | None] = mapped_column(
        ForeignKey("warranty_mappings.id", ondelete="SET NULL"), nullable=True
    )
    expiry_date_snapshot: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminder_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # e.g. "60d", "30d", "7d"
    notification_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status"),
        default=NotificationStatus.SCHEDULED,
        nullable=False,
        index=True,
    )
    # --- Delivery / retry handling ---------------------------------------------
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False, server_default="3")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-channel sent markers: a retry only re-sends channels without one.
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
