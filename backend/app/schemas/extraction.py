"""Schemas for extraction field review (FR-07, FR-17)."""

from pydantic import BaseModel, Field


class FieldOut(BaseModel):
    id: int
    field_name: str
    value: str | None
    normalized_value: str | None
    original_value: str | None
    confidence: float | None
    source_page: int | None
    source_region: str | None
    extraction_method: str
    model_name: str
    model_version: str
    verification_status: str
    is_critical: bool
    requires_review: bool


class FieldCorrection(BaseModel):
    field_name: str = Field(min_length=1, max_length=40)
    action: str = Field(pattern="^(confirm|correct|reject)$")
    value: str | None = Field(default=None, max_length=500)
