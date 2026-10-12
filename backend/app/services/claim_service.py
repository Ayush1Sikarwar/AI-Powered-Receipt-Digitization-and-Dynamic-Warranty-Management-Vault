"""Claim package generation (SRS FR-16, TSD §23/§33/§57).

A claim-ready package pre-fills everything the user needs to submit to the
manufacturer/retailer: purchase details, item rows (model/SKU, serial), the
matched manufacturer warranty coverage with computed timelines + source
provenance, the receipt (with its extracted fields), attached warranty
documents, and uploaded evidence (supporting media).

Every item carries a provenance/trust state (TSD §33/§42):

    VERIFIED          deterministic, reproducible
    AI_EXTRACTED      machine-produced, unconfirmed
    USER_CONFIRMED    the user stands behind it (incl. user-corrected)
    EXTERNAL_SOURCE   obtained from a manufacturer/official source
    INFERRED          heuristic / unconfirmed — explicitly NOT verified
    COMPUTED          deterministic computation from inputs

The MVP stops at CLAIM_READY (TSD §57): the package is never submitted
anywhere by the system; there is no submission endpoint, and no automation
that would bypass CAPTCHA, MFA, anti-bot controls, or access restrictions.

Status transition: DRAFT -> READY on generation. Regeneration is allowed
anytime before a terminal status (the package reflects current data).
"""

import io
import logging
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit as audit_helper
from app.core.audit import AuditAction
from app.models.claim import Claim, ClaimDocument, ClaimDocumentRole, ClaimFile, ClaimStatus
from app.models.document import Document, DocumentType
from app.models.extraction import ExtractedField
from app.models.notification import Notification, NotificationCategory, NotificationStatus
from app.models.purchase import Purchase
from app.models.user import User
from app.models.warranty import WarrantyPolicy
from app.warranty.service import source_out

logger = logging.getLogger(__name__)

PACKAGE_VERSION = "1.1"

# TSD §57 — the MVP boundary, stated inside the artifact itself.
SUBMISSION_BOUNDARY = {
    "mode": "CLAIM_READY",
    "external_submission": False,
    "note": (
        "This is a claim-ready package. WarrantyVault AI does not submit claims to "
        "manufacturers or retailers and has no automatic external submission. "
        "Automatic external submission may only be added where an official, permitted "
        "integration exists. The system never bypasses CAPTCHA, MFA, anti-bot controls, "
        "or access restrictions (TSD §57). Submit the package manually via the "
        "manufacturer's or retailer's own channels."
    ),
}

_RECEIPT_TYPES = {DocumentType.RECEIPT, DocumentType.INVOICE, DocumentType.PURCHASE_ORDER}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _provenance_from_verification(value: str, *, corrected_note: bool = False) -> tuple[str, str | None]:
    """Map a stored verification status to a package provenance label."""
    if value == "USER_CONFIRMED":
        return "USER_CONFIRMED", None
    if value == "USER_CORRECTED":
        return "USER_CONFIRMED", "user-corrected (AI original retained: see original_value)"
    if value == "AI_EXTRACTED":
        return "AI_EXTRACTED", "machine-produced; not yet confirmed by the user"
    # DRAFT / PARTIALLY_VERIFIED / anything unexpected -> honest fallback
    if value == "PARTIALLY_VERIFIED":
        return "USER_CONFIRMED", "partially confirmed by the user"
    return "INFERRED", "unconfirmed; treat as a starting point, not a verified fact"


def _media_kind(mime_type: str) -> str:
    mime = (mime_type or "").lower()
    if mime.startswith("image/"):
        return "PHOTO"
    if mime.startswith("video/"):
        return "VIDEO"
    if mime == "application/pdf" or mime.startswith("text/"):
        return "DOCUMENT"
    return "OTHER"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "file"


