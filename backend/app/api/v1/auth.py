"""Authentication endpoints (TSD §17).

POST /auth/register
POST /auth/login
POST /auth/logout
POST /auth/refresh
POST /auth/password-reset/request
POST /auth/password-reset/confirm
GET  /auth/me
"""

from contextlib import suppress

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db, get_request_id, rate_limit
from app.core.config import get_settings
from app.core.security import decode_access_token
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    RefreshRequest,
    RegisterRequest,
    UserOut,
    UserUpdate,
)
from app.services import auth_service

router = APIRouter()
settings = get_settings()


@router.post("/register", status_code=201)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    user = auth_service.register(
        db,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        country=body.country,
        timezone=body.timezone,
        locale=body.locale,
        request=request,
        request_id=get_request_id(request),
    )
    tokens = auth_service._issue_tokens(db, user, request)
    return envelope.ok({"user": envelope.user_out(user), **tokens}, request)


@router.post("/login", dependencies=[Depends(rate_limit("login", settings.rl_login_per_min))])
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    tokens = auth_service.login(
        db,
        email=body.email,
        password=body.password,
        request=request,
        request_id=get_request_id(request),
    )
    user = auth_service.get_user_by_email(db, body.email)
    return envelope.ok({"user": envelope.user_out(user), **tokens}, request)


@router.post("/logout")
def logout(
    request: Request,
    authorization: str | None = Header(default=None),
    body: RefreshRequest | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    refresh_token = body.refresh_token if body else None

    # Revoke the access token used for this request (from the Bearer header)
    # so it cannot be reused after logout, even before its JWT expiry.
    access_token_jti = None
    if authorization and authorization.lower().startswith("bearer "):
        with suppress(Exception):
            access_token_jti = decode_access_token(
                authorization.split(" ", 1)[1].strip()
            )["jti"]

    auth_service.logout(
        db,
        user=user,
        refresh_token=refresh_token,
        access_token_jti=access_token_jti,
        request_id=get_request_id(request),
    )
    return envelope.ok({"logged_out": True}, request)


@router.post("/refresh")
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    tokens = auth_service.refresh(
        db, refresh_token=body.refresh_token, request=request, request_id=get_request_id(request)
    )
    return envelope.ok(tokens, request)


@router.post("/password-reset/request", dependencies=[Depends(rate_limit("pwreset", settings.rl_password_reset_per_hour, 3600))])
def password_reset_request(body: PasswordResetRequestRequest, request: Request, db: Session = Depends(get_db)):
    auth_service.request_password_reset(
        db, email=body.email, request=request, request_id=get_request_id(request)
    )
    # Always 200 + identical body to avoid account enumeration.
    return envelope.ok({"sent": True, "message": "If that email is registered, a reset link has been sent."}, request)


@router.post("/password-reset/confirm")
def password_reset_confirm(body: PasswordResetConfirmRequest, request: Request, db: Session = Depends(get_db)):
    auth_service.confirm_password_reset(
        db, token=body.token, new_password=body.new_password, request_id=get_request_id(request)
    )
    return envelope.ok({"reset": True}, request)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.patch("/me")
def update_me(body: UserUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    updated = auth_service.update_profile(
        db, user=user, data=body.model_dump(exclude_unset=True), request_id=get_request_id(request)
    )
    return envelope.ok({"user": envelope.user_out(updated)}, request)