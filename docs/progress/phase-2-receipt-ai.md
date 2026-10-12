# Phase 2 — Receipt AI (complete)

**Status:** ✅ Complete & verified

## Deliverables (PRD Phase 2)

| Deliverable | Status | Notes |
| --- | --- | --- |
| Document classification | ✅ | Rule-based keyword/structural classifier: RECEIPT / INVOICE / WARRANTY_CARD / PURCHASE_ORDER / UNKNOWN, per-type scores + confidence, stored on the document and the extraction job |
| Field extraction | ✅ | Schema-constrained rule-based extraction (8 canonical fields) over real Tesseract OCR text; every value carries confidence + provenance (method, model, version, source page/region) |
| Model registry | ✅ | `app/ai/registry.py` — names/versioned models actually used (OCR engine, classifier, extractor, enhancer); endpoints expose registry entries |
| Evaluation metrics | ✅ | `app/ai/evaluation/metrics.py` — precision / recall / F1 over extracted vs. ground-truth fields |
| Human-in-the-loop field review | ✅ | `GET/PATCH /documents/{id}/fields` — confirm / correct / reject per field; `USER_CONFIRMED` / `USER_CORRECTED` / `REJECTED` with original value preserved; document status → `REVIEW_REQUIRED` |
| Classification & fields API | ✅ | `GET /documents/{id}/classification`, `GET /documents/{id}/fields`, `PATCH /documents/{id}/fields` |
| Purchases by document | ✅ | `GET /purchases?document_id=N` filter + purchase prefill from confirmed fields |
| Frontend field-review UI | ✅ | Field panel with confidence bands, verification badges, inline confirm/correct/reject, provenance line, classification banner, original/enhanced preview toggle, "Create purchase from receipt" prefill |
| Pipeline version bump | ✅ | `pipeline_version` 1.1.0 (config + `.env.example`), stamped on jobs, status, and fields |
| No fake AI | ✅ | Everything is real: real Tesseract OCR, real rule-based scoring, honest confidence scores. No canned OCR text, no canned warranty/claim results |

## How it works

**Classification** (`app/ai/classification/classifier.py`): per-type weighted keyword + structural-line scoring against OCR text. A document is `UNKNOWN` when the best score is below threshold (3) or the margin between 1st and 2nd place is under 15% of total; `UNKNOWN` confidence is capped at 0.4. Live run on the E2E receipt: `RECEIPT` @ 0.778 with scores `{RECEIPT: 7, INVOICE: 2, WARRANTY_CARD: 0, PURCHASE_ORDER: 0}`.

**Extraction** (`app/ai/extraction/extractor.py`): keyword + pattern + proximity + dictionary + positional heuristics per field, each with its own confidence model. Live run extracted 8 fields, e.g. `total_price 1403.98 @ 0.95 (keyword+pattern)`, `purchase_date 2026-08-01 @ 0.92 (pattern)`, `product_name "Nortech 55in 4K QLED TV" @ 0.62 (positional-heuristic)`, `unit_price 55 @ 0.68 (proximity)` — the `55` is an honest heuristic false-positive ("55in" display size), exactly the kind of imperfect value the human-review loop exists to catch.

**Review loop**: fields start as `AI_EXTRACTED`; user actions PATCH the field (`confirm` → `USER_CONFIRMED`, `correct` → `USER_CORRECTED` with new value + confidence 1.0 + method `user`, `reject` → `REJECTED`). When the AI had a value, `original_value` is preserved for audit. The document status becomes `REVIEW_REQUIRED` whenever any critical field awaits review.

**Frontend confidence bands** (SDD §8, provisional until calibrated): ≥ 0.9 auto-accept (green), ≥ 0.7 user review (amber), < 0.7 manual (red). Each row shows `source region · method · model@version` and, after correction, "AI said: <s>original</s>".

**Purchase prefill**: "Create purchase from receipt" maps extracted fields → purchase (total, tax, subtotal = total − tax, purchase date, one line item from product_name/sku/model_number/serial_number/quantity/unit_price) and navigates to the new purchase.

## Files created (Phase 2)

Backend:
- `backend/app/ai/classification/__init__.py`
- `backend/app/ai/classification/classifier.py`
- `backend/app/ai/registry.py`
- `backend/app/ai/evaluation/__init__.py`
- `backend/app/ai/evaluation/metrics.py`
- `backend/app/schemas/extraction.py`
- `backend/tests/ai/test_classifier.py`
- `backend/tests/ai/test_metrics.py`
- `backend/tests/ai/test_registry.py`
- `backend/tests/api/test_fields.py`

Frontend:
- `frontend/src/components/FieldReview.tsx`
- `frontend/tests/field-review.test.tsx`

## Files modified (Phase 2)

Backend:
- `backend/app/config.py` — `pipeline_version` → 1.1.0
- `backend/.env.example` — `PIPELINE_VERSION=1.1.0`
- `backend/app/ai/pipeline.py` — classification + extraction wired into the document pipeline; provenance stamped on fields; status → `REVIEW_REQUIRED`
- `backend/app/services/extraction_service.py` — `list_fields`, `correct_field`, `_get_owned_document` (404 on other users' docs)
- `backend/app/api/v1/documents.py` — `GET/POST /{id}/fields` (PATCH), `GET /{id}/classification`
- `backend/app/api/v1/purchases.py` — `document_id` list filter
- `backend/tests/api/test_purchases.py` — added `test_filter_by_document_id`

Frontend:
- `frontend/src/pages/DocumentDetail.tsx` — Original/Enhanced preview toggle, field-review section
- `frontend/src/index.css` — `.chip` utility class

## Verification

| Suite | Result |
| --- | --- |
| Backend pytest | **131 / 131 passed** (91 pre-Phase-2 + 40 new) |
| Backend ruff | clean |
| Backend mypy | clean |
| Frontend vitest | **12 / 12 passed** (incl. 3 new field-review tests) |
| Frontend tsc / build | clean, `vite build` OK |
| Live E2E (API + worker) | upload → worker (pipeline 1.1.0, OCR conf 94.24) → 8 fields → classification RECEIPT → confirm/correct/reject all persisted → purchase prefilled + `?document_id=` filter OK |

Live E2E account: `demo@warrantyvault.dev` / `Passw0rd!123` (recreated after env rebuild).

## Notes & honest limitations

- Classifier and extractor are **rule-based by design** (no trained models in scope); confidences are heuristic scores labeled with `method` + `model@version` provenance so they can be calibrated/replaced later. Confidence bands in the UI are marked provisional in code comments.
- `f1` on a no-positives evaluation follows the suite-wide "no data" convention and returns 1.0 (documented in `test_extraction_eval_mismatch_counts_as_fp_and_fn`).
- Field PATCH validation errors return **422** (`VALIDATION_ERROR`), consistent with the AppError status map.
- PDF page rendering requires `poppler-utils` in the environment (`pdftoppm`); PNG/JPG previews don't need it.
