"""Extraction models (TSD §8).

- ExtractionJob: one processing execution (pipeline versioned).
- ExtractedField: every extracted field with value, confidence, source
  (page/region), method/model, and verification status.

User corrections (FR-17) never overwrite the original AI extraction: the
AI value is preserved in `original_value` and the document's own extraction
history stays auditable. The original uploaded document is never touched.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidMixin


class ExtractionJobStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class VerificationStatus(enum.StrEnum):
    AI_EXTRACTED = "AI_EXTRACTED"
    USER_CONFIRMED = "USER_CONFIRMED"
    USER_CORRECTED = "USER_CORRECTED"
    REJECTED = "REJECTED"


# Supported fields (TSD §8.2).
SUPPORTED_FIELDS = (
    "retailer",
    "purchase_date",
    "invoice_number",
    "product_name",
    "manufacturer",
    "sku",
    "model_number",
    "serial_number",
    "quantity",
    "unit_price",
    "total_price",
    "tax",
    "currency",
)

# Critical fields requiring human review when low-confidence (TSD §46).
CRITICAL_FIELDS = frozenset(
    {
        "purchase_date",
        "product_name",
        "model_number",
        "sku",
        "serial_number",
        "total_price",
    }
)


class ExtractionJob(Base, UuidMixin, TimestampMixin):
    __tablename__ = "extraction_jobs"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ExtractionJobStatus] = mapped_column(
        Enum(ExtractionJobStatus, name="extraction_job_status"),
        default=ExtractionJobStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OCR provenance: per-page engine, confidence, and comparison result (TSD §26).
    ocr_report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Document classification result (FR-03).
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ExtractedField(Base, TimestampMixin):
    __tablename__ = "extracted_fields"
    __table_args__ = (
        # One active value per (document, field). Corrections update the row in
        # place but preserve original_value, so the AI result is never lost.
        UniqueConstraint("document_id", "field_name", name="uq_field_doc"),
    )

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False)
    extraction_job_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="SET NULL"), index=True, nullable=True)
    field_name: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    field_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The AI's original extraction, preserved when the user corrects/rejects.
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_region: Mapped[str | None] = mapped_column(String(120), nullable=True)  # e.g. "line 12"
    extraction_method: Mapped[str] = mapped_column(String(40), default="rule", nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"),
        default=VerificationStatus.AI_EXTRACTED,
        nullable=False,
    )
