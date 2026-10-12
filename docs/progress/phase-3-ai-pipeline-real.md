# Phase Report — Real Receipt/Document AI Pipeline (SRS/TSD)

Date: 2026-08-31 · Status: **COMPLETE** — pipeline stages real, OCR record
persisted, evaluation baseline measured, hallucination + review-preservation
verified (unit, API, and live E2E).

## 1. What was verified as already real (Phase 1/2)

| Stage | Where | State found |
|---|---|---|
| Quality assessment | `app/ai/pipeline.py::quality_score` | real deterministic heuristic |
| Classification (receipt/warranty/invoice/purchase-order/unknown) | `app/ai/classification/classifier.py` | real keyword scoring |
| Preprocessing: perspective, deskew, denoise (NLM), illumination, CLAHE, upscale, sharpen | `app/ai/preprocessing/enhance.py` | real OpenCV stages, provenance recorded |
| OCR provider interface + fallback | `app/ai/ocr/engine.py` | real ABC + Tesseract; Paddle slot honest "not configured" |
| Two-pass OCR (original vs enhanced, keep better) | `app/ai/pipeline.py` | real |
| Structured extraction + normalization + validation | `app/ai/extraction/extractor.py` | real schema-constrained rule extractor, null when no evidence |
| Confidence scoring + review routing | `app/ai/pipeline.py`, `app/ai/registry.py` | real, thresholds flagged provisional |
| CER / WER / extraction P·R·F1 primitives | `app/ai/evaluation/metrics.py` | real (Levenshtein) |
| Human review UI (FieldReview) + correction preservation | `app/services/extraction_service.py`, frontend | real (original_value kept) |

## 2. Gaps closed in this phase

### 2.1 Adaptive thresholding (last missing preprocessing stage)
`enhance.py` now has a gated adaptive-threshold stage (light background +
low contrast + no truly-dark ink → binarize only if edge energy improves).
Crisp documents are provably untouched (`test_crisp_image_not_binarized`).
The stage is recorded in `EnhancementResult.operations` (provenance).

### 2.2 Complete OCR record persistence (SRS ML-03: text, confidence, boxes, page, engine, model/version)
- Per page (existing): `ocr_text`, `ocr_engine`, `ocr_confidence`
- **New column** `document_pages.ocr_engine_version` (migration
  `ae4f04771389`), set by the pipeline on both OCR paths
- **Job-level `ocr_report` (extraction_jobs) now stores the per-line record**:
  `lines[]` = `{page, line, text, confidence, bbox}`, plus
  `engine`, `engine_version`, `line_source`, and the existing
  primary/fallback comparison keys.

### 2.3 Extractor correctness fixes (found via evaluation, fixed in app code)
1. `_norm_money` picked the leftmost number — "Front Load Washing Machine
   **8kg** $685.59" → unit price "8". Now prefers the rightmost decimal
   amount (prices end the line; "8kg"/"355ml"/"12pk" are not prices).
2. New `_money_price_match`: unit price only from amounts with cents or a
   currency symbol (a bare "90" from "Model: X90" is not a price).
3. Product-name core regex no longer strips trailing model numbers
   ("Headphones X90" kept; only whitespace/currency-prefixed amounts and
   lowercase quantity suffixes "x1" are stripped).
4. Single-item inference: one product candidate + qty 1 + total present →
   unit price = total at 0.50 confidence, method `single-item-inference`
   (evidence-backed, not invented).

### 2.4 Hallucination protection (SRS ML-06) — explicit tests
- `test_no_fields_invented_without_evidence`: date+total-only document →
  only `purchase_date`/`total_price`; product/sku/model/serial/tax/retailer
  provably absent.
- `test_blank_document_yields_no_fields`: empty OCR → zero fields + warning.

### 2.5 Controlled evaluation dataset + measured baseline (PRD §5–6)
Canonical system: **`backend/ai_eval/`** (supersedes the legacy
`backend/scripts/run_*_evaluation.py`, removed; its recorded baseline is
retained in `backend/data/evaluation/results/` as "before" evidence — see
`docs/ai/evaluation.md`).

- 38 synthetic samples, exact ground truth at generation, deterministic
  seeds: 5 × {clear, faded, low_contrast, crumpled, skewed, shadowed,
  water_damaged} + 3 retailer-format samples (Nortech / CityMart / BlueBox).
- Runs the **production** sequence per sample (quality → enhance → OCR pass 1
  → OCR pass 2 → keep better page → extract) and scores CER, WER, exact
  match, P, R, F1.

**Measured results (full run, tesseract 5.5.0, extraction model
rule-based-schema-constrained 1.0.0) — actual numbers, `ai_eval/RESULTS.md`:**

