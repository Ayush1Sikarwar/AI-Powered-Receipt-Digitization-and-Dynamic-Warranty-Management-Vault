"""Document processing pipeline (TSD §24).

Full chain:
    Document → File Validation (at upload) → Image/PDF Rendering
    → Quality Assessment → Classification → Preprocessing/Enhancement
    → OCR Pass 1 (original) → OCR Pass 2 (enhanced) + fallback comparison
    → Structured Extraction → Normalization → Validation
    → Confidence Scoring → Human Review routing

Invariants:
- The original storage object is never mutated (TSD §7/§25).
- Enhancement never invents content.
- Extraction returns null when evidence is insufficient (never fabricates).
- Every AI result records model name/version + pipeline version (TSD §7/§45).
- Low-confidence critical fields route the document to REVIEW_REQUIRED (TSD §46).
"""

import logging
import uuid
from io import BytesIO

import cv2
import numpy as np
from PIL import Image
from sqlalchemy import select

from app.ai.classification.classifier import classify_document
from app.ai.extraction.extractor import (
    extract_from_lines,
)
from app.ai.extraction.llm_extractor import (
    extract_with_llm,
    should_use_llm_fallback,
)
from app.ai.ocr.engine import OcrLine, run_ocr_on_pages
from app.ai.preprocessing.enhance import enhance_image
from app.core.config import get_settings
from app.core.exceptions import OcrError, ProcessingError
from app.models.document import (
    DerivativeType,
    Document,
    DocumentDerivative,
    DocumentPage,
    DocumentStatus,
)
from app.models.extraction import (
    CRITICAL_FIELDS,
    ExtractedField,
    ExtractionJob,
    ExtractionJobStatus,
    VerificationStatus,
)
from app.services import storage as storage_service

logger = logging.getLogger(__name__)
settings = get_settings()

# Below this mean OCR confidence, a page is flagged for review (TSD §46).
PAGE_REVIEW_CONFIDENCE = 60.0

# Decompression-bomb guard: max decoded pixels per image (40 MP ≈ a 6400x6400
# image; a 15MB upload that decodes to more than this is rejected).
MAX_DECODE_PIXELS = 40_000_000


def render_pages(data: bytes, mime: str) -> list[Image.Image]:
    """Render a document (image or PDF) into a list of PIL images (1 page each).

    DoS guards (applied BEFORE heavy rendering/decoding):
    - PDF: page count checked via pypdf before any page is rasterized.
    - Images: decoded pixel dimensions capped (decompression-bomb guard).
    """
    if mime == "application/pdf":
        # Pre-render page-count check: reject over-cap PDFs in milliseconds
        # instead of rasterizing hundreds of pages into memory first.
        from pypdf import PdfReader

        try:
            page_count = len(PdfReader(BytesIO(data), strict=False).pages)
        except Exception:
            page_count = None  # unreadable -> pdf2image will raise below
        if page_count is not None and page_count > settings.upload_pdf_max_pages:
            raise ProcessingError(
                "PDF exceeds the maximum page count",
                details={"pages": page_count, "max_pages": settings.upload_pdf_max_pages},
            )
        from pdf2image import convert_from_bytes

        pages = convert_from_bytes(data, dpi=200)
        if not pages:
            raise ProcessingError("PDF produced no pages")
        if len(pages) > settings.upload_pdf_max_pages:
            raise ProcessingError(
                "PDF exceeds the maximum page count",
                details={"max_pages": settings.upload_pdf_max_pages},
            )
        return pages
    img: Image.Image = Image.open(BytesIO(data))
    # Decompression-bomb guard: cap decoded pixels (a few-KB flat-color PNG
    # can declare dimensions requiring multiple GB of RAM).
    try:
        w, h = img.size
    except Exception:
        w = h = 0
    if w * h > MAX_DECODE_PIXELS:
        raise ProcessingError(
            "Image exceeds the maximum decoded size",
            details={"width": w, "height": h, "max_pixels": MAX_DECODE_PIXELS},
        )
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return [img]


