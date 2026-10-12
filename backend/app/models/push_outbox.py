"""Push outbox for the log/dev push provider (mirrors email_outbox).

When the push provider is `log`, outbound push messages are recorded here
so the flow is inspectable and testable. With `none` (default), the push
provider reports `configured: false` and no push is attempted — callers
surface an honest "push not configured" state instead of pretending
delivery happened.
"""

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PushOutbox(Base, TimestampMixin):
    __tablename__ = "push_outbox"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), default="LOG", nullable=False)
