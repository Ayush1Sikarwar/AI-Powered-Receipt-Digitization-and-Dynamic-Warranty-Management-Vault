"""Claim model (TSD §23).

Claim states: DRAFT, READY, SUBMITTED_EXTERNALLY, IN_PROGRESS, RESOLVED,
CLOSED. The MVP generates a claim-ready package; direct portal submission is a
later integration (TSD §57).

A document referenced by a claim in a NON-TERMINAL state (anything other than
RESOLVED or CLOSED) is subject to a retention hold: it cannot be purged until
the claim reaches a terminal state (SRS NFR-02, TSD §37). This model is present
from Phase 1 so the retention-hold check is real and testable early.
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidMixin


class ClaimStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    SUBMITTED_EXTERNALLY = "SUBMITTED_EXTERNALLY"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"  # terminal
    CLOSED = "CLOSED"  # terminal

    @property
    def is_terminal(self) -> bool:
        return self in (ClaimStatus.RESOLVED, ClaimStatus.CLOSED)


# Non-terminal claim statuses that impose a retention hold on referenced documents.
TERMINAL_STATUSES = frozenset({ClaimStatus.RESOLVED, ClaimStatus.CLOSED})


class Claim(Base, UuidMixin, TimestampMixin):
    __tablename__ = "claims"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchases.id", ondelete="SET NULL"), index=True, nullable=True)
    status: Mapped[ClaimStatus] = mapped_column(Enum(ClaimStatus, name="claim_status"), default=ClaimStatus.DRAFT, nullable=False, index=True)
    issue_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Serialized claim package (generated artifact) stored alongside.
    package: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    package_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClaimFile(Base, UuidMixin, TimestampMixin):
    """Evidence file uploaded by the user against a claim (SRS FR-16/FR-19).

    Owned by the claim's user; both `claim_id` and `user_id` CASCADE so the
    retention purge removes evidence together with the claim/account.
    """

    __tablename__ = "claim_files"

    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class ClaimDocumentRole(enum.StrEnum):
    """User-declared role of an attached stored document (TSD §33).

    The role itself is USER_CONFIRMED provenance (the user chose it); the
    document's own extracted fields keep their own provenance.
    """

    RECEIPT = "RECEIPT"
    WARRANTY = "WARRANTY"
    EVIDENCE = "EVIDENCE"


class ClaimDocument(Base, UuidMixin, TimestampMixin):
    """A stored vault document selected as claim evidence (SRS FR-16).

    Evidence selection: instead of re-uploading, the user attaches an
    already-stored document (receipt, warranty card, supporting photo) to
    the claim. One active attachment per (claim, document).
    """

    __tablename__ = "claim_documents"
    __table_args__ = (UniqueConstraint("claim_id", "document_id", name="uq_claim_document"),)

    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True, nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[ClaimDocumentRole] = mapped_column(Enum(ClaimDocumentRole, name="claim_document_role"), nullable=False)