"""Schemas for document endpoints (TSD §18)."""

from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: int
    uuid: str
    document_type: str
    original_filename: str
    mime_type: str
    file_size: int
    page_count: int
    status: str
    sha256: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class DocumentStatusOut(BaseModel):
    id: int
    status: str
    processing: bool
    current_stage: str | None = None
    pipeline_version: str | None = None
    error: dict | None = None
    updated_at: datetime


class DerivativeOut(BaseModel):
    id: int
    derivative_type: str
    page_number: int
    processing_version: str
    mime_type: str
    metadata: dict


class PageOut(BaseModel):
    page_number: int
    width: int | None
    height: int | None
    quality_score: float | None
    ocr_engine: str | None
    ocr_confidence: float | None
