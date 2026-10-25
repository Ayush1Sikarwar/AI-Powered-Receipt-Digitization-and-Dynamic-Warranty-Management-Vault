"""Unit tests: background job handlers (TSD §31).

Covers the two handlers that are actually wired:

- DOCUMENT_PROCESSING → runs the document pipeline
- CLEANUP → hard-purge via the data-retention engine

The six other JobType values (OCR_PROCESSING, PRODUCT_RESOLUTION,
WARRANTY_RESOLUTION, NOTIFICATION_SCHEDULING, CLAIM_PACKAGE_GENERATION,
SOURCE_REFRESH) are intentionally NOT enqueued by the application — see
the handlers.py module docstring for the rationale. No placeholder
handlers are added for them.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.auth_token import RevokedAccessToken
from app.models.document import Document, DocumentStatus
from app.models.job import BackgroundJob, JobStatus, JobType
from app.models.user import User, UserStatus
from app.services import data_retention
from app.services.auth_service import purge_expired_revocations
from app.workers import handlers
from app.workers.jobs import (
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    recover_stale_jobs,
    utcnow,
)
from tests.api.test_documents import upload
from tests.conftest import _register, auth_headers


def _doc_id_from_upload(app_client, tokens, filename: str) -> int:
    resp = upload(app_client, tokens, filename=filename)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]["id"]


def _find_docproc_job(db_session, document_id: int) -> BackgroundJob | None:
    """Find the DOCUMENT_PROCESSING job auto-enqueued by the upload endpoint."""
    from app.core.config import get_settings

    key = f"docproc:{document_id}:v{get_settings().pipeline_version}"
    return db_session.scalar(
        select(BackgroundJob).where(BackgroundJob.idempotency_key == key)
    )


def _clear_jobs(db_session):
    """Clear all background jobs — avoids cross-test leakage in shared DB."""
    db_session.query(BackgroundJob).delete()
    # Also clear revoked access tokens so purge tests don't leak rows
    # between tests (shared session-scoped test database).
    db_session.query(RevokedAccessToken).delete()
    db_session.commit()


def test_handler_registration_covers_enqueued_types():
    """HANDLERS maps every job type the application actually enqueues."""
    assert JobType.DOCUMENT_PROCESSING in handlers.HANDLERS
    assert JobType.CLEANUP in handlers.HANDLERS
    assert len(handlers.HANDLERS) == 2

    # Types intentionally not registered are not enqueued anywhere.
    unregistered = {
        JobType.OCR_PROCESSING,
        JobType.PRODUCT_RESOLUTION,
        JobType.WARRANTY_RESOLUTION,
        JobType.NOTIFICATION_SCHEDULING,
        JobType.CLAIM_PACKAGE_GENERATION,
        JobType.SOURCE_REFRESH,
    }
    assert unregistered.isdisjoint(set(handlers.HANDLERS.keys()))


def test_document_processing_handler_success(
    app_client, registered_user, db_session
):
    """DOCUMENT_PROCESSING handler runs the pipeline and completes the job."""
    doc_id = _doc_id_from_upload(app_client, registered_user, "handler-ok.png")

    # The upload endpoint auto-enqueues a DOCUMENT_PROCESSING job.
    job = _find_docproc_job(db_session, doc_id)
    assert job is not None, "upload endpoint must enqueue a processing job"
    assert job.status == JobStatus.QUEUED

    handlers._handle_document_processing(db_session, job)

    db_session.expire_all()
    doc = db_session.get(Document, doc_id)
    assert doc is not None
    # Pipeline ran synchronously: document is COMPLETED (or REVIEW_REQUIRED),
    # not FAILED — the handler successfully ran the full pipeline.
    assert doc.status != DocumentStatus.FAILED
    assert doc.status in (DocumentStatus.COMPLETED, DocumentStatus.REVIEW_REQUIRED)


def test_document_processing_handler_missing_document(db_session):
    """Handler raises ValueError for a nonexistent document."""
    _clear_jobs(db_session)
    job = BackgroundJob(
        type=JobType.DOCUMENT_PROCESSING,
        status=JobStatus.QUEUED,
        resource_type="document",
        resource_id="999999",
        payload={},
        idempotency_key=f"docproc:999999:missing-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(ValueError, match="document 999999 not found"):
        handlers._handle_document_processing(db_session, job)


def test_document_processing_handler_pipeline_failure(
    app_client, registered_user, db_session
):
    """Pipeline failure propagates to the worker's fail_job path."""
    doc_id = _doc_id_from_upload(app_client, registered_user, "handler-fail.png")

    job = _find_docproc_job(db_session, doc_id)
    assert job is not None

    with patch(
        "app.ai.pipeline.run_document_pipeline",
        side_effect=RuntimeError("pipeline exploded"),
    ), pytest.raises(RuntimeError, match="pipeline exploded"):
        handlers._handle_document_processing(db_session, job)