def _document_out(db: Session, doc: Document, *, with_fields: bool = False) -> dict:
    """A stored document with metadata + (optionally) its extracted fields."""
    from app.services import extraction_service

    out: dict[str, Any] = {
        "id": doc.id,
        "document_type": doc.document_type.value,
        "original_filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "file_size": doc.file_size,
        "sha256": doc.sha256_hash,
        "page_count": doc.page_count,
        "status": doc.status.value,
        "provenance": "USER_CONFIRMED",
        "provenance_note": "stored vault document selected by the user",
    }
    if with_fields:
        fields = db.scalars(
            select(ExtractedField).where(ExtractedField.document_id == doc.id).order_by(ExtractedField.id)
        ).all()
        field_rows = []
        for f in fields:
            if f.verification_status.value == "REJECTED":
                continue  # rejected fields are not data for a claim
            prov, note = _provenance_from_verification(f.verification_status.value)
            row = extraction_service.field_out(f)
            row["created_at"] = _iso(row.get("created_at"))
            row["updated_at"] = _iso(row.get("updated_at"))
            row["provenance"] = prov
            row["provenance_note"] = note
            field_rows.append(row)
        out["fields"] = field_rows
    return out


def _mapping_for_item(db: Session, item_id: int):
    from app.models.warranty import WarrantyMapping

    return db.scalar(select(WarrantyMapping).where(WarrantyMapping.purchase_item_id == item_id))


def _timelines_for_item(db: Session, item_id: int):
    from app.models.warranty import WarrantyTimeline

    return db.scalars(select(WarrantyTimeline).where(WarrantyTimeline.purchase_item_id == item_id)).all()


def _claim_documents(db: Session, claim_id: int) -> list[ClaimDocument]:
    return list(
        db.scalars(
            select(ClaimDocument).where(ClaimDocument.claim_id == claim_id).order_by(ClaimDocument.id)
        ).all()
    )


