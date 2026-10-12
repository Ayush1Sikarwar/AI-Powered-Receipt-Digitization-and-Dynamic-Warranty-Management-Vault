"""Notification scheduling & delivery (SRS FR-15, TSD §22/§32).

Reminder tiers are configurable (default 60/30/7 days before expiry).

Scheduling is idempotent via the TSD §32 key
``purchase_id : coverage/extended identity : expiry_date_snapshot : tier``.
The expiry snapshot at scheduling time is part of the key, so a user
correction that changes the purchase date (and therefore the expiry)
produces a DISTINCT key: the new correct reminder is created while the
stale SCHEDULED row is CANCELLED — never suppressed by accident.

Delivery (background worker) honours per-user preferences:
  - email channel  (provider: log dev outbox / smtp)
  - push channel   (optional provider: none / log dev outbox)
  - no enabled channels -> SUPPRESSED (terminal, honest, not a failure)

Retry handling: a failed attempt increments `attempts` and reschedules
the row (still SCHEDULED) at `next_retry_at` with exponential backoff
(5m, 10m, 20m, ...). After `max_attempts` the row is FAILED with
`last_error`. A retry only re-sends channels without a sent marker
(channel-level idempotency — no double sends).

Timezone handling: the reminder for due date D is scheduled at
`reminder_local_hour` (default 09:00) in the USER'S timezone on D,
stored as UTC. Invalid timezones fall back to UTC (logged).
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.config import get_settings
from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.notification_preferences import NotificationPreferences
from app.models.purchase import Purchase
from app.models.user import User
from app.models.warranty import WarrantyTimeline
from app.notifications.email import EmailMessage, get_email_provider
from app.notifications.push import PushMessage, get_push_provider

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reminder_days() -> list[int]:
    raw = get_settings().warranty_reminder_days
    try:
        days = sorted({int(part) for part in raw.replace(" ", "").split(",") if part})
    except ValueError:
        days = [60, 30, 7]
    return [d for d in days if d > 0] or [60, 30, 7]


def _notification_key(purchase_id: int, coverage_id: int | None, extended_warranty_id: int | None, expiry: date, tier: int) -> str:
    source = f"coverage-{coverage_id}" if coverage_id is not None else f"extended-{extended_warranty_id or 0}"
    return f"purchase-{purchase_id}:{source}:{expiry.isoformat()}:{tier}d"


def _user_tz(db: Session, user_id: int) -> ZoneInfo:
    """The user's IANA timezone; invalid values fall back to UTC (logged)."""
    u = db.get(User, user_id)
    name = (u.timezone if u else None) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError et al.
        logger.warning("invalid_timezone name=%r user_id=%s falling back to UTC", name, user_id)
        return ZoneInfo("UTC")


def _local_moment(due_date: date, hour: int, tz: ZoneInfo) -> datetime:
    """Local `hour:00` on `due_date` in `tz`, normalised to UTC."""
    return datetime.combine(due_date, time(hour=hour, minute=0), tzinfo=tz).astimezone(UTC)


def _product_label(db: Session, purchase_id: int) -> str:
    purchase = db.get(Purchase, purchase_id)
    if purchase is None:
        return "product"
    for item in purchase.items:
        if item.product_name_raw:
            return item.product_name_raw
    return "product"


def get_preferences(db: Session, user_id: int) -> NotificationPreferences:
    """Per-user preferences; defaults are email ON / push OFF."""
    prefs = db.scalar(select(NotificationPreferences).where(NotificationPreferences.user_id == user_id))
    if prefs is None:
        prefs = NotificationPreferences(user_id=user_id)
        db.add(prefs)
        db.flush()
    return prefs


