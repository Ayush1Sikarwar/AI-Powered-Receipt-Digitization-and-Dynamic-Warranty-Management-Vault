"""Authentication business logic (FR-01, TSD §17, §36).

Kept separate from API route handlers (TSD §4: no business logic in routes).
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ValidationError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_token_id,
    verify_password,
)
from app.models.auth_token import (
    PasswordResetToken,
    RefreshToken,
    RefreshTokenStatus,
    RevokedAccessToken,
)
from app.models.user import User, UserStatus
from app.notifications.email import EmailMessage, get_email_provider

logger = logging.getLogger(__name__)
settings = get_settings()

# Precomputed Argon2id hash used to equalize response timing when the account
# does not exist (user-enumeration mitigation).
_DUMMY_HASH = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
).hash("warrantyvault-timing-equalizer")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


def _ua(request: Request | None) -> str | None:
    return (request.headers.get("user-agent") or "")[:255] if request else None


def register(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    country: str | None,
    timezone: str | None,
    locale: str | None,
    request: Request | None = None,
    request_id: str | None = None,
) -> User:
    email_norm = email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email_norm))
    if existing:
        raise ValidationError("An account with this email already exists")
    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
        country=country,
        timezone=timezone or "UTC",
        locale=locale or "en",
        status=UserStatus.ACTIVE,
        role="CONSUMER",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit_helper.audit(
        db,
        action=audit_helper.AuditAction.REGISTER,
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=_ip(request),
        metadata={"email_domain": email_norm.split("@")[-1] if "@" in email_norm else None},
    )
    logger.info("user_registered user_id=%s", user.id)
    return user


def _issue_tokens(db: Session, user: User, request: Request | None) -> dict:
    access, jti = create_access_token(user.id, user.role)
    refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            jti=new_token_id(),
            user_id=user.id,
            token_hash=hash_token(refresh),
            expires_at=_utcnow() + timedelta(days=settings.refresh_token_ttl_days),
            status=RefreshTokenStatus.ACTIVE,
            created_ip=_ip(request),
            user_agent=_ua(request),
        )
    )
    db.commit()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
        "jti": jti,
    }


def login(
    db: Session,
    *,
    email: str,
    password: str,
    request: Request | None = None,
    request_id: str | None = None,
) -> dict:
    email_norm = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email_norm))
    if user is None:
        # Constant-ish time: verify against a dummy hash to reduce user-enumeration via timing.
        verify_password(password, _DUMMY_HASH)
        raise AuthenticationError("Invalid email or password")
    if user.status == UserStatus.CLOSED:
        raise AuthenticationError("Account is closed")
    if not verify_password(password, user.password_hash):
        raise AuthenticationError("Invalid email or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _utcnow()
    db.commit()
    tokens = _issue_tokens(db, user, request)
    audit_helper.audit(
        db,
        action=audit_helper.AuditAction.LOGIN,
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=_ip(request),
        metadata={},
    )
    logger.info("user_login user_id=%s", user.id)
    return tokens


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def get_current_user(db: Session, access_token: str) -> User:
    payload = _decode(access_token)
    user = db.get(User, int(payload["sub"]))
    if user is None or user.status != UserStatus.ACTIVE:
        raise AuthenticationError("User not found or inactive")
    if _is_access_token_revoked(db, payload["jti"]):
        raise AuthenticationError("Token revoked")
    return user


def _decode(access_token: str) -> dict:
    from app.core.security import decode_access_token

    return decode_access_token(access_token)


def refresh(
    db: Session,
    *,
    refresh_token: str,
    request: Request | None = None,
    request_id: str | None = None,
) -> dict:
    """Rotating refresh: the presented token is revoked and a new pair issued.

    Token reuse after rotation is treated as revocation of the whole chain
    (basic stolen-token detection) — the user must log in again.
    """
    token_hash = hash_token(refresh_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if record is None:
        raise AuthenticationError("Invalid refresh token")
    if record.status == RefreshTokenStatus.ROTATED or record.status == RefreshTokenStatus.REVOKED:
        # Possible reuse of a rotated/revoked token: revoke the whole family.
        _revoke_user_tokens(db, record.user_id)
        raise AuthenticationError("Refresh token reuse detected; please log in again")
    if record.expires_at < _utcnow():
        record.status = RefreshTokenStatus.EXPIRED
        db.commit()
        raise AuthenticationError("Refresh token expired")
    user = db.get(User, record.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise AuthenticationError("User not found or inactive")
    record.status = RefreshTokenStatus.ROTATED
    tokens = _issue_tokens(db, user, request)
    record.replaced_by_jti = tokens["jti"]
    db.commit()
    return tokens


def logout(
    db: Session,
    *,
    user: User,
    refresh_token: str | None = None,
    access_token_jti: str | None = None,
    request_id: str | None = None,
) -> None:
    if access_token_jti:
        _revoke_access_token(db, access_token_jti, user.id)
    if refresh_token:
        record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)))
        if record is not None and record.status == RefreshTokenStatus.ACTIVE:
            record.status = RefreshTokenStatus.REVOKED
            record.revoked_at = _utcnow()
    db.commit()
    audit_helper.audit(
        db,
        action=audit_helper.AuditAction.LOGOUT,
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        request_id=request_id,
    )


def revoke_all_user_tokens(db: Session, user_id: int) -> int:
    return _revoke_user_tokens(db, user_id)


def _revoke_user_tokens(db: Session, user_id: int) -> int:
    count = 0
    for record in db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)):
        if record.status == RefreshTokenStatus.ACTIVE:
            record.status = RefreshTokenStatus.REVOKED
            record.revoked_at = _utcnow()
            count += 1
    db.commit()
    return count


def _revoke_access_token(db: Session, jti: str, user_id: int, expires_at: datetime | None = None) -> None:
    """Mark an access-token JTI as revoked. Idempotent — re-voking is a no-op."""
    existing = db.scalar(select(RevokedAccessToken).where(RevokedAccessToken.jti == jti))
    if existing is not None:
        return
    if expires_at is None:
        expires_at = _utcnow() + timedelta(minutes=settings.access_token_ttl_minutes)
    db.add(
        RevokedAccessToken(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
        )
    )
    db.commit()


def _is_access_token_revoked(db: Session, jti: str) -> bool:
    """Return True if the access-token JTI is in the revocation list."""
    return db.scalar(select(RevokedAccessToken).where(RevokedAccessToken.jti == jti)) is not None


def purge_expired_revocations(db: Session) -> int:
    """Remove revocation rows whose underlying access token has expired.

    Called periodically to keep the revocation table bounded. Safe to run
    on every logout/refresh; expired rows are harmless but should not grow
    unboundedly.
    """
    count = 0
    for record in db.scalars(
        select(RevokedAccessToken).where(RevokedAccessToken.expires_at < _utcnow())
    ):
        db.delete(record)
        count += 1
    db.commit()
    return count


def request_password_reset(
    db: Session,
    *,
    email: str,
    request: Request | None = None,
    request_id: str | None = None,
) -> dict:
    """Always returns success (prevents account enumeration). Token sent by
    the configured email provider; with the log provider it is recorded in
    the outbox. Rate limiting is applied at the API layer."""
    user = get_user_by_email(db, email)
    if user is None:
        logger.info("password_reset_requested_unknown_email")
        return {"sent": True}
    token = secrets.token_urlsafe(48)
    db.add(
        PasswordResetToken(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=_utcnow() + timedelta(minutes=settings.password_reset_token_ttl_minutes),
        )
    )
    db.commit()
    provider = get_email_provider()
    link = f"{settings.frontend_base_url}/reset-password?token={token}"
    sent = provider.send(
        db,
        EmailMessage(
            to=user.email,
            subject="WarrantyVault AI — password reset",
            body_text=(
                f"Hi {user.display_name},\n\n"
                "We received a request to reset your WarrantyVault AI password.\n"
                f"Open this link within {settings.password_reset_token_ttl_minutes} minutes:{link}\n\n"
                "If you did not request this, you can ignore this email.\n"
            ),
            payload={"type": "password_reset", "user_id": user.id},
        ),
    )
    audit_helper.audit(
        db,
        action=audit_helper.AuditAction.PASSWORD_RESET,
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=_ip(request),
        metadata={"email_delivery": "logged" if provider.is_configured() else "unavailable"},
    )
    return {"sent": sent}


def confirm_password_reset(db: Session, *, token: str, new_password: str, request_id: str | None = None) -> None:
    record = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
    )
    if record is None or record.revoked or record.used_at is not None:
        raise ValidationError("Reset token is invalid or already used")
    if record.expires_at < _utcnow():
        raise ValidationError("Reset token has expired")
    user = db.get(User, record.user_id)
    if user is None:
        raise ValidationError("User no longer exists")
    user.password_hash = hash_password(new_password)
    record.used_at = _utcnow()
    _revoke_user_tokens(db, user.id)
    db.commit()
    audit_helper.audit(
        db,
        action=audit_helper.AuditAction.PASSWORD_RESET,
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        request_id=request_id,
        metadata={"event": "confirmed"},
    )


def update_profile(db: Session, user: User, *, data: dict, request_id: str | None = None) -> User:
    if "display_name" in data and data["display_name"] is not None:
        user.display_name = str(data["display_name"]).strip()
    if "country" in data and data["country"] is not None:
        user.country = str(data["country"]).upper()
    if "phone" in data and data["phone"] is not None:
        user.phone = str(data["phone"]).strip()
    if "timezone" in data and data["timezone"] is not None:
        user.timezone = str(data["timezone"])
    if "locale" in data and data["locale"] is not None:
        user.locale = str(data["locale"])
    if "password" in data and data["password"]:
        user.password_hash = hash_password(str(data["password"]))
        _revoke_user_tokens(db, user.id)
    db.commit()
    db.refresh(user)
    return user


def close_account(
    db: Session, *, user: User, request_id: str | None = None, ip_address: str | None = None
) -> None:
    """Account closure: status CLOSED + closed_at. Personal data purge runs in
    the CLEANUP worker within the configured retention window (SRS NFR-02),
    unless documents are held by an open claim."""
    user.status = UserStatus.CLOSED
    user.closed_at = _utcnow()
    _revoke_user_tokens(db, user.id)
    db.commit()

    # Retention hook (SRS NFR-02): personal data is hard-purged by the
    # CLEANUP worker once the retention window has elapsed.
    from app.services.data_retention import schedule_user_purge

    schedule_user_purge(db, user_id=user.id)

    audit_helper.audit(
        db,
        action=audit_helper.AuditAction.ACCOUNT_DELETED,
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        request_id=request_id,
        ip_address=ip_address,
        metadata={"purge_within_days": settings.retention_closed_account_days},
    )