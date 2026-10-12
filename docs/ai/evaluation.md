# Document AI — Evaluation Design & Baseline Status

Status: **measured; thresholds FINAL** (PRD §5 floor met: 50 labeled samples
per quality category, 400 total, `RESULTS.json` → `metadata.floor_met=true`).
The dataset is synthetic (rendered templates, exact GT) — the floor is met for
*measured* behavior under controlled degradation, not for real-world receipts;
see the caveats below.

## Canonical evaluation system

- Package: `backend/ai_eval/` (see `backend/ai_eval/README.md`)
- Dataset: `backend/ai_eval/dataset/` — synthetic receipts rendered from
  three retailer templates; exact ground truth captured at generation time;
  deterministic seeds (regenerate → identical manifest).
- Categories (PRD §5): `clear`, `faded`, `low_contrast`, `crumpled`, `skewed`,
  `shadowed`, `water_damaged`, `retailer_formats`.
- Measurement: runs the **production** pipeline stages per sample (quality →
  enhancement → OCR pass 1 → OCR pass 2 → better-page selection → structured
  extraction), then scores:
  - Text: CER, WER (`app.ai.evaluation.metrics`)
  - Extraction: exact match, precision, recall, F1 (per-field normalized:
    ISO dates, numeric money ±0.005, casefolded text, upper-cased currency)
- Results: `backend/ai_eval/RESULTS.json` + `backend/ai_eval/RESULTS.md`
  (machine- and human-readable; per-sample detail in the JSON).

Run:

```bash
cd backend
.venv/bin/python -m ai_eval.generate_dataset --per-category 5
.venv/bin/python -m ai_eval.run_evaluation
```

## Threshold policy (SRS ML-05 / PRD §5)

Confidence thresholds become **final** once ~50 labeled samples per quality
category exist. That floor is now met (50/category, 400 total).
`app/ai/registry.py::thresholds_provisional()` is data-driven: it reads
`metadata.floor_met` from `ai_eval/RESULTS.json` (missing artifact → True,
conservative). `RESULTS.md` repeats the floor check for each run.

## Legacy baseline (retained as "before" evidence)

An earlier phase recorded a baseline in `backend/data/evaluation/` (27
synthetic samples, single-pass OCR): CER 0.0985 / WER 0.2338 and extraction
P 0.7724 / R 0.5026 / F1 0.609 with a 100% review-rate proxy. Its generator
and runner scripts were superseded by `backend/ai_eval/` (which adds exact
match, the production two-pass flow, and the floor check) and removed; the
recorded result JSONs are kept as-is. The current measured baseline in
`ai_eval/RESULTS.md` supersedes them for all decisions.

## What is synthetic, and why that is acceptable for now

The dataset is synthetic by construction (rendered templates, exact GT). It
makes the pipeline's behavior under controlled degradations measurable with
real numbers — the prerequisite the PRD demands before thresholds are final.
It is **not** a substitute for real labeled receipts: the floor is met for
*measured pipeline behavior under controlled degradation*, and validating the
thresholds on a corpus of real (or realistically degraded) receipts remains
the follow-up work. No benchmark number in this system is invented; every
figure is a measurement from a recorded run.
