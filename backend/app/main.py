"""FastAPI application factory.

- JSON structured logging (NFR-07)
- request-id correlation on every response (TSD §16/§39)
- consistent success/error envelopes (TSD §16)
- CORS, health checks (TSD §51)
"""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import AppError, RateLimitError, log_error
from app.core.logging import setup_logging

setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

# Process start marker (used by admin observability uptime metric).
BOOT_TIME = time.time()

# The secret shipped as a code default. Using it in production means anyone
# who reads the source can mint valid JWTs — refuse to start in that case.
_DEFAULT_AUTH_SECRET = "dev-only-insecure-secret-change-me"


def _enforce_secret_policy() -> None:
    """AUTH_SECRET policy (TSD §52): a weak/default secret is fatal in
    production and a loud warning elsewhere."""
    weak = settings.auth_secret == _DEFAULT_AUTH_SECRET or len(settings.auth_secret) < 32
    if not weak:
        return
    if settings.app_env == "production":
        raise RuntimeError(
            "Refusing to start in production: AUTH_SECRET must be a unique random "
            "value of at least 32 characters (openssl rand -hex 32) — "
            "never the development default"
        )
    logger.warning(
        "SECURITY_WARNING: default/short AUTH_SECRET in use (env=%s). "
        "Set a long random AUTH_SECRET before deploying.",
        settings.app_env,
    )


def _enforce_production_policy() -> None:
    """Production boot guards (TSD §52): the process must not start with a
    weak secret, debug mode, or an open CORS origin set."""
    _enforce_secret_policy()
    if settings.app_env != "production":
        return
    problems: list[str] = []
    if settings.debug:
        problems.append("DEBUG must be false in production")
    if "*" in settings.cors_origin_list or "http://localhost:5173" in settings.cors_origin_list:
        problems.append(
            "CORS_ORIGINS must list the real production origin(s) — "
            "never '*' and never the development origin"
        )
    if problems:
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


