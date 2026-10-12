"""Notification system tests (SRS FR-15, TSD §22/§32).

Covers the full delivery pipeline:
  - 60/30/7-day reminder scheduling
  - timezone-aware delivery time (user's timezone)
  - duplicate prevention (idempotent key)
  - changed expiry date -> stale CANCELLED, new reminder created
  - worker retry with exponential backoff
  - provider failure -> terminal FAILED after max attempts
  - disabled notifications -> SUPPRESSED (not a failure)
  - optional push channel + channel-level idempotency on retry
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.email_outbox import EmailOutbox
from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.notification_preferences import NotificationPreferences
from app.models.push_outbox import PushOutbox
from app.services import notification_service
from app.services.notification_service import (
    schedule_warranty_reminders,
    send_due_notifications,
)
from tests.api.test_warranty import _create_purchase, _seed
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(PushOutbox).delete()
    db_session.query(EmailOutbox).delete()
    db_session.query(NotificationPreferences).delete()
    db_session.query(Notification).delete()
    from app.models.product import Manufacturer, Product
    from app.models.purchase import Purchase
    from app.models.warranty import (
        WarrantyCoverage,
        WarrantyMapping,
        WarrantyPolicy,
        WarrantySource,
        WarrantyTimeline,
    )

    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(Purchase).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _expiry_in(app_client, token, product, days_out: int) -> tuple[dict, date]:
    """A purchase whose FULL_UNIT (12 mo) expiry lands `days_out` days out."""
    expiry_wanted = date.today() + timedelta(days=days_out)
    purchase_date = expiry_wanted.replace(year=expiry_wanted.year - 1).isoformat()
    p = _create_purchase(
        app_client,
        token,
        {
            "purchase_date": purchase_date,
            "warranty_region": "IN",
            "items": [{"product_id": product.id, "product_name": "QLED TV"}],
        },
    )
    return p, expiry_wanted


def _rows(db, purchase_id: int | None = None) -> list[Notification]:
    q = select(Notification)
    if purchase_id is not None:
        q = q.where(Notification.purchase_id == purchase_id)
    return list(db.scalars(q).all())


def _register_with_tz(app_client, tz: str) -> str:
    import uuid

    email = f"tz-{uuid.uuid4().hex[:8]}@example.com"
    r = app_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Passw0rd!123",
            "display_name": "TZ User",
            "country": "IN",
            "timezone": tz,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["access_token"]


# --- 1-3. Reminder tiers --------------------------------------------------------


def test_60_30_7_day_reminders_all_scheduled(app_client, authed_user, db_session):
    s = _seed(db_session)
    p, expiry = _expiry_in(app_client, authed_user["access_token"], s["tv"], 100)
    rows = _rows(db_session, p["id"])
    assert sorted(n.reminder_type for n in rows) == ["30d", "60d", "7d"]
    assert all(n.status == NotificationStatus.SCHEDULED for n in rows)
    assert all(n.category == NotificationCategory.WARRANTY_EXPIRING for n in rows)
    assert all(n.expiry_date_snapshot == expiry for n in rows)
    # UTC user: each reminder is scheduled at 09:00 UTC on its due date.
    by_tier = {n.reminder_type: n for n in rows}
    for tier in ("60d", "30d", "7d"):
        due = expiry - timedelta(days=int(tier[:-1]))
        assert by_tier[tier].scheduled_at == datetime.combine(due, datetime.min.time(), tzinfo=UTC) + timedelta(hours=9)
    # The key embeds purchase identity + coverage/extended identity +
    # expiry snapshot + tier (TSD §32).
    key = by_tier["60d"].notification_key
    assert key.startswith(f"purchase-{p['id']}:")
    assert ("coverage-" in key) or ("extended-" in key)
    assert key.endswith(f":{expiry.isoformat()}:60d")


def test_7_day_reminder_immediately_due_inside_final_window(app_client, authed_user, db_session):
    s = _seed(db_session)
    p, expiry = _expiry_in(app_client, authed_user["access_token"], s["tv"], 5)
    rows = _rows(db_session, p["id"])
    assert [n.reminder_type for n in rows] == ["7d"]
    assert rows[0].status == NotificationStatus.SCHEDULED
    assert rows[0].scheduled_at <= datetime.now(UTC)  # due now
    # Delivery through the (log) email provider marks it SENT + outbox row.
    result = send_due_notifications(db_session)
    assert result["sent"] == 1 and result["failed"] == 0
    db_session.expire_all()
    assert rows[0].status == NotificationStatus.SENT
    assert rows[0].email_sent_at is not None
    outbox = db_session.scalars(select(EmailOutbox)).all()
    assert len(outbox) == 1 and outbox[0].delivery == "LOG"


# --- 6. Timezone handling --------------------------------------------------------


def test_timezone_handling_positive_offset(app_client, db_session):
    """Asia/Kolkata (UTC+5:30, no DST): 09:00 local == 03:30 UTC."""
    s = _seed(db_session)
    token = _register_with_tz(app_client, "Asia/Kolkata")
    p, expiry = _expiry_in(app_client, token, s["tv"], 100)
    rows = _rows(db_session, p["id"])
    assert len(rows) == 3
    for n in rows:
        tier_days = int(n.reminder_type[:-1])
        due = expiry - timedelta(days=tier_days)
        expected = (
            datetime.combine(due, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata")) + timedelta(hours=9)
        ).astimezone(UTC)
        assert n.scheduled_at == expected, f"{n.reminder_type}: {n.scheduled_at} != {expected}"
        # 09:00 IST is always 03:30 UTC.
        assert n.scheduled_at.hour == 3 and n.scheduled_at.minute == 30


def test_timezone_handling_negative_offset(app_client, db_session):
    """A user west of UTC: local 09:00 converts to a later UTC instant, and
    the conversion must use the due date's DST offset (zoneinfo, not a
    fixed offset)."""
    s = _seed(db_session)
    token = _register_with_tz(app_client, "America/New_York")
    # 20 days out -> only the 7d tier is scheduled (due in 13 days).
    p, expiry = _expiry_in(app_client, token, s["tv"], 20)
    rows = _rows(db_session, p["id"])
    assert [n.reminder_type for n in rows] == ["7d"]
    due = expiry - timedelta(days=7)
    expected = (
        datetime.combine(due, datetime.min.time(), tzinfo=ZoneInfo("America/New_York")) + timedelta(hours=9)
    ).astimezone(UTC)
    assert rows[0].scheduled_at == expected
    # NY local 09:00 is 13:00 or 14:00 UTC depending on DST — never 09:00 UTC.
    assert rows[0].scheduled_at.hour in (13, 14)


def test_invalid_timezone_falls_back_to_utc(app_client, db_session):
    s = _seed(db_session)
    token = _register_with_tz(app_client, "Bogus/Zone")
    p, expiry = _expiry_in(app_client, token, s["tv"], 20)
    rows = _rows(db_session, p["id"])
    assert len(rows) == 1
    due = expiry - timedelta(days=7)
    assert rows[0].scheduled_at == datetime.combine(due, datetime.min.time(), tzinfo=UTC) + timedelta(hours=9)


# --- 4. Duplicate prevention -----------------------------------------------------


def test_duplicate_prevention_across_repeated_sweeps(app_client, authed_user, db_session):
    s = _seed(db_session)
    p, _ = _expiry_in(app_client, authed_user["access_token"], s["tv"], 100)
    first = schedule_warranty_reminders(db_session)
    assert first["created"] == 0  # created at purchase time already
    n1 = len(_rows(db_session, p["id"]))
    second = schedule_warranty_reminders(db_session)
    third = schedule_warranty_reminders(db_session)
    assert second["created"] == 0 and third["created"] == 0
    assert len(_rows(db_session, p["id"])) == n1
    # Keys remain unique.
    keys = [n.notification_key for n in _rows(db_session, p["id"])]
    assert len(keys) == len(set(keys))


# --- 5. Changed expiry date ------------------------------------------------------


def test_changed_expiry_via_user_correction_creates_new_reminder(app_client, authed_user, db_session):
    s = _seed(db_session)
    p, first_expiry = _expiry_in(app_client, authed_user["access_token"], s["tv"], 20)
    old = _rows(db_session, p["id"])[0]
    assert old.reminder_type == "7d" and old.status == NotificationStatus.SCHEDULED
    old_key = old.notification_key

    # User correction: PATCH the purchase date ~7 days earlier -> the FULL_UNIT
    # expiry moves 7 days earlier. The scheduler must create a NEW reminder
    # (distinct key via the new expiry snapshot), not suppress it.
    new_purchase_date = (date.fromisoformat(p["purchase_date"]) - timedelta(days=7)).isoformat()
    r = app_client.patch(
        f"/api/v1/purchases/{p['id']}",
        headers=auth_headers(authed_user),
        json={"purchase_date": new_purchase_date},
    )
    assert r.status_code == 200, r.text
    # (The purchase update already re-synced & re-scheduled; an explicit
    # sweep is idempotent.)
    schedule_warranty_reminders(db_session)
    db_session.expire_all()

    old = db_session.get(Notification, old.id)
    assert old.status == NotificationStatus.CANCELLED

    new = [
        n
        for n in _rows(db_session, p["id"])
        if n.status == NotificationStatus.SCHEDULED
    ]
    assert len(new) == 1
    assert new[0].id != old.id
    assert new[0].expiry_date_snapshot == first_expiry - timedelta(days=7)
    assert new[0].notification_key != old_key
    assert new[0].notification_key.endswith(f":{new[0].expiry_date_snapshot.isoformat()}:7d")

    # Delivering the new reminder works; the cancelled one is never sent.
    # (Move the new one into the final window so it is immediately due.)
    from app.models.warranty import WarrantyTimeline

    t = db_session.scalar(
        select(WarrantyTimeline).where(
            WarrantyTimeline.purchase_item_id == p["items"][0]["id"],
            WarrantyTimeline.component == "FULL_UNIT",
        )
    )
    t.expiry_date = date.today() + timedelta(days=5)
    db_session.commit()
    schedule_warranty_reminders(db_session)
    result = send_due_notifications(db_session)
    assert result["sent"] >= 1
    sent_ids = {o.payload["notification_id"] for o in db_session.scalars(select(EmailOutbox)).all()}
    assert old.id not in sent_ids


# --- 7-8. Worker retry & provider failure ---------------------------------------


def _make_provider(flaky: list[bool] | None = None, always_ok: bool = True):
    """Test email provider. `flaky` is a consumed list of per-call results."""

    class _Stub:
        calls = 0

        def is_configured(self) -> bool:
            return True

        def send(self, db, message) -> bool:
            _Stub.calls += 1
            if flaky is not None:
                return flaky.pop(0)
            return always_ok

    return _Stub()


def test_worker_retry_with_backoff_then_success(app_client, authed_user, db_session, monkeypatch):
    s = _seed(db_session)
    p, _ = _expiry_in(app_client, authed_user["access_token"], s["tv"], 5)  # due now
    stub = _make_provider(flaky=[False, False, True])
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: stub)

    t0 = datetime.now(UTC)
    r1 = send_due_notifications(db_session, now=t0)
    assert r1["retrying"] == 1 and r1["sent"] == 0
    n = _rows(db_session, p["id"])[0]
    assert n.status == NotificationStatus.SCHEDULED
    assert n.attempts == 1
    assert n.last_error is not None
    # Backoff: first retry 5 minutes out.
    assert n.next_retry_at == t0 + timedelta(minutes=5)
    # Not due before the retry moment.
    r_early = send_due_notifications(db_session, now=t0 + timedelta(minutes=4))
    assert r_early["retrying"] == 0 and r_early["sent"] == 0
    assert _rows(db_session, p["id"])[0].attempts == 1

    # Retry #2 fails -> second backoff (10 minutes from t0+5m).
    r2 = send_due_notifications(db_session, now=t0 + timedelta(minutes=5, seconds=1))
    assert r2["retrying"] == 1
    n = db_session.get(Notification, n.id)
    assert n.attempts == 2
    assert n.next_retry_at == t0 + timedelta(minutes=5, seconds=1) + timedelta(minutes=10)

    # Retry #3 succeeds -> SENT after exactly 3 provider attempts.
    r3 = send_due_notifications(db_session, now=t0 + timedelta(minutes=15, seconds=2))
    assert r3["sent"] == 1 and r3["failed"] == 0
    n = db_session.get(Notification, n.id)
    assert n.status == NotificationStatus.SENT
    assert n.attempts == 3
    assert n.sent_at is not None
    assert stub.calls == 3  # 2 failures + 1 success, no extra attempts


def test_provider_failure_terminal_after_max_attempts(app_client, authed_user, db_session, monkeypatch):
    s = _seed(db_session)
    p, _ = _expiry_in(app_client, authed_user["access_token"], s["tv"], 5)
    stub = _make_provider(always_ok=False)
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: stub)

    t0 = datetime.now(UTC)
    r1 = send_due_notifications(db_session, now=t0)
    assert r1["retrying"] == 1
    r2 = send_due_notifications(db_session, now=t0 + timedelta(minutes=6))
    assert r2["retrying"] == 1
    r3 = send_due_notifications(db_session, now=t0 + timedelta(minutes=17))
    assert r3["failed"] == 1
    n = _rows(db_session, p["id"])[0]
    assert n.status == NotificationStatus.FAILED
    assert n.attempts == 3
    assert n.last_error is not None
    assert n.sent_at is None
    # Nothing was ever delivered.
    assert db_session.scalars(select(EmailOutbox)).all() == []


# --- 9. Disabled notifications ---------------------------------------------------


def test_disabled_notifications_suppressed(app_client, authed_user, db_session, monkeypatch):
    s = _seed(db_session)
    p, _ = _expiry_in(app_client, authed_user["access_token"], s["tv"], 5)  # due now

    # Disable every channel through the preferences API.
    r = app_client.put(
        "/api/v1/notifications/preferences",
        headers=auth_headers(authed_user),
        json={"email_enabled": False, "push_enabled": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["email_enabled"] is False

    stub = _make_provider(always_ok=True)
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: stub)
    monkeypatch.setattr(notification_service, "get_push_provider", lambda: _make_provider(always_ok=True))

    result = send_due_notifications(db_session)
    assert result["suppressed"] == 1 and result["sent"] == 0
    n = _rows(db_session, p["id"])[0]
    assert n.status == NotificationStatus.SUPPRESSED
    assert n.attempts == 0
    assert n.sent_at is None
    # No provider was invoked at all.
    assert db_session.scalars(select(EmailOutbox)).all() == []
    assert db_session.scalars(select(PushOutbox)).all() == []


def test_preferences_api_roundtrip(app_client, authed_user, db_session):
    h = auth_headers(authed_user)
    r = app_client.get("/api/v1/notifications/preferences", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["email_enabled"] is True  # default
    assert data["push_enabled"] is False  # default (opt-in)
    assert data["timezone"]
    assert "email" in data["channels"] and "push" in data["channels"]

    r = app_client.put(
        "/api/v1/notifications/preferences", headers=h, json={"push_enabled": True}
    )
    assert r.status_code == 200
    assert r.json()["data"]["push_enabled"] is True
    assert r.json()["data"]["email_enabled"] is True  # untouched

    db_session.expire_all()
    row = db_session.scalar(
        select(NotificationPreferences).where(
            NotificationPreferences.user_id == authed_user["user"]["id"]
        )
    )
    assert row is not None
    assert row.email_enabled is True and row.push_enabled is True


# --- Push channel + channel-level idempotency ------------------------------------


def test_push_channel_and_channel_level_idempotency(app_client, authed_user, db_session, monkeypatch):
    s = _seed(db_session)
    h = auth_headers(authed_user)
    app_client.put("/api/v1/notifications/preferences", headers=h, json={"push_enabled": True})
    p, _ = _expiry_in(app_client, authed_user["access_token"], s["tv"], 5)  # due now

    email_ok = [False, True]  # email fails once, push succeeds first try
    email_stub = _make_provider(flaky=email_ok)
    push_stub = _make_provider(flaky=None)  # push always ok
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: email_stub)
    monkeypatch.setattr(notification_service, "get_push_provider", lambda: push_stub)

    t0 = datetime.now(UTC)
    r1 = send_due_notifications(db_session, now=t0)
    assert r1["retrying"] == 1  # email failed, push succeeded
    n = db_session.get(Notification, _rows(db_session, p["id"])[0].id)
    assert n.push_sent_at is not None  # push delivered
    assert n.email_sent_at is None  # email pending
    assert push_stub.calls == 1

    # Retry re-sends ONLY the email channel — push is never re-sent.
    r2 = send_due_notifications(db_session, now=t0 + timedelta(minutes=5, seconds=1))
    assert r2["sent"] == 1
    n = db_session.get(Notification, n.id)
    assert n.status == NotificationStatus.SENT
    assert n.email_sent_at is not None
    assert push_stub.calls == 1  # channel-level idempotency: no double push
    assert email_stub.calls == 2  # fail, then success


def test_push_not_configured_is_honest_status(app_client, authed_user):
    h = auth_headers(authed_user)
    r = app_client.get("/api/v1/system/email-status", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert "provider" in data  # email part (backward compatible)
    assert "push" in data  # honest push channel state
    assert data["push"]["configured"] is False  # default push_provider=none
