"""System / environment status endpoints.

These expose the HONEST state of optional integrations (email, storage) so
the UI never implies a capability that is not configured. No secrets are
ever returned.
"""

from fastapi import APIRouter, Depends, Request

from app.api import envelope
from app.api.deps import current_user
from app.core.config import get_settings
from app.models.user import User
from app.notifications.email import email_status
from app.notifications.push import push_status
from app.services import storage as storage_service
from app.services.auth_service import close_account

router = APIRouter()
settings = get_settings()


@router.get("/system/email-status")
def get_email_status(request: Request, user: User = Depends(current_user)):
    # Backward-compatible shape plus the optional push channel state.
    return envelope.ok({**email_status(), "push": push_status()}, request)


@router.get("/system/status")
def get_system_status(request: Request, user: User = Depends(current_user)):
    return envelope.ok(
        {
            "env": settings.app_env,
            "storage": storage_service.storage_status(),
            "email": email_status(),
            "pipeline_version": settings.pipeline_version,
            "confidence": {
                "auto_accept": settings.confidence_auto_accept,
                "user_review": settings.confidence_user_review,
                "note": "PROVISIONAL until calibrated against the PRD evaluation dataset floor",
            },
        },
        request,
    )


@router.get("/system/model-registry")
def model_registry(request: Request, user: User = Depends(current_user)):
    from app.ai.registry import registry, thresholds_provisional

    return envelope.ok(
        {
            "components": [m.as_dict() for m in registry()],
            "confidence_thresholds": {
                "auto_accept": settings.confidence_auto_accept,
                "user_review": settings.confidence_user_review,
                "provisional": thresholds_provisional(),
                "note": "Provisional until the PRD §5 evaluation-dataset floor is met and thresholds are calibrated (SDD §8).",
            },
        },
        request,
    )


@router.post("/account/close")
def close_my_account(request: Request, user: User = Depends(current_user)):
    return _do_close(request, user)


def _do_close(request: Request, user: User):
    from sqlalchemy.orm import Session

    from app.db.session import SessionLocal

    # Use a fresh session to guarantee the close commits independently.
    db: Session = SessionLocal()
    try:
        # Re-fetch to operate on the committed row.
        from app.services import auth_service

        fresh = auth_service.get_user_by_id(db, user.id)
        if fresh is None:
            from app.core.exceptions import AuthenticationError

            raise AuthenticationError("User not found")
        close_account(db, user=fresh, request_id=getattr(request.state, "request_id", None))
    finally:
        db.close()
    return envelope.ok({"closed": True, "purge_within_days": settings.retention_closed_account_days}, request)
