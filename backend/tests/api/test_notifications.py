"""Notification API tests (SRS FR-15)."""

import uuid

from app.models.notification import Notification, NotificationCategory, NotificationStatus
from tests.conftest import auth_headers


def _add_notification(db_session, user_id, title, status=NotificationStatus.SENT):
    n = Notification(
        user_id=user_id,
        category=NotificationCategory.SYSTEM,
        title=title,
        status=status,
        notification_key=f"ntf-{uuid.uuid4().hex}",
    )
    db_session.add(n)
    db_session.commit()
    db_session.refresh(n)
    return n


def test_list_and_unread_count(app_client, registered_user, db_session):
    user_id = registered_user["user"]["id"]
    a = _add_notification(db_session, user_id, "Read me")
    _add_notification(db_session, user_id, "Scheduled one", NotificationStatus.SCHEDULED)
    h = auth_headers(registered_user)

    data = app_client.get("/api/v1/notifications", headers=h).json()["data"]
    titles = [n["title"] for n in data["items"]]
    assert set(titles) == {"Read me", "Scheduled one"}
    assert data["unread_count"] == 1  # only SENT counts as unread

    unread = app_client.get("/api/v1/notifications", headers=h, params={"unread": "true"}).json()["data"]
    assert [n["id"] for n in unread["items"]] == [a.id]


def test_mark_read_and_read_all(app_client, registered_user, db_session):
    user_id = registered_user["user"]["id"]
    a = _add_notification(db_session, user_id, "One")
    b = _add_notification(db_session, user_id, "Two")
    scheduled = _add_notification(db_session, user_id, "Not sent yet", NotificationStatus.SCHEDULED)
    h = auth_headers(registered_user)

    resp = app_client.patch(f"/api/v1/notifications/{a.id}/read", headers=h)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "READ"
    assert resp.json()["data"]["read_at"] is not None
    db_session.refresh(a)
    assert a.status == NotificationStatus.READ

    resp = app_client.post("/api/v1/notifications/read-all", headers=h)
    assert resp.json()["data"]["marked_read"] == 1  # only 'Two' was still SENT
    db_session.refresh(b)
    db_session.refresh(scheduled)
    assert b.status == NotificationStatus.READ
    assert scheduled.status == NotificationStatus.SCHEDULED  # untouched

    data = app_client.get("/api/v1/notifications", headers=h).json()["data"]
    assert data["unread_count"] == 0
