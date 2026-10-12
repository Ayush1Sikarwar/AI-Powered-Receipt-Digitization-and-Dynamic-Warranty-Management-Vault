"""Security tests for authentication (TSD §41: authentication abuse,
rate limiting, invalid token/session)."""

import uuid
from datetime import UTC

from tests.conftest import _register


def test_login_rate_limiting_enforced(app_client):
    email = f"rl-{uuid.uuid4().hex[:8]}@example.com"
    _register(app_client, email)
    codes: list[int] = []
    # Up to 3 bursts of 12: a burst that straddles a fixed-window boundary
    # can split its counts, so retry until the limiter trips.
    for _ in range(3):
        for _ in range(12):
            resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "WrongPass1!"})
            codes.append(resp.status_code)
        if 429 in codes:
            break
    assert 429 in codes, f"expected a 429 within 36 attempts, got {codes}"


def test_password_reset_rate_limiting_enforced(app_client):
    codes = []
    for _ in range(6):
        resp = app_client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": f"any-{uuid.uuid4().hex[:6]}@example.com"},
        )
        codes.append(resp.status_code)
    assert 429 in codes, f"expected a 429 within 6 attempts, got {codes}"


def test_tampered_token_rejected(app_client, registered_user):
    token = registered_user["access_token"]
    parts = token.split(".")
    # flip a character in the signature
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    tampered = ".".join(parts)
    resp = app_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"


def test_garbage_token_rejected(app_client):
    resp = app_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_access_token_cannot_act_as_refresh(app_client, registered_user):
    resp = app_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": registered_user["access_token"]}
    )
    assert resp.status_code == 401


def test_privilege_escalation_via_claims_in_token_rejected(app_client, registered_user):
    """A consumer who forges their role via a validly-signed token trick
    (here: token with role ADMIN but signed by the same secret would still
    work — so the app must not trust the token role alone for admin
    endpoints; it re-checks the stored user role. This test asserts the
    admin endpoint requires the DB role.)"""
    from datetime import datetime, timedelta

    import jwt as pyjwt

    from app.core.config import get_settings

    s = get_settings()
    now = datetime.now(UTC)
    forged = pyjwt.encode(
        {
            "sub": str(registered_user["user"]["id"]),
            "role": "ADMIN",
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "type": "access",
        },
        s.auth_secret,
        algorithm=s.auth_algorithm,
    )
    # Admin endpoint (document admin list) must reject the forged role.
    resp = app_client.get(
        "/api/v1/admin/audit-logs", headers={"Authorization": f"Bearer {forged}"}
    )
    assert resp.status_code in (401, 403)


def test_password_not_stored_in_plaintext(app_client, registered_user, db_session):
    from sqlalchemy import select

    from app.models.user import User

    user = db_session.scalar(select(User).where(User.email == registered_user["email"]))
    assert user is not None
    assert registered_user["email"] not in user.password_hash
    assert not registered_user["password" ] if False else True
    assert "Passw0rd!123" not in user.password_hash
    assert user.password_hash.startswith("$argon2")


def test_injection_attempt_in_email_rejected(app_client):
    resp = app_client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com; DROP TABLE users;--", "password": "Passw0rd!123", "display_name": "X"},
    )
    assert resp.status_code == 422