def build_claim_package(db: Session, claim: Claim) -> dict:
    """Serialize the claim package (read-only, no status changes)."""
    from app.warranty.service import policy_out  # local import: avoid cycle at module load

    purchase = db.get(Purchase, claim.purchase_id)
    if purchase is None:
        return {
            "package_version": PACKAGE_VERSION,
            "claim_id": claim.id,
            "error": "purchase no longer exists",
        }

    purchase_prov, purchase_note = _provenance_from_verification(purchase.verification_status.value)

    items = []
    warranty = []
    for item in purchase.items:
        items.append(
            {
                "product_name": item.product_name_raw,
                "sku": item.sku_raw,
                "model_number": item.model_number_raw,
                "serial_number": item.serial_number,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price) if item.unit_price is not None else None,
                "line_total": str(item.line_total) if item.line_total is not None else None,
                "provenance": "USER_CONFIRMED",
                "provenance_note": "entered by the user when the purchase was recorded",
            }
        )
        mapping = _mapping_for_item(db, item.id)
        if mapping is None or mapping.verification_status.value == "REJECTED":
            # No accepted match -> no coverage is asserted in the package.
            continue

        policy = db.get(WarrantyPolicy, mapping.warranty_policy_id)
        if policy is None:
            continue
        match_prov, match_note = _provenance_from_verification(mapping.verification_status.value)
        timelines = [
            {
                "component": t.component,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "expiry_date": t.expiry_date.isoformat() if t.expiry_date else None,
                "status": t.status.value,
                "provenance": "COMPUTED",
                "provenance_note": "computed deterministically from the policy terms and the purchase anchor",
            }
            for t in _timelines_for_item(db, item.id)
        ]
        source = source_out(db, policy.source_id) if policy.source_id else None
        warranty.append(
            {
                "item_index": len(items) - 1,
                "match_method": mapping.match_method.value,
                "match_confidence": mapping.match_confidence,
                "verification_status": mapping.verification_status.value,
                "match_provenance": match_prov,
                "match_provenance_note": match_note,
                "policy": policy_out(db, policy),
                "source": source,
                "source_provenance": "EXTERNAL_SOURCE",
                "source_provenance_note": (
                    f"manufacturer/retailer terms obtained from an external source "
                    f"({source['source_type']}, trust {source['trust_level']}/5)"
                    if source
                    else "no external source recorded for this policy"
                ),
                "timelines": timelines,
            }
        )

    # --- Documents: the purchase's receipt + user-attached stored documents ---
    attachments = _claim_documents(db, claim.id)
    purchase_doc = db.get(Document, purchase.document_id) if purchase.document_id else None

    receipt = None
    if purchase_doc is not None and purchase_doc.document_type in _RECEIPT_TYPES:
        receipt = _document_out(db, purchase_doc, with_fields=True)
    else:
        for a in attachments:
            if a.role == ClaimDocumentRole.RECEIPT:
                doc = db.get(Document, a.document_id)
                if doc is not None:
                    receipt = _document_out(db, doc, with_fields=True)
                    receipt["provenance_note"] = "user-selected receipt attachment"
                    break

    warranty_documents = []
    if purchase_doc is not None and purchase_doc.document_type == DocumentType.WARRANTY_CARD:
        d = _document_out(db, purchase_doc)
        d["provenance_note"] = "purchase's linked warranty document"
        warranty_documents.append(d)
    for a in attachments:
        if a.role != ClaimDocumentRole.WARRANTY:
            continue
        doc = db.get(Document, a.document_id)
        if doc is None:
            continue
        d = _document_out(db, doc, with_fields=doc.document_type in _RECEIPT_TYPES)
        d["provenance_note"] = "user-selected warranty document attachment"
        warranty_documents.append(d)

    attached_evidence_docs = []
    for a in attachments:
        if a.role != ClaimDocumentRole.EVIDENCE:
            continue
        doc = db.get(Document, a.document_id)
        if doc is None:
            continue
        d = _document_out(db, doc)
        d["provenance_note"] = "user-selected supporting document"
        attached_evidence_docs.append(d)

    evidence = [
        {
            "id": f.id,
            "original_filename": f.original_filename,
            "mime_type": f.mime_type,
            "media_kind": _media_kind(f.mime_type),
            "sha256": f.sha256,
            "file_size": f.file_size,
            "provenance": "USER_CONFIRMED",
            "provenance_note": "uploaded by the user as supporting evidence",
        }
        for f in db.scalars(select(ClaimFile).where(ClaimFile.claim_id == claim.id)).all()
    ]

    from app.models.product import Retailer

    retailer = db.get(Retailer, purchase.retailer_id) if purchase.retailer_id else None

    pkg = {
        "package_version": PACKAGE_VERSION,
        "claim_id": claim.id,
        "claim_uuid": claim.uuid,
        "issue_category": claim.issue_category,
        "issue_description": claim.issue_description,
        "issue_provenance": "USER_CONFIRMED",
        "issue_provenance_note": "described by the user",
        "contact": {
            "name": claim.contact_name,
            "email": claim.contact_email,
            "phone": claim.contact_phone,
            "provenance": "USER_CONFIRMED",
        },
        "notes": claim.notes,
        "purchase": {
            "id": purchase.id,
            "purchase_date": purchase.purchase_date,  # ISO YYYY-MM-DD string
            "invoice_number": purchase.invoice_number,
            "currency": purchase.currency,
            "total": str(purchase.total) if purchase.total is not None else None,
            "retailer": retailer.name if retailer else None,
            "warranty_region": purchase.warranty_region,
            "warranty_region_resolution_method": (
                purchase.warranty_region_resolution_method.value
                if purchase.warranty_region_resolution_method
                else None
            ),
            "provenance": purchase_prov,
            "provenance_note": purchase_note,
        },
        "items": items,
        "warranty": warranty,
        "receipt": receipt,
        "warranty_documents": warranty_documents,
        "attached_documents": attached_evidence_docs,
        "evidence": evidence,
        "submission": SUBMISSION_BOUNDARY,
        "disclaimer": (
            "Coverage windows are computed from manufacturer policy terms stored in "
            "WarrantyVault AI and may not reflect your full legal rights. Verify "
            "current terms with the manufacturer or retailer before submitting."
        ),
        "generated_at": _iso(datetime.now(UTC)),
    }
    pkg["report"] = render_claim_report(pkg)
    return pkg


