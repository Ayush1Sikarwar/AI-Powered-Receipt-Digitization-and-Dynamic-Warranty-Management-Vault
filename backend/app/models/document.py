"""Document model (TSD §6-7).

The ORIGINAL stored object is immutable: the processing pipeline never
rewrites it. Derived outputs (enhanced, cropped, OCR preview, thumbnail)
are separate derivative rows with their own storage keys.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidMixin


class DocumentType(enum.StrEnum):
    RECEIPT = "RECEIPT"
    INVOICE = "INVOICE"
    WARRANTY_CARD = "WARRANTY_CARD"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class DocumentStatus(enum.StrEnum):
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DELETED = "DELETED"


class DerivativeType(enum.StrEnum):
    ORIGINAL = "ORIGINAL"
    ENHANCED = "ENHANCED"
    CROPPED = "CROPPED"
    OCR_PREVIEW = "OCR_PREVIEW"
    THUMBNAIL = "THUMBNAIL"


class Document(Base, UuidMixin, TimestampMixin):
    __tablename__ = "documents"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type"), default=DocumentType.UNKNOWN, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), default=DocumentStatus.UPLOADED, nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-delete strategy (TSD §34): deleted_at set on DELETE; row retained for
    # audit/retention until the CLEANUP worker purges per policy.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    derivatives: Mapped[list["DocumentDerivative"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base, TimestampMixin):
    __tablename__ = "document_pages"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_engine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ocr_engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(nullable=True)
    quality_score: Mapped[float | None] = mapped_column(nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")


class DocumentDerivative(Base, TimestampMixin):
    __tablename__ = "document_derivatives"
    __table_args__ = (
        UniqueConstraint("document_id", "derivative_type", "page_number", name="uq_derivative"),
    )

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False)
    derivative_type: Mapped[DerivativeType] = mapped_column(Enum(DerivativeType, name="derivative_type"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = whole document
    processing_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    document: Mapped[Document] = relationship(back_populates="derivatives")
