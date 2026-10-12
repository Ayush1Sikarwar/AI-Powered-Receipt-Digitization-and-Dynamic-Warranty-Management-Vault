"""Audit trail (SRS FR-18, TSD §38).

Auditable events include: LOGIN, DOCUMENT_UPLOADED, DOCUMENT_DELETED,
AI_EXTRACTION_COMPLETED, FIELD_CORRECTED, PRODUCT_CONFIRMED, WARRANTY_MAPPED,
WARRANTY_SOURCE_UPDATED, NOTIFICATION_SENT, CLAIM_CREATED,
CLAIM_PACKAGE_GENERATED, ADMIN_ACTION.

Audit records include: actor, action, resource, timestamp, request_id, metadata.
We never store raw receipt images, passwords, tokens, or unnecessary personal
data. Audit logs are retained >= 1 year regardless of document deletion
(SRS NFR-02), so this table is intentionally NOT soft-deleted with documents.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_action", "actor_id", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(index=True, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="USER", nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
