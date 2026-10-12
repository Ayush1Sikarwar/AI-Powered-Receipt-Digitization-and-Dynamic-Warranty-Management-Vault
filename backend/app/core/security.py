"""Security primitives: password hashing, JWT access tokens, refresh token ids.

- Passwords: Argon2id (modern memory-hard hash, SRS Security).
- Access tokens: short-lived signed JWT (stateless verification + expiry).
- Refresh tokens: opaque random ids stored hashed (SHA-256) in the database,
  revocable per-token and per-user (logout revokes, SRS FR-01).
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_settings = get_settings()

ph = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_cost,
    parallelism=_settings.argon2_parallelism,
)


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return ph.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_access_token(user_id: int, role: str, jti: str | None = None) -> tuple[str, str]:
    """Returns (token, jti). jti enables revocation of individual access tokens."""
    jti = jti or uuid.uuid4().hex
    now = _utcnow()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_settings.access_token_ttl_minutes)).timestamp()),
        "type": "access",
    }
    token = jwt.encode(payload, _settings.auth_secret, algorithm=_settings.auth_algorithm)
    return token, jti


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises AuthenticationError on any invalid/expired token."""
    from app.core.exceptions import AuthenticationError

    try:
        payload = jwt.decode(
            token,
            _settings.auth_secret,
            algorithms=[_settings.auth_algorithm],
            options={"require": ["exp", "sub", "jti"]},
        )
        if payload.get("type") != "access":
            raise AuthenticationError("Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token expired") from None
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token") from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Refresh tokens are stored hashed at rest (never plaintext)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token_id() -> str:
    return uuid.uuid4().hex
