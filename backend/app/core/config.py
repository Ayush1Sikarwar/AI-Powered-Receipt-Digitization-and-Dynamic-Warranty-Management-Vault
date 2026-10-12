"""Application configuration.

All secrets are read from environment variables / a local .env file.
No secret values are stored in source code (SRS Security Requirements).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application ---
    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "WarrantyVault AI"
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    api_base_path: str = "/api/v1"

    # --- Database ---
    database_url: str = "postgresql+psycopg://warranty:warranty_dev_pw@127.0.0.1:5432/warranty_vault"

    # --- Redis (rate limiting / job signalling; optional, in-memory fallback) ---
    redis_url: str = "redis://127.0.0.1:6379/0"
    use_redis: bool = True

    # --- Auth ---
    auth_secret: str = Field(default="dev-only-insecure-secret-change-me", repr=False)
    auth_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    password_reset_token_ttl_minutes: int = 30
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536  # 64 MiB
    argon2_parallelism: int = 4

    # --- Rate limits (requests / window) ---
    rl_login_per_min: int = 10
    rl_password_reset_per_hour: int = 5
    rl_upload_per_hour: int = 20
    rl_api_per_min: int = 300
    rl_window_seconds: int = 60

    # --- Upload limits ---
    upload_max_bytes: int = 15 * 1024 * 1024  # 15 MB
    upload_allowed_extensions: str = "jpg,jpeg,png,webp,pdf"
    upload_pdf_max_pages: int = 20

    # --- Object storage ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = "data/storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "warranty-vault"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = Field(default=None, repr=False)
    s3_secret_key: str | None = Field(default=None, repr=False)
    signed_url_ttl_seconds: int = 300

    # --- Email ---
    email_provider: Literal["log", "smtp"] = "log"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = Field(default=None, repr=False)
    smtp_from: str = "no-reply@warrantyvault.local"
    frontend_base_url: str = "http://localhost:5173"

    # --- Warranty alerts / notifications (SRS FR-15) ---
    # Reminder tiers in days before expiry (configurable, comma-separated).
    warranty_reminder_days: str = "60,30,7"
    # Worker idle-sweep cadence for scheduling/sending notifications.
    notification_sweep_seconds: int = 60
    # Local hour (0-23, user's timezone) at which due reminders are sent.
    reminder_local_hour: int = 9
    # Delivery retry: max attempts per notification, and the base of the
    # exponential backoff (5m, 10m, 20m, ... between attempts).
    notification_max_attempts: int = 3
    notification_retry_base_minutes: int = 5
    # Push channel: optional provider. "none" = not configured (honest
    # UI state); "log" = dev transport recorded in push_outbox.
    push_provider: Literal["none", "log"] = "none"

    # --- AI pipeline ---
    # 1.1.0: digit-aware product-name heuristic (model numbers no longer
    # disqualify candidate lines) + model identity on every extracted field.
    pipeline_version: str = "1.1.0"
    ocr_primary_engine: Literal["tesseract", "paddleocr"] = "tesseract"
    ocr_fallback_enabled: bool = True
    ocr_lang: str = "eng"
    tesseract_psm: int = 6
    enhancement_upscale_max: int = 2
    # Confidence strategy (SDD §8) - PROVISIONAL until calibrated against
    # the PRD evaluation dataset floor. See docs/ai/evaluation.md.
    confidence_auto_accept: float = 0.90
    confidence_user_review: float = 0.70
    # --- LLM-assisted extraction (SRS ML-03, SDD "Optional LLM for schema
    # extraction/reasoning") ---
    # The deterministic regex/heuristic extractor (extractor.py) is kept as
    # the fast, zero-cost, zero-dependency default and remains the ONLY path
    # when this is disabled or misconfigured. When enabled, the LLM path is
    # used as a FALLBACK for documents the regex extractor is least
    # confident about (see extraction_llm_fallback_threshold) — never a
    # silent replacement, and never the sole source of truth for a field
    # (ML-06: nothing here bypasses confidence scoring, provenance, or
    # human review).
    #
    # "anthropic" uses the native Anthropic SDK (paid beyond a small
    # one-time trial credit). "openai_compatible" uses the OpenAI SDK
    # pointed at any OpenAI-compatible chat-completions endpoint — this
    # covers Google's Gemini API free tier (an ongoing, genuinely free tier;
    # set extraction_llm_base_url to Gemini's OpenAI-compatible endpoint), a
    # local Ollama server (zero cost forever, fully private — set the base
    # URL to your local Ollama address), Groq's free tier, or any other
    # compatible provider/proxy.
    extraction_llm_enabled: bool = False
    extraction_llm_provider: Literal["anthropic", "openai_compatible", "gemini_native"] = "anthropic"
    extraction_llm_model: str = "claude-sonnet-4-6"
    extraction_llm_api_key: str | None = Field(default=None, repr=False)
    # Only used when extraction_llm_provider="openai_compatible". Examples:
    #   Gemini free tier: https://generativelanguage.googleapis.com/v1beta/openai/
    #   Local Ollama:     http://127.0.0.1:11434/v1
    #   Groq free tier:   https://api.groq.com/openai/v1
    extraction_llm_base_url: str | None = None
    # Trigger the LLM fallback when the regex extractor's mean field
    # confidence for a document falls below this, OR when it produced too
    # few fields to be usable (see extraction_llm_min_fields_trigger).
    extraction_llm_fallback_threshold: float = 0.70
    extraction_llm_min_fields_trigger: int = 4
    # When True, the LLM path runs on every document regardless of the
    # deterministic extractor's confidence — for consistent extraction
    # quality across all documents rather than only the weak ones. The
    # deterministic extractor still runs first and is still the result
    # used if the LLM call fails for any reason (ML-06: this never blocks
    # a document on an external API being unavailable).
    extraction_llm_always_use: bool = False
    extraction_llm_timeout_seconds: int = 20
    extraction_llm_max_retries: int = 1

    # --- Warranty / claims ---
    reminder_days: str = "60,30,7"
    region_defaults: str = ""  # optional fallback map, e.g. US:USA,DE:Germany

    # --- Retention (SRS NFR-02) ---
    retention_closed_account_days: int = 90
    retention_audit_min_days: int = 365
    # Grace period between a user-requested document deletion and the hard
    # purge by the CLEANUP worker. Purge re-checks the open-claim hold, so a
    # claim opened during the grace period still blocks deletion (SRS NFR-02).
    retention_deleted_document_days: int = 7

    # --- Production operations (TSD §40/§48/§51; enabled via the production
    # environment file, off by default so dev behavior is unchanged) ---
    # Prometheus text-format /metrics endpoint (never exposed by the proxy).
    metrics_enabled: bool = False
    # Secure response headers (HSTS, CSP, nosniff, framing, referrer).
    # The reverse proxy sets the same headers; this is defense in depth and
    # what makes them verifiable in single-container deployments.
    security_headers_enabled: bool = False
    # Readiness check: require a fresh worker heartbeat (worker monitoring).
    health_check_worker: bool = False
    worker_heartbeat_key: str = "worker:heartbeat"
    worker_heartbeat_max_age_seconds: int = 120
    # Behind a trusted reverse proxy: derive the client IP from the first
    # X-Forwarded-For entry (rate limits and logs key on the real client,
    # not the proxy's address — audit finding F10).
    trust_proxy: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_upload_extensions(self) -> set[str]:
        return {e.strip().lower().lstrip(".") for e in self.upload_allowed_extensions.split(",") if e.strip()}

    @property
    def reminder_day_list(self) -> list[int]:
        return sorted({int(x) for x in self.reminder_days.split(",") if x.strip()}, reverse=True)

    @field_validator("log_level")
    @classmethod
    def _upper_log(cls, v: str) -> str:
        return v.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
