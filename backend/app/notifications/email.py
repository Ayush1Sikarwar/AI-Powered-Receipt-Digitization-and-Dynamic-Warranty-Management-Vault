"""Email delivery provider interface.

- `LogEmailProvider` (default in development): records the message in the
  `email_outbox` table and in structured logs. Honest about being a dev
  transport — the `delivery` field on outbox rows says LOG, and the UI
  settings page reports email delivery status accordingly.
- `SmtpEmailProvider`: used only when SMTP settings are configured in the
  environment. If SMTP is not configured, `is_configured()` is False and
  callers must surface an honest "email not configured" state rather than
  pretending delivery happened.
"""

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage as _PyEmail

from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    to: str
    subject: str
    body_text: str
    payload: dict = field(default_factory=dict)
    delivery: str = "LOG"


class EmailProvider:
    def send(self, db: Session, message: EmailMessage) -> bool:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError


class LogEmailProvider(EmailProvider):
    def send(self, db: Session, message: EmailMessage) -> bool:
        from app.models.email_outbox import EmailOutbox

        try:
            db.add(
                EmailOutbox(
                    to_address=message.to,
                    subject=message.subject,
                    body_text=message.body_text,
                    payload=message.payload,
                    delivery="LOG",
                )
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("email_outbox_write_failed to=%s", message.to)
            return False
        logger.info("email_logged to=%s subject=%r", message.to, message.subject)
        return True

    def is_configured(self) -> bool:
        return True  # log transport is always available; it is not a real mailbox


class SmtpEmailProvider(EmailProvider):
    def __init__(self) -> None:
        s = get_settings()
        self._host = s.smtp_host
        self._port = s.smtp_port
        self._user = s.smtp_user
        self._password = s.smtp_password
        self._from = s.smtp_from

    def is_configured(self) -> bool:
        return bool(self._host)

    def send(self, db: Session, message: EmailMessage) -> bool:
        if not self.is_configured():
            logger.error("smtp_not_configured to=%s subject=%r", message.to, message.subject)
            return False
        try:
            host = self._host
            if host is None:
                return False
            msg = _PyEmail()
            msg["From"] = self._from
            msg["To"] = message.to
            msg["Subject"] = message.subject
            msg.set_content(message.body_text)
            with smtplib.SMTP(host, self._port, timeout=15) as server:
                server.starttls()
                if self._user:
                    server.login(self._user, self._password or "")
                server.send_message(msg)
            message.delivery = "SMTP"
            logger.info("email_sent to=%s subject=%r", message.to, message.subject)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("email_send_failed to=%s subject=%r", message.to, message.subject)
            return False


_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        _provider = SmtpEmailProvider() if s.email_provider == "smtp" else LogEmailProvider()
    return _provider


def email_status() -> dict:
    """Honest status for UI/health endpoints."""
    s = get_settings()
    provider = get_email_provider()
    if s.email_provider == "smtp":
        return {"provider": "smtp", "configured": provider.is_configured()}
    return {"provider": "log", "configured": True, "note": "log transport: messages recorded in email_outbox, no real delivery"}
