"""Warranty source registry operations (SRS FR-12; task: source failure).

`refresh_source` re-retrieves a source and updates its provenance record
(URL, content hash, retrieval date, retrieval status, error). The hard
safety rule: **a failed refresh never deletes or deactivates the previous
valid policy** — it only records FAILED + the error. A successful refresh
with unchanged content merely updates `retrieved_at`. A successful refresh
with changed content updates the hash and, when a structured policy spec is
supplied, applies versioning (new version + archived predecessor) via
`upsert_policy_versioned`. Without a spec it is flagged `review_required`
and the existing policy stays valid.
"""

import hashlib
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.core.config import get_settings
from app.models.warranty import RetrievalStatus, WarrantySource
from app.warranty.versioning import upsert_policy_versioned

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 15
MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB cap for retrieved documents


class SourceFetchError(Exception):
    """Raised when a source cannot be retrieved."""


def fetch_source_content(url: str, *, timeout: float | None = None) -> str:
    """HTTP GET a source URL (official website / API landing page)."""
    try:
        resp = httpx.get(url, timeout=timeout or FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers={
            "User-Agent": f"{get_settings().app_name} warranty-source-refresh (+{get_settings().frontend_base_url})"
        })
    except httpx.HTTPError as exc:
        raise SourceFetchError(f"network error: {exc.__class__.__name__}: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceFetchError(f"HTTP {resp.status_code}")
    if len(resp.content) > MAX_CONTENT_BYTES:
        raise SourceFetchError(f"content too large ({len(resp.content)} bytes)")
    return resp.text


def refresh_source(
    db: Session,
    source_id: int,
    *,
    fetch=fetch_source_content,
    policies: list[dict] | None = None,
    actor_id: int | None = None,
    request_id: str | None = None,
) -> dict:
    """Refresh one source. See module docstring for the safety contract."""
    source = db.get(WarrantySource, source_id)
    if source is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Warranty source not found")

    if not source.source_url:
        return {"status": "SKIPPED", "reason": "source has no URL to retrieve"}

    try:
        content = fetch(source.source_url)
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not raised as deletion
        source.retrieval_status = RetrievalStatus.FAILED
        source.last_error = str(exc)[:2000]
        source.last_failure_at = datetime.now(UTC)
        db.commit()
        audit_helper.audit(
            db,
            action=AuditAction.WARRANTY_SOURCE_UPDATED,
            resource_type="warranty_source",
            resource_id=str(source.id),
            actor_id=actor_id,
            request_id=request_id,
            metadata={"status": "FAILED", "error": str(exc)[:200], "policies_unchanged": True},
        )
        logger.warning("source_refresh_failed source_id=%s error=%s", source.id, str(exc)[:200])
        return {
            "status": "FAILED",
            "error": str(exc)[:200],
            "policies_unchanged": True,  # the previous valid policy remains
        }

    new_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
    unchanged = new_hash == source.document_hash

    source.retrieval_status = RetrievalStatus.SUCCESS
    source.last_error = None
    source.retrieved_at = datetime.now(UTC)
    source.last_success_at = datetime.now(UTC)
    source.document_hash = new_hash

    result: dict = {
        "status": "UNCHANGED" if unchanged else "CHANGED",
        "content_changed": not unchanged,
        "document_hash": new_hash,
    }

    if unchanged:
        db.commit()
        return result

    if policies:
        applied = []
        for spec in policies:
            out = upsert_policy_versioned(
                db,
                manufacturer_id=source.manufacturer_id,
                policy_name=spec["policy_name"],
                region=spec.get("region"),
                policy_version=spec.get("policy_version", "1.0"),
                effective_from=_iso(spec["effective_from"]),
                effective_to=_iso(spec.get("effective_to")) if spec.get("effective_to") else None,
                product_category=spec.get("product_category"),
                product_model_pattern=spec.get("product_model_pattern"),
                source_id=source.id,
                coverages=spec.get("coverages") or [],
            )
            applied.append(
                {
                    "policy_name": spec["policy_name"],
                    "policy_version": out["policy"].policy_version,
                    "created": out["created"],
                    "superseded": out["superseded"],
                }
            )
        db.commit()
        result["policies"] = applied
    else:
        # Changed content but no structured terms supplied: keep the
        # current policy valid and flag it for manual review.
        result["review_required"] = True
        db.commit()

    audit_helper.audit(
        db,
        action=AuditAction.WARRANTY_SOURCE_UPDATED,
        resource_type="warranty_source",
        resource_id=str(source.id),
        actor_id=actor_id,
        request_id=request_id,
        metadata={"status": "SUCCESS", "content_changed": not unchanged, "policies": result.get("policies")},
    )
    return result


def _iso(value: str | object):
    from datetime import date

    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def source_out(db: Session, source: WarrantySource) -> dict:
    from sqlalchemy import select

    from app.models.product import Manufacturer
    from app.models.warranty import WarrantyPolicy

    mfr = db.get(Manufacturer, source.manufacturer_id) if source.manufacturer_id else None
    policies = db.scalars(select(WarrantyPolicy).where(WarrantyPolicy.source_id == source.id)).all()
    return {
        # TSD §44: source identifier
        "id": source.id,
        "manufacturer": mfr.name if mfr else None,
        "manufacturer_id": source.manufacturer_id,
        # source type
        "source_type": source.source_type.value,
        "source_url": source.source_url,
        "source_title": source.source_title,
        # owner
        "owner": source.owner,
        "region": source.region,
        # retrieval method
        "retrieval_method": source.retrieval_method,
        "trust_level": source.trust_level,
        # content hash + version
        "document_hash": source.document_hash,
        "content_hash": source.document_hash,
        "source_version": source.source_version,
        # last successful retrieval / last failure
        "retrieved_at": source.retrieved_at.isoformat() if source.retrieved_at else None,
        "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
        "last_failure_at": source.last_failure_at.isoformat() if source.last_failure_at else None,
        # status
        "retrieval_status": source.retrieval_status.value,
        "last_error": source.last_error,
        "effective_date": source.effective_date.isoformat() if source.effective_date else None,
        "policy_versions": [f"{p.policy_name} v{p.policy_version}" for p in policies],
    }