def test_document_processing_handler_invalid_resource_id(db_session):
    """Non-integer resource_id raises ValueError."""
    _clear_jobs(db_session)
    job = BackgroundJob(
        type=JobType.DOCUMENT_PROCESSING,
        status=JobStatus.QUEUED,
        resource_type="document",
        resource_id="not-a-number",
        payload={},
        idempotency_key=f"docproc:bad:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(ValueError):
        handlers._handle_document_processing(db_session, job)


def test_cleanup_handler_document_purge_success(
    app_client, registered_user, db_session
):
    """CLEANUP handler purges a document and its derived data."""
    doc_id = _doc_id_from_upload(app_client, registered_user, "purge-ok.png")

    job = BackgroundJob(
        type=JobType.CLEANUP,
        status=JobStatus.QUEUED,
        resource_type="document",
        resource_id=str(doc_id),
        payload={},
        idempotency_key=f"doc_purge:{doc_id}:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()

    handlers._handle_cleanup(db_session, job)

    db_session.expire_all()
    assert db_session.get(Document, doc_id) is None


def test_cleanup_handler_document_already_purged_is_noop(
    app_client, registered_user, db_session
):
    """Purging an already-deleted document is idempotent (no-op)."""
    doc_id = _doc_id_from_upload(app_client, registered_user, "purge-twice.png")

    # Purge once directly.
    data_retention.purge_document(db_session, document_id=doc_id)
    assert db_session.get(Document, doc_id) is None

    # Purge again via the handler — must not raise.
    job = BackgroundJob(
        type=JobType.CLEANUP,
        status=JobStatus.QUEUED,
        resource_type="document",
        resource_id=str(doc_id),
        payload={},
        idempotency_key=f"doc_purge_2:{doc_id}:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()
    handlers._handle_cleanup(db_session, job)
    db_session.expire_all()
    assert db_session.get(Document, doc_id) is None


def test_cleanup_handler_user_purge_success(app_client, db_session):
    """CLEANUP handler purges all personal data for a closed account."""
    tokens = _register(app_client, f"purge-{uuid.uuid4().hex[:8]}@example.com")
    user_id = tokens["user"]["id"]

    # Upload a document + create a purchase so there is data to purge.
    _doc_id_from_upload(app_client, tokens, "purge-user.png")
    app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(tokens),
        json={"purchase_date": "2026-08-01", "total": 10.0},
    )

    # Close the account first (purge handler requires closed status).
    app_client.post("/api/v1/account/close", headers=auth_headers(tokens))
    user = db_session.get(User, user_id)
    assert user.status == UserStatus.CLOSED

    job = BackgroundJob(
        type=JobType.CLEANUP,
        status=JobStatus.QUEUED,
        resource_type="user",
        resource_id=str(user_id),
        payload={},
        idempotency_key=f"user_purge:{user_id}:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()
    handlers._handle_cleanup(db_session, job)

    db_session.expire_all()
    assert db_session.get(User, user_id) is None


def test_cleanup_handler_retention_hold_requeues(
    app_client, registered_user, db_session
):
    """Open claim → handler re-queues the CLEANUP job (not a failure)."""
    headers = auth_headers(registered_user)
    doc_id = _doc_id_from_upload(app_client, registered_user, "hold-purge.png")
    pur = app_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "document_id": doc_id,
            "purchase_date": "2026-08-01",
            "total": 10.0,
            "items": [{"product_name": "X", "quantity": 1}],
        },
    )
    pur_id = pur.json()["data"]["purchase"]["id"]
    claim = app_client.post(
        "/api/v1/claims",
        headers=headers,
        json={"purchase_id": pur_id, "issue_description": "Open claim"},
    )
    assert claim.status_code == 201

    job = BackgroundJob(
        type=JobType.CLEANUP,
        status=JobStatus.QUEUED,
        resource_type="document",
        resource_id=str(doc_id),
        payload={},
        idempotency_key=f"doc_purge_hold:{doc_id}:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()
    handlers._handle_cleanup(db_session, job)

    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    # Re-queued with +24h backoff, attempt count not burned.
    assert job.status == JobStatus.QUEUED
    assert job.scheduled_at is not None
    assert job.attempt_count == 0
    assert "retention_hold" in (job.error or "")
    # Document still present.
    assert db_session.get(Document, doc_id) is not None


def test_cleanup_handler_unknown_resource_type(db_session):
    """Unknown resource_type raises ValueError."""
    _clear_jobs(db_session)
    job = BackgroundJob(
        type=JobType.CLEANUP,
        status=JobStatus.QUEUED,
        resource_type="bogus",
        resource_id="1",
        payload={},
        idempotency_key=f"cleanup_bogus:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(ValueError, match="unknown cleanup resource_type"):
        handlers._handle_cleanup(db_session, job)


def test_complete_and_fail_job_transitions(db_session):
    """complete_job / fail_job manage job lifecycle correctly."""
    _clear_jobs(db_session)
    key = f"complete-fail:{uuid.uuid4().hex[:8]}"
    job = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=key,
    )

    # complete_job sets COMPLETED.
    complete_job(db_session, job)
    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.COMPLETED
    assert job.completed_at is not None
    assert job.error is None

    # Re-enqueue for fail path with max_attempts=1 → DEAD on first failure.
    job2 = enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="document",
        resource_id="2",
        idempotency_key=f"cleanup_fail:{uuid.uuid4().hex[:8]}",
        max_attempts=1,
    )
    # Mark as RUNNING (as claim_next_job would) then fail.
    job2.status = JobStatus.RUNNING
    job2.attempt_count = 1
    db_session.commit()
    fail_job(db_session, job2, error="boom", error_code="PROCESSING_ERROR")
    db_session.expire_all()
    job2 = db_session.get(BackgroundJob, job2.id)
    assert job2.status == JobStatus.DEAD
    assert job2.error == "boom"
    assert job2.error_code == "PROCESSING_ERROR"


def test_enqueue_job_idempotency(db_session):
    """Same idempotency key returns existing non-terminal job."""
    _clear_jobs(db_session)
    key = f"docproc_idem:{uuid.uuid4().hex[:8]}"
    j1 = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=key,
    )
    j2 = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=key,
    )
    assert j1.id == j2.id  # same job returned


