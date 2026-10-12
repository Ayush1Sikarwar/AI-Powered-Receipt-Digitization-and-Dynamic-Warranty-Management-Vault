"""Standard success/error envelope (TSD §16).

Success: {"data": ..., "request_id": ...}
Error:   {"error_code": ..., "message": ..., "details": ..., "request_id": ...}
"""

from typing import Any
from uuid import uuid4

from fastapi import Request


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid4().hex


def ok(data: Any, request: Request) -> dict[str, Any]:
    return {"data": data, "request_id": request_id(request)}


def user_out(user) -> dict[str, Any]:
    return {
        "id": user.id,
        "uuid": user.uuid,
        "email": user.email,
        "display_name": user.display_name,
        "country": user.country,
        "timezone": user.timezone,
        "locale": user.locale,
        "role": user.role,
        "status": user.status.value if hasattr(user.status, "value") else user.status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }
