"""Run the full evaluation over the dataset and record ACTUAL measured
results (PRD §6, TSD §41).

Usage:
    python -m ai_eval.run_evaluation [--dataset ai_eval/dataset]
                                     [--out ai_eval] [--limit N]
                                     [--category NAME]

Writes:
    <out>/RESULTS.json   (machine-readable, per-sample + per-category)
    <out>/RESULTS.md     (human-readable report with honest caveats)

The PRD requires ~50 labeled samples per quality category before confidence
thresholds may be treated as final. This script checks per-category counts
against that floor and marks the thresholds provisional while it is unmet.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytesseract  # noqa: E402

from ai_eval.pipeline_eval import evaluate_one  # noqa: E402
from app.ai.extraction.extractor import MODEL_NAME, MODEL_VERSION  # noqa: E402

FLOOR_PER_CATEGORY = 50  # PRD §5 baseline floor (pre-calibration)

CATEGORIES = [
    "clear",
    "faded",
    "low_contrast",
    "crumpled",
    "skewed",
    "shadowed",
    "water_damaged",
    "retailer_formats",
]


def _aggregate(results: list[dict]) -> dict:
    if not results:
        return {"n": 0}
    tp = sum(r["tp"] for r in results)
    fp = sum(r["fp"] for r in results)
    fn = sum(r["fn"] for r in results)
    n = len(results)
    return {
        "n": n,
        "mean_cer": round(sum(r["cer"] for r in results) / n, 4),
        "mean_wer": round(sum(r["wer"] for r in results) / n, 4),
        "exact_match_rate": round(sum(r["exact_match"] for r in results) / n, 4),
        "field_precision": round(tp / (tp + fp), 4) if (tp + fp) else 0.0,
        "field_recall": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
        "field_f1": round(2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "mean_quality_score": round(sum(r["quality_score"] for r in results) / n, 4),
        "enhancement_improved_ocr": sum(1 for r in results if r["kept_image"] == "enhanced"),
    }


def run(dataset_dir: Path, out_dir: Path, limit: int = 0, category: str | None = None) -> dict:
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    samples = list(manifest.values())
    if category:
        samples = [s for s in samples if s["quality_category"] == category]
    if limit:
        samples = samples[:limit]
    if not samples:
        raise SystemExit("no samples to evaluate")

    results = []
    for i, sample in enumerate(samples):
        r = evaluate_one(sample, dataset_dir)
        results.append(r)
        print(
            f"[{i + 1}/{len(samples)}] {r['document_id']}: CER={r['cer']} WER={r['wer']} "
            f"F1={r['f1']} exact={r['exact_match']}"
        )

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["quality_category"]].append(r)
    per_category = {cat: _aggregate(by_cat[cat]) for cat in by_cat}
    overall = _aggregate(results)

    floor = {
        cat: {
            "samples": len(by_cat.get(cat, [])),
            "floor": FLOOR_PER_CATEGORY,
            "met": len(by_cat.get(cat, [])) >= FLOOR_PER_CATEGORY,
        }
        for cat in CATEGORIES
    }
    thresholds_final = all(v["met"] for v in floor.values())

    payload = {
        "metadata": {
            "dataset": "synthetic (rendered templates, exact ground truth at generation)",
            "sample_count": len(results),
            "floor_per_category": FLOOR_PER_CATEGORY,
            "floor_met": thresholds_final,
            "confidence_thresholds": "FINAL" if thresholds_final else "PROVISIONAL",
            "ocr_engine": results[0]["ocr_engine"],
            "ocr_engine_version": results[0]["ocr_engine_version"],
            "tesseract_version": str(pytesseract.get_tesseract_version()),
            "extraction_model": MODEL_NAME,
            "extraction_model_version": MODEL_VERSION,
        },
        "overall": overall,
        "per_category": per_category,
        "floor_check": floor,
        "samples": results,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "RESULTS.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / "RESULTS.md").write_text(_render_md(payload))
    print(f"\nwrote {out_dir / 'RESULTS.json'} and {out_dir / 'RESULTS.md'}")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(Path(__file__).parent / "dataset"))
    ap.add_argument("--out", default=str(Path(__file__).parent))
    ap.add_argument("--limit", type=int, default=0, help="evaluate at most N samples (0 = all)")
    ap.add_argument("--category", default=None, help="restrict to one quality category")
    args = ap.parse_args()
    run(Path(args.dataset), Path(args.out), args.limit, args.category)


def _render_md(p: dict) -> str:
    meta = p["metadata"]
    o = p["overall"]
    lines = [
        "# Document AI Pipeline — Baseline Evaluation Results",
        "",
        "Generated by `ai_eval/run_evaluation.py`. **All numbers below are actual",
        "measured results** from running the production pipeline stages over the",
        "evaluation dataset — none are estimates.",
        "",
        "## Honest caveats (read first)",
        "",
        "- Dataset is **synthetic**: receipts rendered from templates with exact",
        "ground truth captured at generation. It measures pipeline behavior under",
        "controlled, known degradations — not real-world variability.",
        f"- Per-category volume vs PRD floor ({meta['floor_per_category']}/category):",
        "see the floor table below. Until the floor is met, per the PRD all",
        f"confidence thresholds are **{meta['confidence_thresholds']}**.",
        f"- OCR engine: {meta['ocr_engine']} {meta['ocr_engine_version']}",
        f" (tesseract {meta['tesseract_version']}); extraction model:",
        f"{meta['extraction_model']} {meta['extraction_model_version']}.",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Samples | {o['n']} |",
        f"| Mean CER | {o['mean_cer']} |",
        f"| Mean WER | {o['mean_wer']} |",
        f"| Extraction exact match (all fields) | {o['exact_match_rate']} |",
        f"| Field precision | {o['field_precision']} |",
        f"| Field recall | {o['field_recall']} |",
        f"| Field F1 | {o['field_f1']} |",
        f"| tp / fp / fn | {o['tp']} / {o['fp']} / {o['fn']} |",
        f"| Mean quality score | {o['mean_quality_score']} |",
        f"| Enhancement kept over original | {o['enhancement_improved_ocr']}/{o['n']} |",
        "",
        "## Per quality category",
        "",
        "| Category | n | CER | WER | Exact | P | R | F1 | Enh kept |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cat, a in p["per_category"].items():
        lines.append(
            f"| {cat} | {a['n']} | {a['mean_cer']} | {a['mean_wer']} | {a['exact_match_rate']} "
            f"| {a['field_precision']} | {a['field_recall']} | {a['field_f1']} "
            f"| {a['enhancement_improved_ocr']}/{a['n']} |"
        )
    lines += [
        "",
        "## PRD floor check (~50 labeled samples/category, pre-calibration)",
        "",
        "| Category | Samples | Floor | Met |",
        "|---|---|---|---|",
    ]
    for cat, v in p["floor_check"].items():
        lines.append(f"| {cat} | {v['samples']} | {v['floor']} | {'yes' if v['met'] else 'no'} |")
    if all(v["met"] for v in p["floor_check"].values()):
        interpretation = (
            "The per-category floor is met in this run: confidence thresholds "
            "may be finalized using the measured values above, subject to the "
            "PRD's variance-based revisit."
        )
    else:
        interpretation = (
            "The per-category floor is **not yet met** in this run, so the "
            "SRS/SDD confidence thresholds remain **provisional** (as flagged "
            "in the model registry). The measured CER/WER/F1 above are the "
            "baseline that calibration will compare against once the labeled "
            "sample set grows toward the floor."
        )
    lines += [
        "",
        "### Interpretation",
        "",
        interpretation,
        "",
        "### Per-sample detail",
        "",
        "Machine-readable per-sample results (incl. per-field ground truth vs",
        "predicted) are in `RESULTS.json` (`samples[]`).",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
