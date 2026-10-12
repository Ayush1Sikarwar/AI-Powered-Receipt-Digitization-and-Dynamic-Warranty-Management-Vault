"""Shared FastAPI dependencies: auth, rate limiting, request context."""

import uuid
from contextvars import ContextVar

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, RateLimitError
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service

settings = get_settings()

# Correlation id per request (TSD §16/§39): generated in middleware, readable
# anywhere in request processing via this contextvar.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", request_id_ctx.get())


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error_code": "AUTHENTICATION_ERROR", "message": "Missing bearer token"})
    token = authorization.split(" ", 1)[1].strip()
    try:
        return auth_service.get_current_user(db, token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=exc.to_payload(get_request_id(request))) from exc


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(
            status_code=403,
            detail={"error_code": "AUTHORIZATION_ERROR", "message": "Administrator access required"},
        )
    return user


def rate_limit(key: str, limit: int, window: int | None = None):
    """Dependency factory: enforces a fixed-window rate limit for a route."""

    def _dep(request: Request) -> None:
        from app.core.rate_limit import rate_limiter

        client_ip = request.client.host if request.client else "unknown"
        result = rate_limiter.hit(f"{key}:{client_ip}", limit, window)
        if not result.allowed:
            raise RateLimitError(
                "Too many requests, please slow down",
                details={"retry_after_seconds": result.retry_after_seconds},
            )

    return _dep


def new_request_id() -> str:
    return uuid.uuid4().hex