def quality_score(img: Image.Image) -> float:
    """Deterministic readability heuristic in [0,1] (diagnostic only)."""
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    contrast = float(gray.std())
    lap = _laplacian(gray)
    sharp = float(lap.var())
    mean = float(gray.mean())
    illum = max(0.0, 1.0 - abs(mean - 128.0) / 128.0)
    score = 0.4 * min(1.0, contrast / 60.0) + 0.4 * min(1.0, sharp / 400.0) + 0.2 * illum
    return round(min(1.0, max(0.0, score)), 4)


def _laplacian(gray: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    from numpy.lib.stride_tricks import sliding_window_view

    padded = np.pad(gray, 1, mode="edge")
    windows = sliding_window_view(padded, (3, 3))
    return np.einsum("ijkl,kl->ij", windows, kernel)


def _pil_to_bgr(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()
    return arr


def _save_png(img: Image.Image) -> tuple[bytes, str]:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def _ensure_derivative(db, document: Document, dtype: DerivativeType, page_number: int, processing_version: str):
    existing = db.scalar(
        select(DocumentDerivative).where(
            DocumentDerivative.document_id == document.id,
            DocumentDerivative.derivative_type == dtype,
            DocumentDerivative.page_number == page_number,
        )
    )
    return existing


def _upsert_page(db, document_id: int, page_number: int) -> DocumentPage:
    page = db.scalar(
        select(DocumentPage).where(DocumentPage.document_id == document_id, DocumentPage.page_number == page_number)
    )
    if page is None:
        page = DocumentPage(document_id=document_id, page_number=page_number)
        db.add(page)
        db.flush()
    return page


def _scaled(img: Image.Image, max_side: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img


def run_document_pipeline(db, document: Document) -> None:
    processing_version = settings.pipeline_version
    document.status = DocumentStatus.PROCESSING
    db.commit()

    job = ExtractionJob(
        document_id=document.id,
        pipeline_version=processing_version,
        status=ExtractionJobStatus.RUNNING,
    )
    db.add(job)
    db.flush()

    try:
        from app.core.security import _utcnow

        job.started_at = _utcnow()
        data = storage_service.get_storage().get(document.storage_key)
        pil_pages = render_pages(data, document.mime_type)
        document.page_count = len(pil_pages)

        bgr_pages = [_pil_to_bgr(p) for p in pil_pages]
        qualities = [quality_score(p) for p in pil_pages]

        # --- OCR pass 1 (original) ---------------------------------------
        try:
            ocr_result, ocr_report = run_ocr_on_pages(bgr_pages, low_confidence_trigger=PAGE_REVIEW_CONFIDENCE)
        except OcrError as exc:
            job.status = ExtractionJobStatus.FAILED
            job.error_code = "OCR_ERROR"
            job.error_message = str(exc)[:500]
            document.status = DocumentStatus.FAILED
            document.error_code = "OCR_ERROR"
            document.error_message = "OCR engine unavailable or failed"
            db.commit()
            raise ProcessingError("OCR failed: no recoverable text") from exc

        # --- Classification (FR-03) --------------------------------------
        full_text = ocr_result.text()
        classification = classify_document(full_text)
        document.document_type = classification.document_type
        job.classification = {
            "document_type": classification.document_type.value,
            "confidence": classification.confidence,
            "scores": classification.scores,
            "method": classification.method,
        }

        # --- Per-page records + OCR text (provenance) ---------------------
        review_needed = False
        for i, pil_img in enumerate(pil_pages):
            page = _upsert_page(db, document.id, i + 1)
            page.width, page.height = pil_img.width, pil_img.height
            page.quality_score = qualities[i]
            page_lines = ocr_result.pages[i]
            page.ocr_text = "\n".join(line.text for line in page_lines)
            page.ocr_engine = ocr_result.engine
            page.ocr_engine_version = ocr_result.engine_version
            confs = [line.confidence for line in page_lines if line.confidence >= 0]
            page.ocr_confidence = round(sum(confs) / len(confs), 2) if confs else 0.0
            if page.ocr_confidence < PAGE_REVIEW_CONFIDENCE:
                review_needed = True

            # --- Enhancement → derivative + OCR pass 2 on enhanced image --
            enhanced = enhance_image(bgr_pages[i], quality_before=qualities[i])
            enhanced_pil = Image.fromarray(cv2.cvtColor(enhanced.image, cv2.COLOR_BGR2RGB))
            enh_bytes, enh_mime = _save_png(enhanced_pil)
            if not _ensure_derivative(db, document, DerivativeType.ENHANCED, i + 1, processing_version):
                key = storage_service.storage_key("enhanced", document.user_id, uuid.uuid4().hex, "png")
                storage_service.get_storage().put(key, enh_bytes, enh_mime)
                db.add(
                    DocumentDerivative(
                        document_id=document.id,
                        derivative_type=DerivativeType.ENHANCED,
                        storage_key=key,
                        page_number=i + 1,
                        processing_version=processing_version,
                        mime_type=enh_mime,
                        metadata_={
                            "operations": enhanced.operations,
                            "metrics": enhanced.metrics,
                            "quality_before": enhanced.quality_before,
                            "quality_after": enhanced.quality_after,
                        },
                    )
                )
            # OCR pass 2: if the enhanced page scores higher, prefer it.
            try:
                from app.ai.ocr.engine import get_engine

                eng = get_engine()
                enhanced_lines = eng.recognize(enhanced.image, i + 1)
                enhanced_confs = [line.confidence for line in enhanced_lines if line.confidence >= 0]
                enhanced_mean = sum(enhanced_confs) / len(enhanced_confs) if enhanced_confs else 0.0
                if enhanced_mean > (page.ocr_confidence or 0) + 2.0:
                    page.ocr_text = "\n".join(line.text for line in enhanced_lines)
                    page.ocr_confidence = round(enhanced_mean, 2)
                    page.ocr_engine = f"{eng.name}+enhanced"
                    page.ocr_engine_version = eng.version
                    ocr_report[f"page{i+1}_enhanced_better"] = True
            except OcrError:
                pass

            # Thumbnail.
            thumb = _scaled(pil_img, max_side=360)
            thumb_bytes, thumb_mime = _save_png(thumb)
            if not _ensure_derivative(db, document, DerivativeType.THUMBNAIL, i + 1, processing_version):
                key = storage_service.storage_key("thumbnails", document.user_id, uuid.uuid4().hex, "png")
                storage_service.get_storage().put(key, thumb_bytes, thumb_mime)
                db.add(
                    DocumentDerivative(
                        document_id=document.id,
                        derivative_type=DerivativeType.THUMBNAIL,
                        storage_key=key,
                        page_number=i + 1,
                        processing_version=processing_version,
                        mime_type=thumb_mime,
                        metadata_={"max_side": 360},
                    )
                )

        job.ocr_report = ocr_report

        # --- Structured extraction (primary page = most evidence) ---------
        primary_idx = max(range(len(ocr_result.pages)), key=lambda i: len(ocr_result.pages[i]))
        primary_lines: list[OcrLine] = ocr_result.pages[primary_idx]
        extraction = extract_from_lines(primary_lines, page=primary_idx + 1)
        if should_use_llm_fallback(extraction):
            raw_text = "\n".join(line.text for line in primary_lines)
            try:
                llm_extraction = extract_with_llm(raw_text, page=primary_idx + 1)
            except Exception:  # noqa: BLE001 - defense in depth
                logger.exception("llm_extraction_fallback_unexpected_error document_id=%s", document.id)
                llm_extraction = None
            if llm_extraction is not None and llm_extraction.fields:
                logger.info(
                    "llm_extraction_fallback_used document_id=%s regex_fields=%d llm_fields=%d",
                    document.id, len(extraction.fields), len(llm_extraction.fields),
                )
                extraction = llm_extraction

        # Replace prior AI fields (corrections live on the same rows and are
        # preserved: we only replace AI_EXTRACTED rows, never user edits).
        for old in db.scalars(select(ExtractedField).where(ExtractedField.document_id == document.id)).all():
            if old.verification_status in (
                VerificationStatus.USER_CONFIRMED,
                VerificationStatus.USER_CORRECTED,
                VerificationStatus.REJECTED,
            ):
                continue
            db.delete(old)
        db.flush()

        for f in extraction.fields:
            db.add(
                ExtractedField(
                    document_id=document.id,
                    extraction_job_id=job.id,
                    field_name=f.field_name,
                    field_value=f.value,
                    normalized_value=f.normalized_value,
                    confidence=f.confidence,
                    source_page=f.source_page,
                    source_region=f.source_region,
                    extraction_method=f.method,
                    model_name=f.model_name,
                    model_version=f.model_version,
                    verification_status=VerificationStatus.AI_EXTRACTED,
                )
            )
        from app.models.product import ProductMatchStatus
        from app.services import product_service

        product_match = product_service.resolve_document_product(
            db, document, {f.field_name: f.value for f in extraction.fields}
        )
        if product_match.status == ProductMatchStatus.REVIEW_REQUIRED:
            review_needed = True

        # --- Confidence scoring + human review routing (TSD §46) ----------
        low_critical = []
        for f in extraction.fields:
            if f.field_name in CRITICAL_FIELDS and f.confidence < settings.confidence_user_review:
                low_critical.append(f.field_name)
        if low_critical or review_needed or extraction.warnings:
            review_needed = True
            document.status = DocumentStatus.REVIEW_REQUIRED
        else:
            document.status = DocumentStatus.COMPLETED

        job.status = ExtractionJobStatus.COMPLETED
        job.completed_at = _utcnow()
        document.error_code = None
        document.error_message = None
        db.commit()

        # --- Audit ---------------------------------------------------------
        from app.core import audit as audit_helper

        audit_helper.audit(
            db,
            action=audit_helper.AuditAction.AI_EXTRACTION_COMPLETED,
            resource_type="document",
            resource_id=document.id,
            metadata={
                "pipeline_version": processing_version,
                "classification": classification.document_type.value,
                "ocr_engine": ocr_result.engine,
                "ocr_mean_confidence": ocr_result.mean_confidence,
                "fields_extracted": [f.field_name for f in extraction.fields],
                "review_required": review_needed,
                "low_confidence_critical": low_critical,
                "product_match_status": product_match.status.value,
                "product_match_method": product_match.match_method.value,
                "product_match_confidence": product_match.match_confidence,
                "product_id": product_match.product_id,
            },
        )
        logger.info(
            "document_ai_processed doc_id=%s type=%s ocr_conf=%s fields=%s review=%s",
            document.id,
            classification.document_type.value,
            ocr_result.mean_confidence,
            len(extraction.fields),
            review_needed,
        )
    except ProcessingError as exc:
        # Typed failure (e.g. PDF page cap, image decode cap). The OCR path
        # above already committed the FAILED state; render-guard paths did
        # not. Ensure the document/job reach a terminal FAILED state either
        # way — a document must never be left stuck in PROCESSING.
        if job.status != ExtractionJobStatus.FAILED:
            logger.warning("document_processing_failed doc_id=%s error=%s", document.id, exc.message)
            job.status = ExtractionJobStatus.FAILED
            job.error_code = "PROCESSING_ERROR"
            job.error_message = exc.message[:500]
            document.status = DocumentStatus.FAILED
            document.error_code = "PROCESSING_ERROR"
            document.error_message = exc.message[:500]
            db.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("document_processing_failed doc_id=%s", document.id)
        job.status = ExtractionJobStatus.FAILED
        job.error_code = "PROCESSING_ERROR"
        job.error_message = exc.__class__.__name__
        document.status = DocumentStatus.FAILED
        document.error_code = "PROCESSING_ERROR"
        document.error_message = f"{exc.__class__.__name__}"
        db.commit()
        raise ProcessingError("Document processing failed") from exc
