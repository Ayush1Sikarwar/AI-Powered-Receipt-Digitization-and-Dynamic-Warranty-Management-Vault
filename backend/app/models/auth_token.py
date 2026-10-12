"""Refresh tokens: stored hashed, individually revocable (SRS FR-01 security)."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RefreshTokenStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    ROTATED = "ROTATED"  # replaced by a newer token during refresh


class PasswordResetToken(Base, TimestampMixin):
    """One-time password-reset tokens; stored hashed, single-use, short-lived."""

    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[RefreshTokenStatus] = mapped_column(
        Enum(RefreshTokenStatus, name="refresh_token_status"),
        default=RefreshTokenStatus.ACTIVE,
        nullable=False,
    )
    replaced_by_jti: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @property
    def is_revoked(self) -> bool:
        return self.status in (RefreshTokenStatus.REVOKED, RefreshTokenStatus.ROTATED) or self.revoked_at is not None
