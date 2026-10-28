"""LLM-assisted structured extraction (SRS ML-03, SDD "Optional LLM for
schema extraction/reasoning").

This is a FALLBACK path only, used when the deterministic regex/heuristic
extractor (extractor.py) produces low-confidence or too-few fields on a
document. It is deliberately NOT a replacement:

- The deterministic extractor remains the default for every document and
  the ONLY path when this is disabled, unconfigured, or the SDK isn't
  installed — this module is imported lazily so the base app never
  requires it.
- Every field this produces still goes through the exact same
  confidence/provenance/human-review pipeline as a regex-extracted field
  (ExtractedField rows, FieldReview UI, TSD §45 model-identity tracking) —
  nothing here bypasses ML-06 (hallucination control) or ML-07
  (human-in-the-loop).
- On ANY failure — SDK missing, no API key, network error, timeout,
  malformed/non-JSON response — this returns None and the caller keeps the
  deterministic extractor's result unchanged. This path never raises into
  document processing and never blocks a document from completing.
- LLM-produced fields are capped at a conservative, provisional confidence
  (see _PROVISIONAL_CONFIDENCE) that keeps them in the "user review" band
  (SDD §8) rather than auto-accept, since this path has not gone through
  the same evaluation-dataset calibration as the deterministic extractor's
  thresholds (see ai_eval/run_evaluation.py).
"""

from __future__ import annotations

import json
import logging

from app.ai.extraction.extractor import ExtractionResult, FieldExtraction
from app.core.config import settings

logger = logging.getLogger(__name__)

LLM_MODEL_NAME = "llm-assisted-extraction"
LLM_MODEL_VERSION = "0.1.0"

# The exact field vocabulary the rest of the pipeline (ExtractedField rows,
# FieldReview.tsx, PurchaseCreate mapping) already expects — the LLM must
# not invent field names outside this set.
FIELD_NAMES = [
    "retailer",
    "purchase_date",
    "product_name",
    "model_number",
    "sku",
    "serial_number",
    "quantity",
    "unit_price",
    "tax",
    "total_price",
    "currency",
    "invoice_number",
]

_PROVISIONAL_CONFIDENCE = 0.75  # deliberately mid-"user review" band, never auto-accept

_SYSTEM_PROMPT = f"""You extract structured data from OCR text of a purchase receipt or invoice.

Return ONLY a JSON object with these fields (all optional — use null if not clearly present):
{json.dumps(FIELD_NAMES)}

Rules:
- NEVER invent, guess, or infer a value that is not clearly evidenced in the text. If uncertain, use null.
- purchase_date must be normalized to YYYY-MM-DD if determinable from any date format present.
- currency must be a 3-letter ISO code (e.g. USD, EUR, INR, GBP) if determinable, else null.
- unit_price, tax, and total_price must be plain decimal strings (e.g. "129.99"), no currency symbols or thousands separators.
- product_name must be the actual purchased item or service — never legal boilerplate, signature
  waivers, "computer generated invoice" disclaimers, watermark text (e.g. "SPECIMEN", "SAMPLE",
  "SYNTHETIC DOCUMENT"), or an amount-written-in-words line (e.g. "Rupees Twelve Thousand Only").
- retailer must be the selling business's name — never a watermark, disclaimer, or document-type
  label (e.g. "TAX INVOICE", "RECEIPT").
- If the document has multiple "total"-shaped lines (subtotal, an EMI/installment total, a tax
  total, a grand total), total_price must be the FINAL amount actually due — prefer a line
  explicitly labeled "grand total" when present.
- Output ONLY the JSON object. No prose, no markdown code fences, no explanation.
"""