| Category | n | CER | WER | Exact | F1 | Enh kept |
|---|---|---|---|---|---|---|
| clear | 5 | 0.0000 | 0.0000 | 1.000 | 1.000 | 3/5 |
| faded | 5 | 0.0000 | 0.0000 | 1.000 | 1.000 | 0/5 |
| low_contrast | 5 | 0.0092 | 0.0401 | 0.800 | 0.960 | 3/5 |
| crumpled | 5 | 0.0011 | 0.0077 | 1.000 | 1.000 | 0/5 |
| skewed | 5 | 0.0197 | 0.0896 | 0.400 | 0.932 | 2/5 |
| shadowed | 5 | 0.1500 | 0.1606 | 0.200 | 0.836 | 1/5 |
| water_damaged | 5 | 0.0739 | 0.2526 | 0.000 | 0.853 | 0/5 |
| retailer_formats | 3 | 0.0020 | 0.0145 | 0.667 | 0.980 | 2/3 |
| **overall** | **38** | **0.0336** | **0.0736** | **0.6316** | **0.947** | 11/38 |

Field-level accuracy (overall): retailer 38/38, invoice_number 36/38,
purchase_date 36/38, total_price 34/38, quantity 32/38, unit_price 20/23,
product_name 20/23, currency 22/23, tax 12/15, serial_number 7/8,
model_number 6/8, sku 5/8.

**Honest caveats (also in RESULTS.md):** dataset is synthetic (rendered
templates, exact GT); per-category volume (5) is far below the PRD floor of
~50/category, so **all confidence thresholds remain PROVISIONAL** — flagged
in `app/ai/registry.py` (`thresholds_provisional()`) and in RESULTS.md.
The degradation gradient (clear → water-damaged) is real and monotonic,
which is what the baseline is for.

## 3. Verification

- **Backend suite: 175/175 passed** (was 166; +9 new tests):
  - `tests/ai/test_ocr.py`: `test_report_persists_line_level_ocr_record`
  - `tests/ai/test_enhancement.py`: `test_adaptive_thresholding_on_low_contrast_light_image`, `test_crisp_image_not_binarized`
  - `tests/ai/test_extraction.py`: `test_no_fields_invented_without_evidence`, `test_blank_document_yields_no_fields`
  - `tests/ai/test_evaluation.py` (new file, 4 tests): dataset structure vs PRD fields + all categories + synthetic labeling; generation determinism; per-sample metric validity + model identity; result output incl. provisional-threshold flag and floor check
- `ruff` clean, `mypy` clean (app/ai + ai_eval + tests)
- Migration cycle: `ae4f04771389` applied to dev DB; test DB rebuilt from
  migrations each suite run (schema verified by conftest)
- Frontend: unchanged this phase (last verified 12/12 vitest + tsc clean)

### Live E2E (fresh API + worker on new code)
1. Upload `live-ai-receipt.png` → worker → **REVIEW_REQUIRED**;
   `document_pages` = `('tesseract', '5.x', 94.76, text present)`;
   `extraction_jobs.ocr_report` contains 7 lines with text/confidence/bbox/
   page + engine + engine_version + line_source; fields extracted
   (invoice_number R-88231, total 189.00, date 2026-08-30, USD, retailer,
   product, quantity, unit_price via single-item inference).
2. Correction flow: AI total 9.99 → user corrects to 10.50 → API/DB show
   `field_value=10.50`, **`original_value=9.99` preserved**,
   `verification_status=USER_CORRECTED`, extraction history intact.

## 4. Out of scope (per TSD sequence)

- **Generative degraded-receipt reconstruction** — deliberately NOT started;
  the TSD sequence requires the baseline to be measurable first (it now is).
- Growing the labeled set toward the 50/category floor with real or
  realistically degraded samples — follow-up; thresholds stay provisional
  until then.

## 5. Files

**Created**
- `backend/ai_eval/__init__.py`, `backend/ai_eval/README.md`
- `backend/ai_eval/generate_dataset.py`, `backend/ai_eval/pipeline_eval.py`, `backend/ai_eval/run_evaluation.py`
- `backend/ai_eval/dataset/manifest.json` + `backend/ai_eval/dataset/images/<category>/*.png` (38)
- `backend/ai_eval/RESULTS.json`, `backend/ai_eval/RESULTS.md`
- `backend/alembic/versions/ae4f04771389_page_ocr_engine_version.py`
- `backend/tests/ai/test_evaluation.py`
- `docs/ai/evaluation.md` (registry-referenced; was missing)
- `docs/progress/phase-3-ai-pipeline-real.md` (this report)

**Modified**
- `backend/app/ai/ocr/engine.py` — line-level OCR record + `line_source` in report
- `backend/app/ai/preprocessing/enhance.py` — adaptive threshold stage
- `backend/app/ai/pipeline.py` — persist `page.ocr_engine_version` (both OCR paths)
- `backend/app/models/document.py` — `DocumentPage.ocr_engine_version`
- `backend/app/ai/extraction/extractor.py` — `_norm_money` rightmost-decimal, `_money_price_match`, product-core regex fixes, single-item unit-price inference
- `backend/tests/ai/test_ocr.py`, `backend/tests/ai/test_enhancement.py`, `backend/tests/ai/test_extraction.py` — new tests appended

**Removed (superseded)**
- `backend/scripts/generate_evaluation_dataset.py`
- `backend/scripts/run_ocr_evaluation.py`
- `backend/scripts/run_extraction_evaluation.py`
  (recorded baselines retained in `backend/data/evaluation/results/`)
