"""Warranty expiry alert tests (SRS FR-15, TSD §22/§32).

Covers the configurable reminder tiers (default 60/30/7 days), idempotent
scheduling via the §32 key, stale-row cancellation when an expiry changes,
delivery through the log email provider (outbox), and the notifications API.

Tier semantics: a tier's reminder moment is ``expiry - tier`` days. Only
moments still in the future are scheduled (no backfill of passed moments);
once the expiry enters the smallest window, that reminder is created
immediately due.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.email_outbox import EmailOutbox
from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.product import Manufacturer, Product
from app.models.purchase import Purchase
from app.models.warranty import (
    WarrantyCoverage,
    WarrantyMapping,
    WarrantyPolicy,
    WarrantySource,
    WarrantyTimeline,
)
from app.services.notification_service import (
    schedule_warranty_reminders,
    send_due_notifications,
)
from tests.api.test_warranty import _create_purchase, _seed
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(Notification).delete()
    db_session.query(EmailOutbox).delete()
    db_session.query(WarrantyTimeline).delete()
    db_session.query(WarrantyMapping).delete()
    db_session.query(WarrantyCoverage).delete()
    db_session.query(WarrantyPolicy).delete()
    db_session.query(WarrantySource).delete()
    db_session.query(Purchase).delete()
    db_session.query(Product).delete()
    db_session.query(Manufacturer).delete()
    db_session.commit()


def _notifications(db, purchase_id: int | None = None) -> list[Notification]:
    q = select(Notification)
    if purchase_id is not None:
        q = q.where(Notification.purchase_id == purchase_id)
    return list(db.scalars(q).all())


def _make_purchase(app_client, token, product, days_out: int, name: str = "QLED TV"):
    """A purchase whose FULL_UNIT (12 mo) expiry lands `days_out` days out."""
    expiry_wanted = date.today() + timedelta(days=days_out)
    purchase_date = expiry_wanted.replace(year=expiry_wanted.year - 1).isoformat()
    p = _create_purchase(
        app_client,
        token,
        {
            "purchase_date": purchase_date,
            "warranty_region": "IN",
            "items": [{"product_id": product.id, "product_name": name}],
        },
    )
    return p, expiry_wanted


def test_tier_scheduling_idempotent_and_delivered(app_client, authed_user, db_session):
    s = _seed(db_session)
    token = authed_user["access_token"]
    # TV product has a single FULL_UNIT coverage -> clean tier assertions.
    # 20 days out: 30d/60d moments already passed -> only the 7d reminder is
    # scheduled (it fires when 7 days remain, ~13 days from now).
    p_a, expiry_a = _make_purchase(app_client, token, s["tv"], 20)
    # 5 days out: already inside the final window -> 7d reminder due now.
    p_b, expiry_b = _make_purchase(app_client, token, s["tv"], 5)

    # create_purchase already ran the best-effort scheduler.
    rows = _notifications(db_session)
    assert len(rows) == 2
    by_purchase = {n.purchase_id: n for n in rows}
    n_a, n_b = by_purchase[p_a["id"]], by_purchase[p_b["id"]]
    for n in (n_a, n_b):
        assert n.category == NotificationCategory.WARRANTY_EXPIRING
        assert n.reminder_type == "7d"
        assert n.status == NotificationStatus.SCHEDULED
    assert n_a.expiry_date_snapshot == expiry_a
    assert n_a.notification_key.endswith(f"{expiry_a.isoformat()}:7d")

    assert n_a.scheduled_at > datetime.now(UTC)  # ~13 days in the future
    assert n_b.scheduled_at <= datetime.now(UTC)  # immediately due

    # Idempotency: a second sweep (worker path) must not duplicate.
    result = schedule_warranty_reminders(db_session)
    assert result["created"] == 0
    assert len(_notifications(db_session)) == 2

    # Delivery: only the due one (n_b) is sent.
    sent = send_due_notifications(db_session)
    assert sent["sent"] == 1 and sent["failed"] == 0
    db_session.expire_all()
    n_b = db_session.get(Notification, n_b.id)
    n_a = db_session.get(Notification, n_a.id)
    assert n_b.status == NotificationStatus.SENT
    assert n_b.sent_at is not None
    assert n_a.status == NotificationStatus.SCHEDULED  # still in the future
    outbox = db_session.scalars(
        select(EmailOutbox).where(EmailOutbox.payload["notification_id"].as_integer() == n_b.id)
    ).all()
    assert len(outbox) == 1
    assert outbox[0].delivery == "LOG"
    assert authed_user["email"] in outbox[0].to_address

    # Visible through the API (1 unread = the sent one).
    r = app_client.get("/api/v1/notifications", headers=auth_headers(authed_user))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["unread_count"] == 1
    assert len(data["items"]) == 2
    titles = {i["title"] for i in data["items"]}
    assert any(t.startswith("Warranty expiring in 7 days") for t in titles)


def test_tier_boundaries(app_client, authed_user, db_session):
    s = _seed(db_session)
    token = authed_user["access_token"]
    # 50 days out: 30d + 7d moments are future; 60d moment passed -> skipped.
    p_a, _ = _make_purchase(app_client, token, s["tv"], 50)
    # 5 days out: inside the final window -> immediate 7d only.
    p_b, _ = _make_purchase(app_client, token, s["tv"], 5)
    tiers_a = sorted(n.reminder_type for n in _notifications(db_session, p_a["id"]))
    tiers_b = sorted(n.reminder_type for n in _notifications(db_session, p_b["id"]))
    assert tiers_a == ["30d", "7d"]
    assert tiers_b == ["7d"]


def test_stale_scheduled_cancelled_when_expiry_moves(app_client, authed_user, db_session):
    s = _seed(db_session)
    token = authed_user["access_token"]
    p, first_expiry = _make_purchase(app_client, token, s["tv"], 20)
    stale = _notifications(db_session, p["id"])[0]
    assert stale.reminder_type == "7d" and stale.status == NotificationStatus.SCHEDULED

    # Re-sync changed the coverage window (e.g. policy update): expiry moved
    # beyond every tier -> the old SCHEDULED row is cancelled, not reused.
    item_id = p["items"][0]["id"]
    t = db_session.scalar(
        select(WarrantyTimeline).where(
            WarrantyTimeline.purchase_item_id == item_id, WarrantyTimeline.component == "FULL_UNIT"
        )
    )
    assert t is not None
    t.expiry_date = date.today() + timedelta(days=120)
    db_session.commit()

    result = schedule_warranty_reminders(db_session)
    assert result["cancelled_stale"] == 1
    db_session.expire_all()
    stale = db_session.get(Notification, stale.id)
    assert stale.status == NotificationStatus.CANCELLED

    # A second move INTO the 7d window: distinct expiry snapshot -> fresh
    # SCHEDULED row under a new §32 key (the stale one is never reused).
    t = db_session.scalar(
        select(WarrantyTimeline).where(
            WarrantyTimeline.purchase_item_id == item_id, WarrantyTimeline.component == "FULL_UNIT"
        )
    )
    assert t is not None
    new_expiry = date.today() + timedelta(days=5)
    t.expiry_date = new_expiry
    db_session.commit()
    result = schedule_warranty_reminders(db_session)
    assert result["created"] == 1
    fresh = [n for n in _notifications(db_session, p["id"]) if n.expiry_date_snapshot == new_expiry]
    assert len(fresh) == 1 and fresh[0].status == NotificationStatus.SCHEDULED
    assert fresh[0].id != stale.id


def test_expired_coverage_no_reminder_active_coverage_scheduled(app_client, authed_user, db_session):
    s = _seed(db_session)
    # ~14 months ago: FULL_UNIT (12 mo) already expired; COMPRESSOR (60 mo)
    # is still active -> reminders scheduled for its future boundaries only.
    p = _create_purchase(
        app_client,
        authed_user["access_token"],
        {
            "purchase_date": (date.today() - timedelta(days=420)).isoformat(),
            "warranty_region": "IN",
            "items": [{"product_id": s["ac"].id, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    item_id = p["items"][0]["id"]
    timelines = {
        t.component: t
        for t in db_session.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_id)).all()
    }
    assert timelines["FULL_UNIT"].expiry_date < date.today()  # expired
    rows = _notifications(db_session, p["id"])
    # No reminder may reference the expired FULL_UNIT window.
    assert all(n.expiry_date_snapshot != timelines["FULL_UNIT"].expiry_date for n in rows)
    # The active COMPRESSOR window gets one reminder per future boundary.
    comp = [n for n in rows if n.expiry_date_snapshot == timelines["COMPRESSOR"].expiry_date]
    assert sorted(n.reminder_type for n in comp) == ["30d", "60d", "7d"]
    assert all(n.status == NotificationStatus.SCHEDULED for n in comp)