def _client():
    """Returns a client for the configured provider. Import is local/lazy so
    the base app never requires any of the optional SDKs/libs."""
    if settings.extraction_llm_provider == "gemini_native":
        import httpx  # already a transitive dependency (fastapi/openai use it)

        return httpx.Client(timeout=settings.extraction_llm_timeout_seconds)
    if settings.extraction_llm_provider == "openai_compatible":
        import openai  # optional dependency

        return openai.OpenAI(
            api_key=settings.extraction_llm_api_key,
            base_url=settings.extraction_llm_base_url,
            timeout=settings.extraction_llm_timeout_seconds,
        )
    import anthropic  # optional dependency

    return anthropic.Anthropic(
        api_key=settings.extraction_llm_api_key,
        timeout=settings.extraction_llm_timeout_seconds,
    )


def _call_model(client, raw_text: str) -> str:
    """Provider-specific call + response-text extraction. Raises on
    failure — the caller (extract_with_llm) handles retries and turns any
    exception into a graceful None return."""
    if settings.extraction_llm_provider == "gemini_native":
        # Google's OpenAI-compatibility shim mishandles the newer "AQ."
        # auth-key type for some accounts (a known, currently unresolved
        # Google-side issue) — the native generateContent endpoint with an
        # x-goog-api-key header is the reliable path, confirmed working.
        assert settings.extraction_llm_api_key
        resp = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{settings.extraction_llm_model}:generateContent",
            headers={
                "x-goog-api-key": settings.extraction_llm_api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": raw_text[:8000]}]}],
                "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    if settings.extraction_llm_provider == "openai_compatible":
        resp = client.chat.completions.create(
            model=settings.extraction_llm_model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text[:8000]},
            ],
        )
        return resp.choices[0].message.content or ""

    resp = client.messages.create(
        model=settings.extraction_llm_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_text[:8000]}],
    )
    return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")

def should_use_llm_fallback(extraction: ExtractionResult) -> bool:
    """Decide whether to engage the LLM path. When
    extraction_llm_always_use is set, this runs on every document for
    consistent quality; otherwise it's a genuine fallback, only engaged
    when the deterministic extraction is weak. Either way, False whenever
    the feature isn't properly configured."""
    if not settings.extraction_llm_enabled or not settings.extraction_llm_api_key:
        return False
    if settings.extraction_llm_always_use:
        return True
    if len(extraction.fields) < settings.extraction_llm_min_fields_trigger:
        return True
    mean_conf = sum(f.confidence for f in extraction.fields) / len(extraction.fields)
    return mean_conf < settings.extraction_llm_fallback_threshold


def extract_with_llm(raw_text: str, page: int = 1) -> ExtractionResult | None:
    """Returns None on ANY failure so the caller falls back to the
    deterministic extractor's result unchanged. Never raises."""
    if not raw_text or not raw_text.strip():
        return None

    try:
        client = _client()
    except Exception:  # noqa: BLE001 - SDK not installed / bad config
        logger.exception("llm_extraction_client_init_failed")
        return None

    data: dict | None = None
    last_err: Exception | None = None
    for _attempt in range(settings.extraction_llm_max_retries + 1):
        try:
            text = _call_model(client, raw_text).strip()
            # Defensive: strip accidental markdown fences even though the
            # prompt forbids them — cheap and avoids a wasted retry.
            if text.startswith("```"):
                text = text.strip("`").removeprefix("json").strip()
            data = json.loads(text)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    if data is None:
        logger.warning("llm_extraction_failed error=%s", last_err)
        return None
    if not isinstance(data, dict):
        logger.warning("llm_extraction_bad_shape type=%s", type(data).__name__)
        return None

    result = ExtractionResult()
    for field_name in FIELD_NAMES:
        value = data.get(field_name)
        if value is None:
            continue
        value_str = str(value).strip()
        if not value_str:
            continue
        result.fields.append(
            FieldExtraction(
                field_name=field_name,
                value=value_str,
                normalized_value=value_str.lower() if field_name in ("retailer", "currency") else value_str,
                confidence=_PROVISIONAL_CONFIDENCE,
                source_page=page,
                source_region="llm",
                method="llm-assisted",
                model_name=LLM_MODEL_NAME,
                model_version=f"{settings.extraction_llm_model}@{LLM_MODEL_VERSION}",
            )
        )

    if not result.fields:
        result.warnings.append("LLM extraction returned no usable fields")
    return result
