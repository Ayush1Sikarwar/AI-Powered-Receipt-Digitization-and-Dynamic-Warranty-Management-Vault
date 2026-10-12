"""SRS data controls: FR-19 export, FR-20 deletion, NFR-02 retention.

Verifies the retention engine performs REAL database + storage operations:
document purge, closed-account purge, hold re-queueing, and the periodic
retention sweep. No TODO endpoints — every assertion checks DB state.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.audit import AuditAction
from app.models.audit import AuditLog
from app.models.claim import Claim, ClaimStatus
from app.models.document import Document
from app.models.extraction import ExtractedField, ExtractionJob
from app.models.job import BackgroundJob, JobStatus, JobType
from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.purchase import Purchase, PurchaseItem
from app.models.user import User, UserStatus
from app.services import data_retention
from app.workers import handlers
from tests.api.test_documents import upload
from tests.conftest import _register, auth_headers


def _due(dt: datetime) -> datetime:
    """Normalize naive vs aware comparison (PG timestamptz returns aware)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def test_export_contains_own_data(app_client, registered_user, db_session):
    user_id = registered_user["user"]["id"]
    doc_id = upload(app_client, registered_user, filename="export-receipt.png").json()["data"]["document"]["id"]

    # Force extraction fields into the DB (pipeline runs async in prod; here
    # we insert rows directly to test export plumbing, not OCR).
    field = ExtractedField(
        document_id=doc_id,
        field_name="total_price",
        field_value="99.99",
        normalized_value="99.99",
        confidence=0.9,
        extraction_method="keyword+pattern",
        model_name="rule-based-schema-constrained",
        model_version="1.1.0",
    )
    db_session.add(field)
    db_session.commit()

    pur = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(registered_user),
        json={
            "document_id": doc_id,
            "purchase_date": "2026-08-01",
            "total": 99.99,
            "items": [{"product_name": "Exported Product", "quantity": 1, "unit_price": 99.99}],
        },
    )
    assert pur.status_code == 201, pur.text

    claim = app_client.post(
        "/api/v1/claims",
        headers=auth_headers(registered_user),
        json={"purchase_id": pur.json()["data"]["purchase"]["id"], "issue_description": "Export test claim"},
    )
    assert claim.status_code == 201, claim.text

    note = Notification(
        user_id=user_id,
        category=NotificationCategory.SYSTEM,
        title="Export me",
        status=NotificationStatus.SENT,
        notification_key=f"exp-{uuid.uuid4().hex}",
    )
    db_session.add(note)
    db_session.commit()

    data = app_client.get("/api/v1/account/export", headers=auth_headers(registered_user)).json()["data"]

    assert data["user"]["email"] == registered_user["email"]
    doc_out = next(d for d in data["documents"] if d["id"] == doc_id)
    assert doc_out["original_filename"] == "export-receipt.png"
    assert any(f["field_name"] == "total_price" for f in doc_out["fields"])
    pur_out = data["purchases"][0]
    assert pur_out["items"][0]["product_name"] == "Exported Product"
    assert data["claims"][0]["issue_description"] == "Export test claim"
    assert any(n["title"] == "Export me" for n in data["notifications"])
    actions = {a["action"] for a in data["audit_trail"]}
    assert AuditAction.DOCUMENT_UPLOADED in actions
    assert AuditAction.PURCHASE_CREATED in actions
    assert AuditAction.CLAIM_CREATED in actions


def _processed_doc_with_claim(app_client, tokens, db_session):
    """Upload a doc, create a purchase + non-terminal claim (real rows)."""
    doc_id = upload(app_client, tokens, filename="retention.png").json()["data"]["document"]["id"]
    pur = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(tokens),
        json={"document_id": doc_id, "purchase_date": "2026-08-01", "total": 10.0, "items": [{"product_name": "X", "quantity": 1}]},
    )
    pur_id = pur.json()["data"]["purchase"]["id"]
    claim = app_client.post(
        "/api/v1/claims",
        headers=auth_headers(tokens),
        json={"purchase_id": pur_id, "issue_description": "Open claim for hold test"},
    )
    assert claim.status_code == 201, claim.text
    return doc_id, pur_id, claim.json()["data"]["claim"]["id"]


def test_document_delete_schedules_deferred_purge(app_client, registered_user, db_session):
    doc_id, pur_id, claim_id = _processed_doc_with_claim(app_client, registered_user, db_session)

    resp = app_client.delete(f"/api/v1/documents/{doc_id}", headers=auth_headers(registered_user))
    assert resp.status_code == 200, resp.text
    # Open claim -> hold (document survives, purge must not run)
    assert resp.json()["data"]["status"] == "HOLD"

    # Close the claim -> terminal state, deletion may proceed
    claim_row = db_session.get(Claim, claim_id)
    claim_row.status = ClaimStatus.RESOLVED
    db_session.commit()

    resp = app_client.delete(f"/api/v1/documents/{doc_id}", headers=auth_headers(registered_user))
    assert resp.json()["data"]["status"] == "DELETED"

    job = db_session.scalar(
        select(BackgroundJob).where(BackgroundJob.idempotency_key == f"doc_purge:{doc_id}")
    )
    assert job is not None, "document deletion must schedule a CLEANUP job"
    assert job.type == JobType.CLEANUP
    assert job.resource_type == "document"
    assert job.scheduled_at is not None
    assert _due(job.scheduled_at) > datetime.now(UTC)  # deferred, not immediate