def test_claim_next_job_claims_one_job(db_session):
    """claim_next_job claims one job at a time (SKIP LOCKED semantics)."""
    _clear_jobs(db_session)
    key1 = f"skip1:{uuid.uuid4().hex[:8]}"
    key2 = f"skip2:{uuid.uuid4().hex[:8]}"
    j1 = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=key1,
    )
    j2 = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="2",
        idempotency_key=key2,
    )

    claimed1 = claim_next_job(db_session, [JobType.DOCUMENT_PROCESSING])
    claimed2 = claim_next_job(db_session, [JobType.DOCUMENT_PROCESSING])
    assert claimed1 is not None
    assert claimed2 is not None
    assert claimed1.id != claimed2.id
    assert {claimed1.id, claimed2.id} == {j1.id, j2.id}

    # No more queued DOCUMENT_PROCESSING jobs.
    claimed3 = claim_next_job(db_session, [JobType.DOCUMENT_PROCESSING])
    assert claimed3 is None


def test_fail_job_retries_then_dead(db_session):
    """fail_job re-queues while attempts remain, then marks DEAD."""
    _clear_jobs(db_session)
    job = enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="document",
        resource_id="1",
        idempotency_key=f"retry_dead:{uuid.uuid4().hex[:8]}",
        max_attempts=2,
    )
    # Simulate claim_next_job: mark RUNNING + increment attempt_count.
    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    db_session.commit()

    # Attempt 1 → re-queued to QUEUED with backoff (attempt_count 1 < max_attempts 2).
    fail_job(db_session, job, error="transient", error_code="PROCESSING_ERROR")
    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.QUEUED
    assert job.attempt_count == 1
    # Backoff is set: not immediately claimable until scheduled_at passes.
    assert job.scheduled_at is not None
    assert job.scheduled_at > utcnow()

    # Expire the backoff for the test.
    job.scheduled_at = utcnow() - timedelta(minutes=1)
    db_session.commit()

    # Attempt 2 → claim again, then DEAD (attempt_count 2 >= max_attempts 2).
    claimed = claim_next_job(db_session, [JobType.CLEANUP])
    assert claimed.id == job.id
    fail_job(db_session, claimed, error="still failing", error_code="PROCESSING_ERROR")
    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.DEAD
    assert job.error == "still failing"


