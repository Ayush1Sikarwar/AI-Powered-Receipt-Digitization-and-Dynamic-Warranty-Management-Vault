"""Per-user notification preferences (SRS FR-15).

Defaults (when no row exists): email ON, push OFF — push is opt-in and
also depends on a push provider being configured. The timezone used for
reminder delivery comes from ``users.timezone`` (part of the account),
not from this table.
"""

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class NotificationPreferences(Base, TimestampMixin):
    __tablename__ = "notification_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="true")
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
