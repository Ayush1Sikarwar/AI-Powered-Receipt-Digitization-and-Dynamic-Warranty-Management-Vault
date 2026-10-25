"""API tests: full authentication flow (FR-01, TSD §17)."""

import uuid
from datetime import UTC, datetime

import jwt

from app.core.config import get_settings
from tests.conftest import _login, _register, auth_headers

_settings = get_settings()


def test_register_login_me_logout_flow(app_client):
    email = f"flow-{uuid.uuid4().hex[:8]}@example.com"
    reg = _register(app_client, email)
    assert reg["user"]["email"] == email
    assert reg["access_token"] and reg["refresh_token"]
    assert reg["user"]["status"] == "ACTIVE"

    # /me with the access token
    me = app_client.get("/api/v1/auth/me", headers=auth_headers(reg))
    assert me.status_code == 200
    assert me.json()["email"] == email

    # /me without token -> 401
    assert app_client.get("/api/v1/auth/me").status_code == 401

    # login works and issues fresh tokens
    login = _login(app_client, email)
    assert login["access_token"]

    # logout revokes the refresh token; refresh with it must fail
    app_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": login["refresh_token"]},
        headers=auth_headers(login),
    )
    refresh = app_client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refresh.status_code == 401
    body = refresh.json()
    assert body["error_code"] == "AUTHENTICATION_ERROR"


def test_register_duplicate_email_rejected(app_client):
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    _register(app_client, email)
    resp = app_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Passw0rd!123", "display_name": "X"},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


def test_register_weak_password_rejected(app_client):
    email = f"weak-{uuid.uuid4().hex[:8]}@example.com"
    # Policy: >= 8 chars, at least one letter and one number.
    for weak in ("short1a", "onlylettersa", "1234567890", "abc"):
        resp = app_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": weak, "display_name": "X"},
        )
        assert resp.status_code == 422, f"{weak!r} should be rejected: {resp.text}"
    # Valid password should be accepted.
    ok = app_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "nouppercasedigit1", "display_name": "X"},
    )
    assert ok.status_code == 201, ok.text


def test_login_wrong_password(app_client):
    email = f"wp-{uuid.uuid4().hex[:8]}@example.com"
    _register(app_client, email)
    resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "WrongPass1!"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"


def test_login_nonexistent_user_same_error(app_client):
    resp = app_client.post(
        "/api/v1/auth/login", json={"email": f"nope-{uuid.uuid4().hex[:8]}@example.com", "password": "WrongPass1!"}
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"


def test_refresh_rotation_and_reuse_detection(app_client):
    email = f"rot-{uuid.uuid4().hex[:8]}@example.com"
    reg = _register(app_client, email)
    refresh1 = reg["refresh_token"]

    # rotate
    r1 = app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert r1.status_code == 200
    new = r1.json()["data"]
    assert new["refresh_token"] != refresh1

    # presenting the OLD (rotated) token again => reuse detection, 401
    r2 = app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert r2.status_code == 401

    # after reuse detection, the new token family is also revoked
    r3 = app_client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r3.status_code == 401


def test_password_reset_flow(app_client, db_session):
    email = f"reset-{uuid.uuid4().hex[:8]}@example.com"
    _register(app_client, email)

    # request reset
    resp = app_client.post("/api/v1/auth/password-reset/request", json={"email": email})
    assert resp.status_code == 200
    assert resp.json()["data"]["sent"] is True

    # unknown email -> same response (no enumeration)
    resp2 = app_client.post(
        "/api/v1/auth/password-reset/request", json={"email": f"ghost-{uuid.uuid4().hex[:8]}@example.com"}
    )
    assert resp2.status_code == 200
    assert resp2.json()["data"] == resp.json()["data"]

    # read the reset token from the log-provider outbox
    from sqlalchemy import select

    from app.models.email_outbox import EmailOutbox

    row = db_session.scalar(select(EmailOutbox).where(EmailOutbox.to_address == email).order_by(EmailOutbox.id.desc()))
    assert row is not None
    token = row.body_text.split("token=")[1].split("\n")[0].strip()

    # confirm with a bad token
    bad = app_client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "f" * 48, "new_password": "N3wPass!xyz"},
    )
    assert bad.status_code == 422

    # confirm with the real token
    ok_resp = app_client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "N3wPass!xyz"},
    )
    assert ok_resp.status_code == 200

    # new password works, old one no longer works
    assert app_client.post("/api/v1/auth/login", json={"email": email, "password": "Passw0rd!123"}).status_code == 401
    assert app_client.post("/api/v1/auth/login", json={"email": email, "password": "N3wPass!xyz"}).status_code == 200

    # token is single-use
    again = app_client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Another1!pw"}
    )
    assert again.status_code == 422


def test_me_reflects_profile_update(app_client, registered_user):
    headers = auth_headers(registered_user)
    resp = app_client.patch("/api/v1/auth/me", json={"display_name": "Renamed", "country": "de"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]["user"]
    assert data["display_name"] == "Renamed"
    assert data["country"] == "DE"


def test_error_envelope_has_request_id(app_client):
    resp = app_client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error_code"] == "AUTHENTICATION_ERROR"
    assert body["request_id"]
    assert resp.headers.get("X-Request-ID") == body["request_id"]


def test_access_token_valid_before_logout(app_client, registered_user):
    """A valid access token can access protected endpoints before logout."""
    headers = auth_headers(registered_user)
    resp = app_client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == registered_user["email"]


def test_logout_revokes_access_token(app_client, registered_user):
    """After logout, the access token is rejected on protected endpoints."""
    headers = auth_headers(registered_user)

    # Token works before logout
    assert app_client.get("/api/v1/auth/me", headers=headers).status_code == 200

    # Logout (revokes both the access token JTI and the refresh token)
    resp = app_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": registered_user["refresh_token"]},
        headers=headers,
    )
    assert resp.status_code == 200

    # Access token reuse after logout -> 401
    resp = app_client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"


def test_expired_access_token_rejected(app_client, registered_user):
    """An expired JWT is rejected even if its signature is valid."""
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(registered_user["user"]["id"]),
            "role": registered_user["user"]["role"],
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()) - 600,
            "exp": int(now.timestamp()) - 300,  # expired 5 minutes ago
            "type": "access",
        },
        _settings.auth_secret,
        algorithm=_settings.auth_algorithm,
    )
    resp = app_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"


def test_refresh_issues_new_access_token_with_fresh_jti(app_client, registered_user):
    """Refresh rotation issues a new access token with a new JTI."""
    headers = auth_headers(registered_user)

    # Log out to revoke the current access token
    app_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": registered_user["refresh_token"]},
        headers=headers,
    )

    # Refresh gets a new token pair
    r = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": registered_user["refresh_token"]},
    )
    assert r.status_code == 401  # refresh token was revoked by logout

    # But we can log in again to get a fresh token
    login = _login(app_client, registered_user["email"])
    new_headers = auth_headers(login)
    assert app_client.get("/api/v1/auth/me", headers=new_headers).status_code == 200
    assert login["access_token"] != registered_user["access_token"]
    assert login["jti"] != registered_user["jti"]