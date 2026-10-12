# Document AI Pipeline — Controlled Evaluation (PRD §5–6, TSD §41–43)

This package implements the PRD's **controlled evaluation dataset** and the
metric scripts. Its purpose is to make the pipeline's baseline **measurable
with actual numbers** so confidence thresholds can be calibrated against
evidence instead of invented benchmarks.

**Canonical location.** This is the single evaluation home for the document
AI pipeline. A legacy precursor (`backend/scripts/run_*_evaluation.py` +
`data/evaluation/`) existed in an earlier phase; its recorded baseline
results are retained in `data/evaluation/results/` as "before" evidence for
the extractor improvements, but the scripts were superseded by this package
and removed (they lacked extraction *exact match* and measured single-pass
OCR instead of the production two-pass flow).

## What is honest here, and what is not

- **The dataset is synthetic.** Receipts are rendered from three retailer
  templates (Nortech Home Appliances, CityMart Grocers, BlueBox Electronics)
  with exact ground truth captured **at generation time**. Ground truth is
  therefore exact by construction — there is no labeling noise, which is a
  property of synthetic data, not a claim about real receipts.
- **Volume meets the PRD floor.** The PRD requires ~50 labeled samples per
  quality category before confidence thresholds may be treated as final. The
  dataset is generated with `generate_dataset.py --per-category 50`
  (50 per quality category, 400 total; the `retailer_formats` category cycles
  the three retailer templates across 50 samples). `RESULTS.json` records
  `metadata.floor_met = true`, so per the PRD the confidence thresholds are
  **final** — `RESULTS.md` and the model registry (`thresholds_provisional()`,
  which now reads the measured `floor_met` flag) both report that state.
  Re-run `run_evaluation.py` after regenerating; the flags follow the data.
- **The measurements are real.** Every number in `RESULTS.json` / `RESULTS.md`
  is produced by running the *production* pipeline stages (quality →
  enhancement → OCR pass 1 → OCR pass 2 → better-page selection → structured
  extraction) over each sample.

## Categories (PRD §5)

`clear`, `faded`, `low_contrast`, `crumpled`, `skewed`, `shadowed`,
`water_damaged`, plus `retailer_formats` (one crisp sample per retailer
template). Degradations are deterministic (seeded per sample) and
reproducible: regenerate → identical manifest.

Per-sample ground truth (PRD schema + extensions): `document_id`, `image`,
`ground_truth_text`, `retailer`, `purchase_date`, `product`, `sku`, `price`,
`currency`, `quality_category`, plus `quantity`, `total`, `tax`, `model`,
`serial`, `receipt_number`, `retailer_format`, and the extractor-keyed
`fields` dict used for field-level evaluation.

Conventions (documented to keep the evaluation fair):

- Multi-item receipts (CityMart) carry **no** `product_name` / `unit_price`
  ground truth — "primary product" is ambiguous by construction; the
  extractor's output on those fields is *exempt* from false-positive counting
  (listed per sample in `exempt_fields`).
- No currency ground truth where the document prints no currency symbol/code
  (CityMart) — nothing exists to extract with evidence.
- Receipt/invoice numbers are evaluated against the extractor's single
  `invoice_number` field (its pattern covers `Receipt No:` / `Invoice No:`).

## Running

```bash
# from backend/
.venv/bin/python -m ai_eval.generate_dataset --per-category 5
.venv/bin/python -m ai_eval.run_evaluation            # full run (~75s)
.venv/bin/python -m ai_eval.run_evaluation --limit 3  # smoke
.venv/bin/python -m ai_eval.run_evaluation --category shadowed
```

Outputs: `dataset/manifest.json`, `dataset/images/<category>/*.png`,
`RESULTS.json` (machine-readable, per-sample + per-category), `RESULTS.md`
(human-readable, with caveats and the PRD floor check).

## Metrics (PRD §6)

- Text: **CER** (character error rate), **WER** (word error rate) — via
  `app.ai.evaluation.metrics` (same primitives the app uses).
- Extraction: **exact match** (all GT fields correct), **precision / recall /
  F1** over (GT fields, extracted fields) with per-field normalization
  (ISO dates, numeric money ±0.005, casefolded text, upper-cased currency).

## Tests

`tests/ai/test_evaluation.py` covers dataset structure (PRD fields, all
categories, synthetic labeling), generation determinism, per-sample metric
validity, and result-file output including the provisional-threshold flag.