def schedule_warranty_reminders(db: Session, now: datetime | None = None) -> dict:
    """Create SCHEDULED reminders for upcoming expiries (idempotent).

    A tier's reminder moment is `reminder_local_hour` on
    ``expiry - tier`` in the user's timezone. Only moments still in the
    future are scheduled (no backfill of passed moments); once the
    expiry has entered the smallest window, that reminder is created
    immediately due so the user is still alerted.

    Also cancels stale SCHEDULED rows whose expiry snapshot no longer
    matches the live timeline (TSD §32).
    """
    now = now or _utcnow()
    today = now.date()
    s = get_settings()
    hour = s.reminder_local_hour
    days = reminder_days()  # ascending, e.g. [7, 30, 60]
    created = 0

    timelines = (
        db.scalars(
            select(WarrantyTimeline).where(
                WarrantyTimeline.expiry_date.is_not(None),
                WarrantyTimeline.expiry_date >= today,
                WarrantyTimeline.status.in_(["ACTIVE", "EXPIRING"]),
            )
        ).all()
    )

    live_keys: set[str] = set()
    tz_cache: dict[int, ZoneInfo] = {}
    for t in timelines:
        purchase = _purchase_of_timeline(db, t)
        if purchase is None:
            continue
        expiry = t.expiry_date
        if expiry is None:
            continue
        if purchase.user_id not in tz_cache:
            tz_cache[purchase.user_id] = _user_tz(db, purchase.user_id)
        tz = tz_cache[purchase.user_id]
        label = _product_label(db, purchase.id)
        for tier in days:
            due_date = expiry - timedelta(days=tier)
            moment = _local_moment(due_date, hour, tz)
            if moment < now and tier != days[0]:
                # That reminder moment already passed and a smaller window
                # still covers the expiry — the smaller tier will fire.
                continue
            scheduled = moment if moment >= now else now
            key = _notification_key(purchase.id, t.coverage_id, t.extended_warranty_id, expiry, tier)
            live_keys.add(key)
            existing = db.scalar(select(Notification).where(Notification.notification_key == key))
            if existing is not None:
                continue
            db.add(
                Notification(
                    user_id=purchase.user_id,
                    category=NotificationCategory.WARRANTY_EXPIRING,
                    title=f"Warranty expiring in {tier} days: {label}",
                    body=(
                        f"The {t.component} warranty for {label} expires on "
                        f"{expiry.isoformat()}. Gather your invoice and "
                        "prepare a claim if the product has issues."
                    ),
                    purchase_id=purchase.id,
                    expiry_date_snapshot=expiry,
                    reminder_type=f"{tier}d",
                    notification_key=key,
                    scheduled_at=scheduled,
                    max_attempts=s.notification_max_attempts,
                    status=NotificationStatus.SCHEDULED,
                )
            )
            created += 1
    if created:
        db.flush()

    # Stale SCHEDULED rows: expiry snapshot no longer matches the live
    # timeline (or the timeline/mapping is gone) -> CANCELLED, not reused.
    stale = 0
    pending = db.scalars(
        select(Notification).where(
            Notification.category == NotificationCategory.WARRANTY_EXPIRING,
            Notification.status == NotificationStatus.SCHEDULED,
        )
    ).all()
    for n in pending:
        if n.notification_key in live_keys:
            continue
        n.status = NotificationStatus.CANCELLED
        stale += 1
    if stale or created:
        db.commit()
    return {"created": created, "cancelled_stale": stale, "timelines_scanned": len(timelines)}


def _purchase_of_timeline(db: Session, timeline: WarrantyTimeline):
    from app.models.purchase import PurchaseItem

    item = db.get(PurchaseItem, timeline.purchase_item_id)
    if item is None:
        return None
    return db.get(Purchase, item.purchase_id)


def _retry_delay(attempts: int) -> timedelta:
    """Exponential backoff: 5m, 10m, 20m, ... (base configurable)."""
    s = get_settings()
    return timedelta(minutes=s.notification_retry_base_minutes * (2 ** (attempts - 1)))