def render_claim_report(pkg: dict) -> str:
    """The claim-ready report: a human-readable summary of the package (TSD §33)."""
    lines: list[str] = []
    p = pkg.get("purchase", {})
    lines.append(f"# Claim-Ready Report — Claim {pkg.get('claim_id')}")
    lines.append("")
    lines.append(f"Generated: {pkg.get('generated_at')}")
    lines.append(f"Version: {pkg.get('package_version')}")
    lines.append("")
    lines.append("## Issue")
    lines.append(f"- Category: {pkg.get('issue_category') or '—'}")
    lines.append(f"- Description: {pkg.get('issue_description') or '—'}")
    contact = pkg.get("contact", {})
    if contact.get("name") or contact.get("email"):
        lines.append(
            f"- Contact: {contact.get('name') or '—'} {contact.get('email') or ''} "
            f"{contact.get('phone') or ''}".strip()
        )
    lines.append("")
    lines.append("## Purchase")
    lines.append(f"- Date: {p.get('purchase_date') or '—'} (provenance: {p.get('provenance')})")
    lines.append(f"- Region: {p.get('warranty_region') or '—'}")
    lines.append(f"- Retailer: {p.get('retailer') or '—'}")
    lines.append(f"- Total: {p.get('total') or '—'} {p.get('currency') or ''}".strip())
    lines.append("")
    lines.append("## Items")
    for i, it in enumerate(pkg.get("items", []), 1):
        bits = [f"{it.get('product_name') or '(unnamed)'}"]
        if it.get("model_number"):
            bits.append(f"model {it['model_number']}")
        if it.get("sku"):
            bits.append(f"SKU {it['sku']}")
        if it.get("serial_number"):
            bits.append(f"serial {it['serial_number']}")
        lines.append(f"{i}. " + " · ".join(bits) + f" — provenance: {it.get('provenance')}")
    lines.append("")
    lines.append("## Manufacturer Warranty Coverage")
    if not pkg.get("warranty"):
        lines.append("- No accepted warranty match for this purchase.")
    for w in pkg.get("warranty", []):
        pol = w.get("policy", {})
        src = w.get("source") or {}
        lines.append(
            f"- Policy: {pol.get('policy_name')} v{pol.get('policy_version')} "
            f"(provenance: {w.get('source_provenance')}, trust {src.get('trust_level')}/5, "
            f"match {w.get('match_method')} — {w.get('match_provenance')})"
        )
        for t in w.get("timelines", []):
            lines.append(
                f"  - {t['component']}: {t.get('start_date') or '—'} → {t.get('expiry_date') or '—'} "
                f"[{t.get('status')}] (provenance: {t.get('provenance')})"
            )
    lines.append("")
    lines.append("## Documents")
    receipt = pkg.get("receipt")
    if receipt:
        lines.append(
            f"- Receipt: {receipt['original_filename']} (sha256 {receipt['sha256'][:16]}…, "
            f"status {receipt.get('status')})"
        )
        for f in receipt.get("fields", []):
            lines.append(
                f"  - {f['field_name']}: {f.get('value') or '—'} (provenance: {f.get('provenance')})"
            )
    for d in pkg.get("warranty_documents", []):
        lines.append(f"- Warranty document: {d['original_filename']} (sha256 {d['sha256'][:16]}…)")
    for d in pkg.get("attached_documents", []):
        lines.append(f"- Supporting document: {d['original_filename']}")
    lines.append("")
    lines.append("## Supporting Media")
    if not pkg.get("evidence"):
        lines.append("- None uploaded.")
    for e in pkg.get("evidence", []):
        lines.append(
            f"- {e['original_filename']} ({e.get('media_kind')}, sha256 {e['sha256'][:16]}…)"
        )
    lines.append("")
    lines.append("## Submission")
    lines.append(pkg["submission"]["note"])
    lines.append("")
    lines.append("> " + pkg.get("disclaimer", ""))
    return "\n".join(lines)