def _make_document(db_session, user_id: int, status: DocumentStatus = DocumentStatus.QUEUED) -> int:
    from app.models.document import Document, DocumentType

    document = Document(
        user_id=user_id,
        document_type=DocumentType.RECEIPT,
        original_filename="stale-test.png",
        mime_type="image/png",
        file_size=100,
        storage_key="test/storage/stale-test.png",
        sha256_hash="a" * 64,
        page_count=1,
        status=status,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document.id


def _make_user(db_session, email: str) -> int:
    from argon2 import PasswordHasher

    from app.core.config import get_settings
    from app.models.user import User, UserStatus

    s = get_settings()
    ph = PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost,
        parallelism=s.argon2_parallelism,
    )
    user = User(
        email=email,
        password_hash=ph.hash("test-password"),
        display_name="Stale Test User",
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user.id


def test_recover_stale_jobs_resets_stale_running_to_queued(db_session):
    """A RUNNING job older than the threshold is re-queued to QUEUED."""
    _clear_jobs(db_session)
    key = f"stale_recover:{uuid.uuid4().hex[:8]}"
    job = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=key,
    )
    # Simulate a worker that claimed the job long ago and crashed.
    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    job.started_at = utcnow() - timedelta(minutes=15)  # older than 10-min threshold
    db_session.commit()

    recovered = recover_stale_jobs(db_session)
    assert recovered == 1

    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.QUEUED
    assert job.started_at is None
    assert job.scheduled_at is not None
    assert job.scheduled_at <= utcnow()  # immediately eligible
    assert job.error_code == "STALE_JOB_RECOVERED"
    # attempt_count NOT burned by recovery (crash is not a processing failure).
    assert job.attempt_count == 1


def test_recover_stale_jobs_keeps_recent_running(db_session):
    """A recently claimed RUNNING job is NOT recovered (worker may still be alive)."""
    _clear_jobs(db_session)
    key = f"recent_running:{uuid.uuid4().hex[:8]}"
    job = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=key,
    )
    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    job.started_at = utcnow() - timedelta(minutes=1)  # within threshold
    db_session.commit()

    recovered = recover_stale_jobs(db_session)
    assert recovered == 0

    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.RUNNING


def test_recover_stale_jobs_skips_non_running(db_session):
    """QUEUED, COMPLETED, DEAD jobs are not touched by recovery."""
    _clear_jobs(db_session)
    queued = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="1",
        idempotency_key=f"skip_queued:{uuid.uuid4().hex[:8]}",
    )
    completed = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="2",
        idempotency_key=f"skip_completed:{uuid.uuid4().hex[:8]}",
    )
    complete_job(db_session, completed)
    dead = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id="3",
        idempotency_key=f"skip_dead:{uuid.uuid4().hex[:8]}",
        max_attempts=1,
    )
    dead.status = JobStatus.RUNNING
    dead.attempt_count = 1
    db_session.commit()
    fail_job(db_session, dead, error="boom", error_code="PROCESSING_ERROR")

    recovered = recover_stale_jobs(db_session)
    assert recovered == 0

    for jid, expected in (
        (queued.id, JobStatus.QUEUED),
        (completed.id, JobStatus.COMPLETED),
        (dead.id, JobStatus.DEAD),
    ):
        db_session.expire_all()
        j = db_session.get(BackgroundJob, jid)
        assert j.status == expected


def test_fail_job_retry_backoff_sets_scheduled_at(db_session):
    """fail_job sets scheduled_at with exponential backoff when re-queuing."""
    _clear_jobs(db_session)
    job = enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="document",
        resource_id="1",
        idempotency_key=f"backoff:{uuid.uuid4().hex[:8]}",
        max_attempts=3,
    )
    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    db_session.commit()

    before = utcnow()
    fail_job(db_session, job, error="transient", error_code="PROCESSING_ERROR")

    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.QUEUED
    assert job.scheduled_at is not None
    # Backoff = 2^0 * 2min = 2min → scheduled_at should be ~2 minutes ahead.
    delta = job.scheduled_at - before
    assert timedelta(minutes=1) <= delta <= timedelta(minutes=3)


