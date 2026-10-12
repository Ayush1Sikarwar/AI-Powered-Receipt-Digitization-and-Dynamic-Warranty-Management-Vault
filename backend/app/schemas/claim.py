"""Schemas for claims, claim evidence, and notifications (SRS FR-15/FR-16)."""

import enum

from pydantic import BaseModel, Field


class ClaimCreate(BaseModel):
    purchase_id: int
    issue_description: str = Field(min_length=3, max_length=4000)
    issue_category: str | None = Field(default=None, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=4000)


class ClaimDocumentRoleLiteral(enum.StrEnum):
    RECEIPT = "RECEIPT"
    WARRANTY = "WARRANTY"
    EVIDENCE = "EVIDENCE"


class ClaimDocumentCreate(BaseModel):
    """Attach an already-stored vault document as claim evidence (SRS FR-16)."""

    document_id: int
    role: ClaimDocumentRoleLiteral = ClaimDocumentRoleLiteral.EVIDENCE


class ClaimUpdate(BaseModel):
    """Editable claim fields.

    Deliberately has NO status field: in the MVP a claim only advances
    DRAFT -> READY by generating its package (TSD §57). Status transitions
    beyond READY (external submission, resolution) require official
    integrations / manual user action and are not exposed here.
    """

    issue_description: str | None = Field(default=None, min_length=3, max_length=4000)
    issue_category: str | None = Field(default=None, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=4000)


class ClaimOut(BaseModel):
    id: int
    uuid: str
    purchase_id: int | None
    status: str
    issue_description: str | None
    issue_category: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    notes: str | None
    package_generated_at: str | None
    created_at: str
    updated_at: str


class EvidenceOut(BaseModel):
    id: int
    uuid: str
    original_filename: str
    mime_type: str
    file_size: int
    sha256: str
    created_at: str


class NotificationOut(BaseModel):
    id: int
    category: str
    title: str
    body: str | None
    purchase_id: int | None
    status: str
    scheduled_at: str | None
    sent_at: str | None
    read_at: str | None
    created_at: str
