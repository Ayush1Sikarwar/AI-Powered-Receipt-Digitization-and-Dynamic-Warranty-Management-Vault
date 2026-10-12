"""Run the *production* pipeline stages over one evaluation sample.

Sequence mirrors app/ai/pipeline.py exactly (no DB/storage):
    render PNG -> quality assessment -> enhancement ->
    OCR pass 1 (original) -> OCR pass 2 (enhanced) -> keep better page
    -> structured extraction -> field comparison against ground truth.

Metrics: CER/WER for text (PRD §6), exact match / precision / recall / F1
for fields, using the shared app.ai.evaluation.metrics primitives.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image

from app.ai.evaluation.metrics import char_error_rate, word_error_rate
from app.ai.extraction.extractor import MODEL_NAME, MODEL_VERSION, extract_from_lines
from app.ai.ocr.engine import get_engine
from app.ai.pipeline import quality_score
from app.ai.preprocessing.enhance import enhance_image

MONEY_FIELDS = {"unit_price", "total_price", "tax"}
INT_FIELDS = {"quantity"}


def _norm_text(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip()).casefold()


def _match_field(field: str, gt: str, pred: str | None) -> bool:
    if pred is None:
        return False
    if field in MONEY_FIELDS:
        try:
            return abs(float(gt) - float(pred)) <= 0.005
        except ValueError:
            return False
    if field in INT_FIELDS:
        try:
            return int(gt) == int(pred)
        except ValueError:
            return _norm_text(gt) == _norm_text(pred)
    if field == "purchase_date":
        return gt == pred
    if field == "currency":
        return gt.upper() == pred.upper()
    return _norm_text(gt) == _norm_text(pred)


def evaluate_one(sample: dict, base_dir: Path) -> dict:
    """Evaluate a single manifest sample. Returns a result dict."""
    img_path = base_dir / sample["image"]
    pil_img = Image.open(img_path).convert("RGB")
    bgr = np.asarray(pil_img)[:, :, ::-1].copy()

    quality = quality_score(pil_img)
    enhanced = enhance_image(bgr, quality_before=quality)

    engine = get_engine()
    original_lines = engine.recognize(bgr, 1)
    enhanced_lines = engine.recognize(enhanced.image, 1)
    orig_confs = [ln.confidence for ln in original_lines if ln.confidence >= 0]
    enh_confs = [ln.confidence for ln in enhanced_lines if ln.confidence >= 0]
    orig_mean = sum(orig_confs) / len(orig_confs) if orig_confs else 0.0
    enh_mean = sum(enh_confs) / len(enh_confs) if enh_confs else 0.0
    # Same decision rule as the pipeline: enhanced wins only if clearly better.
    kept = "enhanced" if enh_mean > orig_mean + 2.0 else "original"
    kept_lines = enhanced_lines if kept == "enhanced" else original_lines

    ocr_text = "\n".join(ln.text for ln in kept_lines)
    gt_text = sample["ground_truth_text"]
    cer = char_error_rate(ocr_text, gt_text)
    wer = word_error_rate(ocr_text, gt_text)

    extraction = extract_from_lines(kept_lines, page=1)
    gt_fields = {k: v for k, v in sample["fields"].items() if v is not None}
    exempt = set(sample.get("exempt_fields", []))
    field_results: dict[str, dict] = {}
    tp = fp = fn = 0
    for name, gt in gt_fields.items():
        pred_field = extraction.get(name)
        pred = pred_field.normalized_value if pred_field else None
        ok = _match_field(name, gt, pred)
        if ok:
            tp += 1
        elif pred is not None:
            fp += 1
        else:
            fn += 1
        field_results[name] = {"ground_truth": gt, "predicted": pred, "match": ok}
    for f in extraction.fields:
        if f.field_name in gt_fields:
            continue
        if f.field_name in ("manufacturer",) or f.field_name in exempt:
            # Legitimate inference / ambiguous on this format — not a false
            # positive (recorded for transparency, excluded from fp count).
            field_results[f.field_name] = {
                "ground_truth": None,
                "predicted": f.normalized_value,
                "match": None,
                "note": "exempt" if f.field_name in exempt else "legitimate-inference",
            }
            continue
        # Predicted a field the document does not carry.
        fp += 1
        field_results[f.field_name] = {"ground_truth": None, "predicted": f.normalized_value, "match": False}

    n_gt = len(gt_fields)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    exact = 1.0 if (n_gt > 0 and tp == n_gt) else 0.0

    return {
        "document_id": sample["document_id"],
        "quality_category": sample["quality_category"],
        "retailer_format": sample["retailer_format"],
        "quality_score": quality,
        "kept_image": kept,
        "original_mean_confidence": round(orig_mean, 2),
        "enhanced_mean_confidence": round(enh_mean, 2),
        "ocr_engine": engine.name,
        "ocr_engine_version": engine.version,
        "extraction_model": MODEL_NAME,
        "extraction_model_version": MODEL_VERSION,
        "cer": round(cer, 4),
        "wer": round(wer, 4),
        "fields": field_results,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "exact_match": exact,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