def test_fail_job_dead_marks_document_failed(db_session, registered_user):
    """When a DOCUMENT_PROCESSing job goes DEAD, its document is marked FAILED."""
    _clear_jobs(db_session)
    user_id = registered_user["user"]["id"]
    doc_id = _make_document(db_session, user_id, DocumentStatus.QUEUED)

    key = f"doc_dead:{uuid.uuid4().hex[:8]}"
    job = enqueue_job(
        db_session,
        type=JobType.DOCUMENT_PROCESSING,
        resource_type="document",
        resource_id=str(doc_id),
        idempotency_key=key,
        max_attempts=1,
    )
    # Simulate claim + failed attempt (max_attempts=1 → DEAD on first failure).
    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    db_session.commit()

    fail_job(db_session, job, error="pipeline exploded", error_code="PROCESSING_ERROR")

    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.DEAD

    from app.models.document import Document

    db_session.expire_all()
    doc = db_session.get(Document, doc_id)
    assert doc.status == DocumentStatus.FAILED
    assert doc.error_code == "PROCESSING_ERROR"
    assert "pipeline exploded" in (doc.error_message or "")


def test_fail_job_dead_non_document_job_no_document_change(
    db_session, registered_user
):
    """A non-DOCUMENT_PROCESSING job going DEAD does not touch documents."""
    _clear_jobs(db_session)
    user_id = registered_user["user"]["id"]
    doc_id = _make_document(db_session, user_id, DocumentStatus.QUEUED)

    key = f"non_doc_dead:{uuid.uuid4().hex[:8]}"
    job = enqueue_job(
        db_session,
        type=JobType.CLEANUP,
        resource_type="document",
        resource_id=str(doc_id),
        idempotency_key=key,
        max_attempts=1,
    )
    job.status = JobStatus.RUNNING
    job.attempt_count = 1
    db_session.commit()

    fail_job(db_session, job, error="cleanup failed", error_code="PROCESSING_ERROR")

    db_session.expire_all()
    job = db_session.get(BackgroundJob, job.id)
    assert job.status == JobStatus.DEAD

    from app.models.document import Document

    db_session.expire_all()
    doc = db_session.get(Document, doc_id)
    # Document status unchanged (CLEANUP job going DEAD doesn't fail the document).
    assert doc.status == DocumentStatus.QUEUED


def test_purge_expired_revocations_removes_expired_only(db_session):
    """Expired RevokedAccessToken rows are purged; unexpired ones remain."""
    _clear_jobs(db_session)
    user_id = 101

    # Expired revocation (past expires_at).
    expired = RevokedAccessToken(
        jti="expired-jti-101",
        user_id=user_id,
        expires_at=utcnow() - timedelta(minutes=5),
    )
    # Unexpired revocation (future expires_at).
    unexpired = RevokedAccessToken(
        jti="unexpired-jti-101",
        user_id=user_id,
        expires_at=utcnow() + timedelta(minutes=30),
    )
    db_session.add_all([expired, unexpired])
    db_session.commit()

    purged = purge_expired_revocations(db_session)
    assert purged == 1

    db_session.expire_all()
    remaining = db_session.scalars(select(RevokedAccessToken)).all()
    assert len(remaining) == 1
    assert remaining[0].jti == "unexpired-jti-101"


def test_purge_expired_revocations_none_expired(db_session):
    """When nothing is expired, purge returns 0 and removes nothing."""
    _clear_jobs(db_session)
    user_id = 102

    unexpired = RevokedAccessToken(
        jti="unexpired-jti-102",
        user_id=user_id,
        expires_at=utcnow() + timedelta(hours=1),
    )
    db_session.add(unexpired)
    db_session.commit()

    purged = purge_expired_revocations(db_session)
    assert purged == 0

    db_session.expire_all()
    remaining = db_session.scalars(select(RevokedAccessToken)).all()
    assert len(remaining) == 1
    assert remaining[0].jti == "unexpired-jti-102"


def test_purge_expired_revocations_empty_table(db_session):
    """Purging an empty revocation table returns 0 without error."""
    _clear_jobs(db_session)
    purged = purge_expired_revocations(db_session)
    assert purged == 0


def test_purge_expired_revocations_called_by_worker_is_protected(db_session):
    """The worker wraps purge_expired_revocations in try/except so a DB error
    never crashes the worker or interrupts unrelated maintenance.

    Mirrors the existing pattern used for retention_sweep and
    recover_stale_jobs in app/workers/run.py: each maintenance task is
    wrapped in its own try/except that logs and continues.
    """
    import inspect

    from app.workers import run as run_module

    source = inspect.getsource(run_module.main)
    assert "purge_expired_revocations" in source
    # The call must be inside a try block that catches Exception.
    purge_section = source[source.index("purge_expired_revocations"):]
    assert "except Exception" in purge_section[:500], (
        "worker must wrap purge_expired_revocations in try/except"
    )