def create_app() -> FastAPI:
    _enforce_production_policy()
    # OpenAPI docs are a development convenience; hide them in production
    # (least attack surface — audit finding F4).
    is_prod = settings.app_env == "production"
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url=None if is_prod else "/api/docs",
        openapi_url=None if is_prod else "/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Secure response headers (defense in depth: the reverse proxy sets the
    # same set; these are added when absent so both layers can be verified).
    if settings.security_headers_enabled:
        _csp = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        _headers = {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": _csp,
        }

        @app.middleware("http")
        async def secure_headers_middleware(request: Request, call_next):
            response = await call_next(request)
            for k, v in _headers.items():
                if k not in response.headers:
                    response.headers[k] = v
            return response

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid

        # Client IP: behind a trusted reverse proxy the socket peer is the
        # proxy itself, so take the first X-Forwarded-For entry instead
        # (audit finding F10 — rate limits must key on the real client).
        client_ip = request.client.host if request.client else "unknown"
        if settings.trust_proxy:
            xff = request.headers.get("X-Forwarded-For")
            if xff:
                client_ip = xff.split(",")[0].strip() or client_ip

        # Global per-IP rate limit on API endpoints (SRS Security: "Rate limits
        # shall protect authentication, upload, and API endpoints"). Auth and
        # upload routes carry their own tighter limits; this is the general cap.
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            from app.core.rate_limit import rate_limiter

            rl = rate_limiter.hit(f"api:{client_ip}", settings.rl_api_per_min, settings.rl_window_seconds)
            if not rl.allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error_code": "RATE_LIMITED",
                        "message": "Too many requests, please slow down",
                        "details": None,
                        "request_id": rid,
                    },
                    headers={"Retry-After": str(rl.retry_after_seconds)},
                )

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = rid
        if settings.metrics_enabled:
            from app.core.metrics import metrics as _metrics

            _metrics.record_request(request.method, request.url.path, response.status_code, elapsed_ms)
        # Simple API latency signal (TSD §40) as a structured log line.
        logger.info(
            "api_request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "client_ip": client_ip,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 1),
            },
        )
        return response

    # --- Error handlers: safe externally, detailed internally ---

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        rid = getattr(request.state, "request_id", None)
        log_error(exc, request_id=rid, context={"path": request.url.path})
        headers = {"Retry-After": str(exc.details.get("retry_after_seconds", 1))} if isinstance(exc, RateLimitError) else {}
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload(rid), headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        rid = getattr(request.state, "request_id", None)
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        if "error_code" not in detail:
            detail = {**detail, "error_code": "VALIDATION_ERROR" if exc.status_code == 422 else "NOT_FOUND" if exc.status_code == 404 else "INTERNAL_ERROR"}
        payload = {"error_code": detail.get("error_code"), "message": detail.get("message", str(exc.detail)), "details": detail.get("details"), "request_id": rid}
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", None)
        payload = {
            "error_code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {"errors": [
                {
                    "loc": [str(p) for p in err.get("loc", [])],
                    "msg": err.get("msg"),
                    "type": err.get("type"),
                }
                for err in exc.errors()
            ]},
            "request_id": rid,
        }
        return JSONResponse(status_code=422, content=payload)

    # Out-of-range integer IDs (e.g. /documents/999999999999) overflow the DB's
    # int4 and raise a driver DataError (wrapped by SQLAlchemy). Semantically
    # that is "not found", so answer 404 with the standard envelope instead of
    # a 500.
    try:
        from psycopg import errors as _pg_errors
        from sqlalchemy.exc import DataError as _sa_data_error

        @app.exception_handler(_sa_data_error)
        async def data_error_handler(request: Request, exc: Exception):
            orig = getattr(exc, "orig", None)
            rid = getattr(request.state, "request_id", None)
            if isinstance(orig, _pg_errors.NumericValueOutOfRange):
                return JSONResponse(
                    status_code=404,
                    content={"error_code": "NOT_FOUND", "message": "Resource not found", "details": None, "request_id": rid},
                )
            return JSONResponse(
                status_code=500,
                content={"error_code": "INTERNAL_ERROR", "message": "An internal error occurred", "details": None, "request_id": rid},
            )
    except Exception:  # noqa: BLE001 - driver optional at import; handler is best-effort
        pass

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        log_error(exc, request_id=rid, context={"path": request.url.path})
        payload = {
            "error_code": "INTERNAL_ERROR",
            "message": "An internal error occurred",
            "details": None,
            "request_id": rid,
        }
        return JSONResponse(status_code=500, content=payload)

    # --- Routers ---
    from app.api.v1 import api_router

    app.include_router(api_router, prefix=settings.api_base_path)

    @app.get("/health", tags=["health"])
    def health():
        from app.core.rate_limit import rate_limiter

        return {"status": "ok", "version": "0.1.0", "env": settings.app_env, "rate_limit_backend": rate_limiter.backend}

    @app.get("/health/ready", tags=["health"])
    def readiness():
        from sqlalchemy import func, select, text

        from app.db.session import engine

        checks: dict[str, str] = {}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {exc.__class__.__name__}"

        # Worker monitoring (TSD §40/§51): when enabled (production), a stale
        # heartbeat makes the deployment not-ready — the worker process is part
        # of the serving system (document processing, alerts, retention).
        if settings.health_check_worker:
            from app.core.rate_limit import get_redis_client

            redis = get_redis_client()
            if redis is None:
                checks["worker"] = "unavailable: redis not connected"
            else:
                try:
                    beat = redis.get(settings.worker_heartbeat_key)
                    age = None if beat is None else time.time() - float(beat)
                    if age is None:
                        checks["worker"] = "no heartbeat"
                    elif age > settings.worker_heartbeat_max_age_seconds:
                        checks["worker"] = f"stale ({age:.0f}s old)"
                    else:
                        checks["worker"] = f"ok ({age:.0f}s old)"
                except Exception as exc:  # noqa: BLE001
                    checks["worker"] = f"error: {exc.__class__.__name__}"

        # Job queue health: DEAD jobs are expected to surface in admin tools,
        # but an unbounded queue is a not-ready condition.
        try:
            from app.models.job import BackgroundJob, JobStatus

            with engine.connect() as conn:
                queued = conn.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status == JobStatus.QUEUED)) or 0
            checks["queue"] = f"ok ({queued} queued)"
            if queued > 500:
                checks["queue"] = f"backlog ({queued} queued)"
        except Exception as exc:  # noqa: BLE001
            checks["queue"] = f"error: {exc.__class__.__name__}"

        ok = checks["database"] == "ok" and not (
            settings.health_check_worker and checks.get("worker", "ok").startswith(("no ", "stale", "unavailable", "error"))
        ) and checks["queue"].startswith("ok")
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded", **checks})

    if settings.metrics_enabled:

        @app.get("/metrics", tags=["health"])
        def metrics_endpoint():

            from sqlalchemy import func, select

            from app.core.metrics import metrics as _metrics
            from app.db.session import SessionLocal
            from app.models.document import Document, DocumentStatus
            from app.models.job import BackgroundJob, JobStatus

            db = SessionLocal()
            gauges: list[tuple[str, str, float | None]] = []
            try:
                for job_status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.DEAD, JobStatus.COMPLETED, JobStatus.FAILED):
                    n = db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status == job_status)) or 0
                    gauges.append((f"warrantyvault_jobs{{status=\"{job_status.value}\"}}", "Jobs by status (queue depth = QUEUED).", float(n)))
                for doc_status in (DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.REVIEW_REQUIRED, DocumentStatus.COMPLETED, DocumentStatus.FAILED):
                    n = db.scalar(select(func.count()).select_from(Document).where(Document.status == doc_status)) or 0
                    gauges.append((f"warrantyvault_documents{{status=\"{doc_status.value}\"}}", "Documents by status.", float(n)))
                from app.core.rate_limit import get_redis_client

                redis = get_redis_client()
                beat = redis.get(settings.worker_heartbeat_key) if redis is not None else None
                age = None if beat is None else time.time() - float(beat)
                gauges.append(("warrantyvault_worker_heartbeat_age_seconds", "Seconds since the worker last beat (NaN = no heartbeat / no redis).", age))
            finally:
                db.close()
            body = _metrics.render(BOOT_TIME, gauges)
            return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


app = create_app()
