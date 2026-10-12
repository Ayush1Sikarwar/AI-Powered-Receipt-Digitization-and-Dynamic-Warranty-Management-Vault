"""Notification endpoints (SRS FR-15). Every query is user-scoped; foreign
notifications are invisible (404), consistent with resource isolation.

Endpoints:
  GET  /notifications              history (filter: unread, status, page)
  PATCH /notifications/{id}/read   mark one read
  POST /notifications/read-all     mark all read
  GET  /notifications/preferences  per-user channel toggles + timezone
  PUT  /notifications/preferences  update channel toggles
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db
from app.models.notification import Notification, NotificationStatus
from app.models.notification_preferences import NotificationPreferences
from app.models.user import User

router = APIRouter()


def notification_out(n: Notification) -> dict:
    return {
        "id": n.id,
        "category": n.category.value,
        "title": n.title,
        "body": n.body,
        "purchase_id": n.purchase_id,
        "claim_id": n.claim_id,
        "expiry_date_snapshot": n.expiry_date_snapshot.isoformat() if n.expiry_date_snapshot else None,
        "reminder_type": n.reminder_type,
        "status": n.status.value,
        "scheduled_at": n.scheduled_at.isoformat() if n.scheduled_at else None,
        "sent_at": n.sent_at.isoformat() if n.sent_at else None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "attempts": n.attempts,
        "last_error": n.last_error,
        "next_retry_at": n.next_retry_at.isoformat() if n.next_retry_at else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _get_owned_notification(db: Session, user: User, notification_id: int) -> Notification:
    from app.core.exceptions import NotFoundError

    row = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id)
    )
    if row is None:
        raise NotFoundError("Notification not found")
    return row


@router.get("")
def list_notifications(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread: bool = Query(False),
    status: NotificationStatus | None = Query(None),
):
    unread_total = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.status == NotificationStatus.SENT
        )
    ) or 0
    stmt = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if unread:
        stmt = stmt.where(Notification.status == NotificationStatus.SENT)
    if status is not None:
        stmt = stmt.where(Notification.status == status)
    rows = db.scalars(stmt).all()
    return envelope.ok({"items": [notification_out(n) for n in rows], "unread_count": unread_total}, request)


@router.patch("/{notification_id}/read")
def mark_read(request: Request, notification_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _get_owned_notification(db, user, notification_id)
    row.status = NotificationStatus.READ
    row.read_at = datetime.now(UTC)
    db.commit()
    return envelope.ok(notification_out(row), request)


@router.post("/read-all")
def mark_all_read(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.status == NotificationStatus.SENT
        )
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.status = NotificationStatus.READ
        row.read_at = now
    db.commit()
    return envelope.ok({"marked_read": len(rows)}, request)


# --- Preferences ---------------------------------------------------------------


class PreferencesUpdate(BaseModel):
    email_enabled: bool | None = Field(default=None)
    push_enabled: bool | None = Field(default=None)


def preferences_out(db: Session, user: User) -> dict:
    prefs = db.scalar(select(NotificationPreferences).where(NotificationPreferences.user_id == user.id))
    from app.notifications.email import email_status
    from app.notifications.push import push_status

    return {
        "email_enabled": prefs.email_enabled if prefs else True,
        "push_enabled": prefs.push_enabled if prefs else False,
        "timezone": user.timezone,
        # Honest channel state: what the worker can actually deliver.
        "channels": {"email": email_status(), "push": push_status()},
    }


@router.get("/preferences")
def get_preferences(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return envelope.ok(preferences_out(db, user), request)


@router.put("/preferences")
def update_preferences(
    body: PreferencesUpdate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    prefs = db.scalar(select(NotificationPreferences).where(NotificationPreferences.user_id == user.id))
    if prefs is None:
        prefs = NotificationPreferences(user_id=user.id)
        db.add(prefs)
    if body.email_enabled is not None:
        prefs.email_enabled = body.email_enabled
    if body.push_enabled is not None:
        prefs.push_enabled = body.push_enabled
    db.commit()
    db.refresh(prefs)
    return envelope.ok(preferences_out(db, user), request)
