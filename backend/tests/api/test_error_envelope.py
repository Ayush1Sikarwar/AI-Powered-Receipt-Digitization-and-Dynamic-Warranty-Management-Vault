"""Error-handling audit (PRD Phase 6): every error response uses the
standard envelope {error_code, message, details, request_id} — no raw
tracebacks, consistent codes per failure class, request-id correlation.
"""

import uuid

from tests.conftest import auth_headers


def test_404_uses_envelope_with_request_id(app_client, authed_user):
    r = app_client.get(f"/api/v1/purchases/{99999999}", headers=auth_headers(authed_user))
    assert r.status_code == 404
    body = r.json()
    assert body["error_code"] == "NOT_FOUND"
    assert body["request_id"]
    assert "Traceback" not in r.text


def test_422_uses_validation_envelope(app_client, authed_user):
    r = app_client.post(
        "/api/v1/purchases",
        headers=auth_headers(authed_user),
        json={"purchase_date": "not-a-date", "items": []},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["request_id"]
    assert "Traceback" not in r.text


def test_403_uses_authorization_envelope(app_client, authed_user):
    r = app_client.get("/api/v1/admin/observability", headers=auth_headers(authed_user))
    assert r.status_code == 403
    body = r.json()
    assert body["error_code"] == "AUTHORIZATION_ERROR"
    assert body["request_id"]


def test_401_missing_token_envelope(app_client):
    r = app_client.get("/api/v1/purchases")
    assert r.status_code == 401
    body = r.json()
    assert body["error_code"] == "AUTHENTICATION_ERROR"
    assert body["request_id"]


def test_429_rate_limit_envelope(app_client, registered_user):
    """Login is rate-limited (10/min): hammer it until limited, then verify
    the 429 response still carries the standard envelope."""
    from app.core.rate_limit import rate_limiter

    rate_limiter.reset()
    email = f"rl-{uuid.uuid4().hex[:10]}@example.com"
    app_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Passw0rd!123", "display_name": "RL", "country": "IN"},
    )
    last = None
    for _ in range(14):
        last = app_client.post("/api/v1/auth/login", json={"email": email, "password": "Passw0rd!123"})
        if last.status_code == 429:
            break
    assert last is not None and last.status_code == 429
    body = last.json()
    assert body["error_code"] == "RATE_LIMITED"
    assert body["request_id"]
    assert "Traceback" not in last.text