def test_purge_document_hard_deletes_rows_and_storage(app_client, registered_user, db_session):
    doc_id = upload(app_client, registered_user, filename="purge-me.png").json()["data"]["document"]["id"]
    doc = db_session.get(Document, doc_id)
    from app.services import storage as storage_service

    assert storage_service.get_storage().exists(doc.storage_key)

    data_retention.purge_document(db_session, document_id=doc_id)

    assert db_session.get(Document, doc_id) is None
    assert db_session.scalar(select(ExtractedField).where(ExtractedField.document_id == doc_id)) is None
    assert db_session.scalar(select(ExtractionJob).where(ExtractionJob.document_id == doc_id)) is None
    assert not storage_service.get_storage().exists(doc.storage_key)
    # Audit of the purge exists
    assert (
        db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_PURGED, AuditLog.resource_id == str(doc_id)
            )
        )
        is not None
    )


def test_purge_document_requeues_on_open_claim(app_client, registered_user, db_session):
    from app.workers.jobs import claim_next_job, enqueue_job

    doc_id, _pur_id, claim_id = _processed_doc_with_claim(app_client, registered_user, db_session)

    # 1) A direct purge attempt while the claim is open must be blocked.
    try:
        data_retention.purge_document(db_session, document_id=doc_id)
        pytest.fail("expected RetentionHold")
    except data_retention.RetentionHold as hold:
        assert hold.reason == "document_referenced_by_open_claim"
    assert db_session.get(Document, doc_id) is not None

    # 2) Simulate the deferred purge job the deletion hook would enqueue:
    # the handler must re-queue (not fail) while the hold is active.
    enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="document",
        resource_id=str(doc_id),
        idempotency_key=f"doc_purge:{doc_id}",
    )
    claimed = claim_next_job(db_session, [JobType.CLEANUP])
    assert claimed is not None and claimed.resource_id == str(doc_id)
    handlers._handle_cleanup(db_session, claimed)
    job = db_session.get(BackgroundJob, claimed.id)
    assert job.status == JobStatus.QUEUED
    assert job.scheduled_at is not None and _due(job.scheduled_at) > datetime.now(UTC)
    assert "retention_hold" in (job.error or "")
    # Document still present, attempt count not burned by the hold
    assert job.attempt_count == 0
    assert db_session.get(Document, doc_id) is not None

    # 3) Terminal claim -> next execution purges.
    claim_row = db_session.get(Claim, claim_id)
    claim_row.status = ClaimStatus.CLOSED
    db_session.commit()
    handlers._handle_cleanup(db_session, db_session.get(BackgroundJob, claimed.id))
    assert db_session.get(Document, doc_id) is None


def test_account_close_revokes_sessions_and_schedules_purge(app_client, db_session):
    tokens = _register(app_client, f"close-{uuid.uuid4().hex[:8]}@example.com")
    user_id = tokens["user"]["id"]

    resp = app_client.post("/api/v1/account/close", headers=auth_headers(tokens))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["purge_within_days"] == 90

    # Refresh no longer works (all sessions revoked)
    resp = app_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401

    # Login on a closed account is rejected
    resp = app_client.post(
        "/api/v1/auth/login", json={"email": tokens["user"]["email"], "password": "Passw0rd!123"}
    )
    assert resp.status_code == 401

    # Purge job scheduled ~90 days out
    job = db_session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == f"user_purge:{user_id}"))
    assert job is not None
    assert job.type == JobType.CLEANUP and job.resource_type == "user"
    assert _due(job.scheduled_at) > datetime.now(UTC) + timedelta(days=89)


