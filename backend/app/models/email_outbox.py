"""Email outbox for the log/dev email provider.

In development/staging the `log` email provider records outbound messages here
so the flow is inspectable and testable. When a real SMTP provider is
configured, messages are delivered directly and this table is bypassed.
"""


from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EmailOutbox(Base, TimestampMixin):
    __tablename__ = "email_outbox"

    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), default="LOG", nullable=False)
