"""Push delivery provider abstraction (optional channel, SRS FR-15).

Mirrors the email provider design:

- `NoopPushProvider` (default): no push transport is configured.
  `is_configured()` is False and callers must surface an honest
  "push not configured" state rather than pretending delivery.
- `LogPushProvider` (development): records the message in the
  `push_outbox` table and in structured logs. Honest about being a dev
  transport — the `delivery` field says LOG and it is not a real device
  push.

A real provider (FCM / APNs / webhook) implements the same two-method
interface and is selected via the `PUSH_PROVIDER` setting.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class PushMessage:
    user_id: int
    title: str
    body_text: str
    payload: dict = field(default_factory=dict)
    delivery: str = "LOG"


class PushProvider:
    def send(self, db: Session, message: PushMessage) -> bool:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError


class NoopPushProvider(PushProvider):
    def send(self, db: Session, message: PushMessage) -> bool:
        logger.error("push_not_configured user_id=%s title=%r", message.user_id, message.title)
        return False

    def is_configured(self) -> bool:
        return False


class LogPushProvider(PushProvider):
    def send(self, db: Session, message: PushMessage) -> bool:
        from app.models.push_outbox import PushOutbox

        try:
            db.add(
                PushOutbox(
                    user_id=message.user_id,
                    title=message.title,
                    body_text=message.body_text,
                    payload=message.payload,
                    delivery="LOG",
                )
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("push_outbox_write_failed user_id=%s", message.user_id)
            return False
        logger.info("push_logged user_id=%s title=%r", message.user_id, message.title)
        return True

    def is_configured(self) -> bool:
        return True  # log transport is always available; it is not a real device push


_provider: PushProvider | None = None


def get_push_provider() -> PushProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        _provider = LogPushProvider() if s.push_provider == "log" else NoopPushProvider()
    return _provider


def push_status() -> dict:
    """Honest status for UI/system endpoints."""
    s = get_settings()
    provider = get_push_provider()
    if s.push_provider == "log" and provider.is_configured():
        return {
            "provider": "log",
            "configured": True,
            "note": "log transport: messages recorded in push_outbox, no real device push",
        }
    return {"provider": s.push_provider, "configured": False, "note": "no push transport configured"}