def test_purge_user_data_cascades_and_keeps_audit(app_client, db_session):
    tokens = _register(app_client, f"purge-{uuid.uuid4().hex[:8]}@example.com")
    user_id = tokens["user"]["id"]
    h = auth_headers(tokens)

    upload(app_client, tokens, filename="purge-user.png")
    pur_resp = app_client.post(
        "/api/v1/purchases", headers=h, json={"purchase_date": "2026-08-01", "total": 5.0, "items": [{"product_name": "P", "quantity": 1}]}
    )
    pur_id = pur_resp.json()["data"]["purchase"]["id"]
    note = Notification(
        user_id=user_id,
        category=NotificationCategory.SYSTEM,
        title="gone soon",
        status=NotificationStatus.SENT,
        notification_key=f"purge-{uuid.uuid4().hex}",
    )
    db_session.add(note)
    db_session.commit()
    # An audit row exists for this user (LOGIN/REGISTER)
    assert db_session.scalar(select(AuditLog).where(AuditLog.actor_id == user_id)) is not None

    result = data_retention.purge_user_data(db_session, user_id=user_id)
    assert result["purged"] is True

    db_session.expire_all()
    assert db_session.get(User, user_id) is None
    assert db_session.scalar(select(Document).where(Document.user_id == user_id)) is None
    assert db_session.scalar(select(Purchase).where(Purchase.user_id == user_id)) is None
    assert db_session.scalar(select(PurchaseItem).where(PurchaseItem.purchase_id == pur_id)) is None
    assert db_session.scalar(select(Notification).where(Notification.user_id == user_id)) is None
    # Audit trail SURVIVES the purge (NFR-02: >= 1 year retention)
    assert (
        db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.ACCOUNT_PURGED, AuditLog.resource_id == str(user_id))
        )
        is not None
    )
    assert db_session.scalar(select(AuditLog).where(AuditLog.actor_id == user_id)) is not None


def test_purge_user_data_holds_on_open_claim(app_client, db_session):
    tokens = _register(app_client, f"hold-{uuid.uuid4().hex[:8]}@example.com")
    user_id = tokens["user"]["id"]
    _processed_doc_with_claim(app_client, tokens, db_session)

    try:
        data_retention.purge_user_data(db_session, user_id=user_id)
        pytest.fail("expected RetentionHold")
    except data_retention.RetentionHold as hold:
        assert hold.reason == "open_claims_exist"

    assert db_session.get(User, user_id) is not None
    assert db_session.scalar(select(Document).where(Document.user_id == user_id)) is not None


def test_retention_sweep_only_due_closed_accounts(app_client, db_session):
    due_tokens = _register(app_client, f"sweep-due-{uuid.uuid4().hex[:8]}@example.com")
    fresh_tokens = _register(app_client, f"sweep-fresh-{uuid.uuid4().hex[:8]}@example.com")

    def _close(email, days_ago):
        u = db_session.scalar(select(User).where(User.email == email))
        u.status = UserStatus.CLOSED
        u.closed_at = datetime.now(UTC) - timedelta(days=days_ago)
        db_session.commit()
        return u.id

    due_user = _close(due_tokens["user"]["email"], 95)
    fresh_user = _close(fresh_tokens["user"]["email"], 10)

    # Open claim blocks the due user (purchase must exist before closure:
    # closed accounts cannot call authenticated endpoints)
    blocked_tokens = _register(app_client, f"sweep-blocked-{uuid.uuid4().hex[:8]}@example.com")
    pur = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(blocked_tokens),
        json={"purchase_date": "2026-08-01", "total": 1.0, "items": [{"product_name": "B", "quantity": 1}]},
    )
    assert pur.status_code == 201, pur.text
    blocked_user = _close(blocked_tokens["user"]["email"], 95)
    db_session.add(
        Claim(user_id=blocked_user, purchase_id=pur.json()["data"]["purchase"]["id"], status=ClaimStatus.DRAFT)
    )
    db_session.commit()

    due = data_retention.retention_sweep(db_session)
    assert due == 1, f"expected exactly 1 due account, got {due}"
    assert db_session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == f"user_purge:{due_user}")) is not None
    assert db_session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == f"user_purge:{fresh_user}")) is None
    assert db_session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == f"user_purge:{blocked_user}")) is None

    # Sweep is idempotent
    assert data_retention.retention_sweep(db_session) == 0


def test_scheduled_jobs_not_claimed_before_due(app_client, db_session):
    tokens = _register(app_client, f"sched-{uuid.uuid4().hex[:8]}@example.com")
    from app.workers.jobs import claim_next_job, enqueue_job

    future_key = f"sched-test-{uuid.uuid4().hex}"
    due_key = f"sched-due-{uuid.uuid4().hex}"
    enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="user",
        resource_id=str(tokens["user"]["id"]),
        idempotency_key=future_key,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    # Drain any immediately-due CLEANUP jobs left by other tests (the test
    # suite has no worker to complete them), then verify the not-yet-due
    # job is never claimable.
    while claim_next_job(db_session, [JobType.CLEANUP]) is not None:
        pass
    assert db_session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == future_key)).status == JobStatus.QUEUED

    # A due job is claimable (drain strays again, then claim ours).
    enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="user",
        resource_id=str(tokens["user"]["id"]),
        idempotency_key=due_key,
    )
    claimed = None
    while True:
        claimed = claim_next_job(db_session, [JobType.CLEANUP])
        if claimed is None or claimed.idempotency_key == due_key:
            break
    assert claimed is not None and claimed.idempotency_key == due_key
    assert claimed.status == JobStatus.RUNNING