def build_evidence_bundle(db: Session, claim: Claim, package: dict) -> bytes:
    """The portable evidence bundle (ZIP) around the claim package.

    Layout:
        package.json        the full package (with provenance + report)
        claim-report.md     the human-readable claim-ready report
        README.txt          what this is + file verification
        evidence/<n>-<file> uploaded evidence files
        documents/<n>-<file> attached stored documents (receipt, warranty, other)
    """
    from app.services import storage as storage_service

    storage = storage_service.get_storage()
    buf = io.BytesIO()
    manifest_files: list[tuple[str, int, str]] = []  # (path, size, sha256)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(path: str, data: bytes) -> None:
            zf.writestr(path, data)
            manifest_files.append((path, len(data), _sha256_hex(data)))

        add("package.json", _json_bytes(package))
        add("claim-report.md", package.get("report", "").encode("utf-8"))

        # Documents: receipt first, then warranty docs, then attached evidence docs.
        docs: list[Document] = []
        receipt = package.get("receipt")
        if receipt:
            d = db.get(Document, receipt["id"])
            if d is not None:
                docs.append(d)
        for section in ("warranty_documents", "attached_documents"):
            for ref in package.get(section, []):
                d = db.get(Document, ref["id"])
                if d is not None and d not in docs:
                    docs.append(d)
        for i, d in enumerate(docs, 1):
            try:
                data = storage.get(d.storage_key)
                add(f"documents/{i:02d}-{_safe_name(d.original_filename)}", data)
            except Exception:  # noqa: BLE001 - missing file must not break the bundle
                logger.warning("bundle_missing_document doc_id=%s", d.id)

        # Evidence files.
        rows = db.scalars(select(ClaimFile).where(ClaimFile.claim_id == claim.id).order_by(ClaimFile.id)).all()
        for i, f in enumerate(rows, 1):
            try:
                data = storage.get(f.storage_key)
                add(f"evidence/{i:02d}-{_safe_name(f.original_filename)}", data)
            except Exception:  # noqa: BLE001
                logger.warning("bundle_missing_evidence file_id=%s", f.id)

        readme = [
            "WarrantyVault AI — claim evidence bundle",
            f"Claim: {package.get('claim_id')} (uuid {package.get('claim_uuid')})",
            f"Generated: {package.get('generated_at')}",
            "",
            "This bundle is a claim-ready artifact. WarrantyVault AI does NOT submit",
            "claims to manufacturers or retailers, and it never bypasses CAPTCHA, MFA,",
            "anti-bot controls, or access restrictions (TSD §57).",
            "",
            "Contents:",
        ]
        for path, size, sha in manifest_files:
            readme.append(f"  {path}  ({size} bytes, sha256 {sha})")
        readme += [
            "",
            "Verify each file with:  sha256sum -c <(awk '{print $2\"  \"$1}' FILES)",
            "or compare the sha256 values above against your downloaded files.",
            "",
            "Disclaimer: " + package.get("disclaimer", ""),
        ]
        add("README.txt", "\n".join(readme).encode("utf-8"))

    return buf.getvalue()


def _json_bytes(obj: dict) -> bytes:
    import json

    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def generate_claim_package(db: Session, claim: Claim, *, user: User, request_id: str | None = None) -> Claim:
    """Generate (or regenerate) the package; DRAFT/READY -> READY."""
    if claim.status in (ClaimStatus.RESOLVED, ClaimStatus.CLOSED):
        from app.core.exceptions import ValidationError

        raise ValidationError("Cannot generate a package for a terminal claim")

    package = build_claim_package(db, claim)
    claim.package = package
    claim.package_generated_at = datetime.now(UTC)
    claim.status = ClaimStatus.READY
    db.commit()
    db.refresh(claim)

    audit_helper.audit(
        db,
        action=AuditAction.CLAIM_PACKAGE_GENERATED,
        resource_type="claim",
        resource_id=claim.id,
        actor_id=user.id,
        request_id=request_id,
        metadata={"purchase_id": claim.purchase_id, "evidence_files": len(package.get("evidence", []))},
    )

    # CLAIM_READY notification (idempotent via notification_key).
    _notify_claim_ready(db, claim)
    return claim


def _notify_claim_ready(db: Session, claim: Claim) -> None:
    key = f"claim-READY-{claim.id}"
    existing = db.scalar(select(Notification).where(Notification.notification_key == key))
    if existing is not None:
        return
    db.add(
        Notification(
            user_id=claim.user_id,
            category=NotificationCategory.CLAIM_READY,
            title=f"Claim {claim.uuid[:8]} is ready",
            body="Your claim package has been generated. Download it and submit it to the manufacturer or retailer.",
            claim_id=claim.id,
            notification_key=key,
            scheduled_at=datetime.now(UTC),
            status=NotificationStatus.SCHEDULED,
        )
    )
    db.commit()
    logger.info("claim_package_generated claim_id=%s", claim.id)
