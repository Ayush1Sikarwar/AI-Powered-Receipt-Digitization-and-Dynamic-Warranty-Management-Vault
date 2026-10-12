"""Unit tests: password hashing and token lifecycle (TSD §41)."""


from datetime import UTC

from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("S3curePass!x")
    assert verify_password("S3curePass!x", h)
    assert not verify_password("wrong", h)


def test_password_hash_not_plaintext():
    h = hash_password("S3curePass!x")
    assert "S3curePass!x" not in h
    assert h.startswith("$argon2")


def test_access_token_roundtrip_and_role():
    token, jti = create_access_token(42, "CONSUMER")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "CONSUMER"
    assert payload["jti"] == jti
    assert payload["type"] == "access"


def test_access_token_expired_rejected():
    from datetime import datetime, timedelta

    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.exceptions import AuthenticationError

    s = get_settings()
    now = datetime.now(UTC)
    token = pyjwt.encode(
        {
            "sub": "1",
            "role": "CONSUMER",
            "jti": "x",
            "iat": int(now.timestamp()),
            "exp": int((now - timedelta(minutes=1)).timestamp()),
            "type": "access",
        },
        s.auth_secret,
        algorithm=s.auth_algorithm,
    )
    try:
        decode_access_token(token)
        raise AssertionError("expired token accepted")
    except AuthenticationError:
        pass


def test_access_token_wrong_secret_rejected():
    from datetime import datetime, timedelta

    import jwt as pyjwt

    from app.core.exceptions import AuthenticationError

    now = datetime.now(UTC)
    token = pyjwt.encode(
        {"sub": "1", "role": "CONSUMER", "jti": "x",
         "iat": int(now.timestamp()),
         "exp": int((now + timedelta(minutes=5)).timestamp()),
         "type": "access"},
        "a-different-secret",
        algorithm="HS256",
    )
    try:
        decode_access_token(token)
        raise AssertionError("forged token accepted")
    except AuthenticationError:
        pass


def test_refresh_tokens_are_unique_and_hashable():
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b
    assert hash_token(a) != hash_token(b)
    assert hash_token(a) == hash_token(a)  # deterministic