def send_due_notifications(db: Session, now: datetime | None = None) -> dict:
    """Deliver SCHEDULED notifications whose time has come (idempotent).

    Channels: email (if enabled) + push (if enabled and configured).
    Failures retry with exponential backoff up to max_attempts, then the
    row is FAILED with last_error. A retry re-sends only the channels
    that have not delivered yet.
    """
    now = now or _utcnow()
    s = get_settings()
    email_provider = get_email_provider()
    push_provider = get_push_provider()
    sent = 0
    failed = 0
    suppressed = 0
    retrying = 0
    sent_ids: list[tuple[int, int, int | None, str | None]] = []
    due = db.scalars(
        select(Notification).where(
            Notification.status == NotificationStatus.SCHEDULED,
            Notification.scheduled_at.is_not(None),
            Notification.scheduled_at <= now,
            or_(Notification.next_retry_at.is_(None), Notification.next_retry_at <= now),
        )
    ).all()
    for n in due:
        prefs = get_preferences(db, n.user_id)
        channels: list[str] = []
        if prefs.email_enabled and email_provider.is_configured():
            channels.append("email")
        if prefs.push_enabled and push_provider.is_configured():
            channels.append("push")

        if not channels:
            # User disabled every channel (or nothing is configured):
            # terminal, honest, NOT a failure.
            n.status = NotificationStatus.SUPPRESSED
            n.last_error = "all notification channels disabled or unconfigured"
            suppressed += 1
            db.flush()
            continue

        n.attempts += 1  # count every dispatch attempt (success or failure)
        errors: list[str] = []
        if "email" in channels and n.email_sent_at is None:
            owner = db.get(User, n.user_id)
            link = (
                f"{s.frontend_base_url}/app/purchases/{n.purchase_id}" if n.purchase_id else ""
            )
            ok = email_provider.send(
                db,
                EmailMessage(
                    to=owner.email if owner else "unknown@example.invalid",
                    subject=f"[{s.app_name}] {n.title}",
                    body_text=(
                        f"{n.body}\n\n"
                        f"WarrantyVault AI — {s.app_name}\n"
                        f"View: {link}\n\n"
                        "This is an automated reminder. Coverage shown is manufacturer "
                        "warranty information, not a statement of your legal rights."
                    ),
                    payload={
                        "type": "warranty_expiring",
                        "notification_id": n.id,
                        "purchase_id": n.purchase_id,
                    },
                ),
            )
            if ok:
                n.email_sent_at = now
            else:
                errors.append("email provider returned failure")
        if "push" in channels and n.push_sent_at is None:
            ok = push_provider.send(
                db,
                PushMessage(
                    user_id=n.user_id,
                    title=n.title,
                    body_text=n.body or "",
                    payload={
                        "type": "warranty_expiring",
                        "notification_id": n.id,
                        "purchase_id": n.purchase_id,
                        "reminder": n.reminder_type,
                        "expiry": n.expiry_date_snapshot.isoformat() if n.expiry_date_snapshot else None,
                    },
                ),
            )
            if ok:
                n.push_sent_at = now
            else:
                errors.append("push provider returned failure")

        if not errors:
            n.status = NotificationStatus.SENT
            n.sent_at = now
            n.next_retry_at = None
            n.last_error = None
            sent_ids.append((n.id, n.user_id, n.purchase_id, n.reminder_type))
            sent += 1
        else:
            n.last_error = "; ".join(errors)
            if n.attempts >= n.max_attempts:
                n.status = NotificationStatus.FAILED
                failed += 1
                logger.error(
                    "notification_failed notification_id=%s attempts=%s error=%s",
                    n.id,
                    n.attempts,
                    n.last_error,
                )
            else:
                n.next_retry_at = now + _retry_delay(n.attempts)
                retrying += 1
                logger.warning(
                    "notification_retry notification_id=%s attempt=%s next_retry_at=%s error=%s",
                    n.id,
                    n.attempts,
                    n.next_retry_at.isoformat(),
                    n.last_error,
                )
        db.flush()
    if sent or failed or suppressed or retrying:
        db.commit()
    for nid, _uid, pid, rem in sent_ids:
        audit_helper.audit(
            db,
            action=AuditAction.NOTIFICATION_SENT,
            resource_type="notification",
            resource_id=nid,
            actor_id=None,
            metadata={"purchase_id": pid, "reminder": rem},
        )
    return {"sent": sent, "failed": failed, "suppressed": suppressed, "retrying": retrying}


def notification_sweep(db: Session, now: datetime | None = None) -> dict:
    """One worker sweep: schedule new reminders, cancel stale, send due."""
    sched = schedule_warranty_reminders(db, now=now)
    send = send_due_notifications(db, now=now)
    return {**sched, **send}
