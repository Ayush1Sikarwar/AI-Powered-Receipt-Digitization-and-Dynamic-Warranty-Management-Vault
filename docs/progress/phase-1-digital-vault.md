# Phase 1 — Digital Vault (complete)

**Status:** ✅ Complete & verified

## Deliverables (PRD Phase 1)

| Deliverable | Status | Notes |
| --- | --- | --- |
| Upload API | ✅ | `POST /api/v1/documents` (multipart, rate-limited 20/h, async — never synchronous OCR) |
| Object storage | ✅ | Provider interface: LocalFS (dev) + S3 (prod, boto3). Categories `original/ processed/ enhanced/ thumbnails/ claims/` (TSD §35). Keys are UUID-based — **no user-controlled paths**; traversal guarded |
| Document metadata | ✅ | `documents` + `document_pages` + `document_derivatives`; sha256, MIME, size, page count, status, immutable original |
| Purchase records | ✅ | `purchases` + `purchase_items`; manual creation, region resolution (FR-11a), verification status, soft delete |
| Dashboard | ✅ | `GET /dashboard/summary` + UI (totals, processing, review-required, recent docs; warranty buckets populate in Phase 4) |
| Search | ✅ | `GET /purchases` with `q` (product/model/SKU/invoice), `manufacturer`, `retailer`, `date_from/to`, `warranty_status`, sort + pagination (SRS FR-14, TSD §54/§55) |

## Security & integrity implemented

- **Upload validation (TSD §36):** extension allowlist, magic-byte signature validation (MIME header never trusted), size limits, filename sanitization (traversal stripped), empty-file rejection
- **Malware scanning:** always-on heuristic scanner (executable/OLE/ZIP signatures, PDF-embedded-JavaScript/launch detection) + ClamAV adapter when a daemon is configured; scanner used is recorded (honest state)
- **Authorization:** every document/purchase access verifies ownership; foreign IDs return 404 (no existence leak) — IDOR tested
- **Retention holds (TSD §37):** deleting a document/purchase referenced by a non-terminal claim returns an explicit HOLD with the condition; no silent failure
- **Audits:** DOCUMENT_UPLOADED, DOCUMENT_DELETED, PURCHASE_CREATED/UPDATED/DELETED with request_id + IP
- **Async processing:** `background_jobs` table; worker polls with `FOR UPDATE SKIP LOCKED`; bounded retries (3) → DEAD; idempotency keys prevent duplicate jobs (TSD §31)
- **Pipeline:** render (PIL/pdf2image) → quality score (contrast/sharpness/illumination) → thumbnail derivatives → COMPLETED; originals never mutated; PDFs up to 20 pages

## Verification performed

- ✅ 69 backend tests pass (new: storage keys/traversal, file validation, region resolution, pipeline, documents API incl. async job enqueue + full in-process pipeline flow + IDOR + hold, purchases API incl. search/hold, upload security: exe/zip/MIME-spoof/disguised-executable/traversal/oversize/rate-limit)
- ✅ 9 frontend tests pass (API client, Login, Upload validation incl. no-network-on-bad-type, Documents list)
- ✅ `ruff` clean, `mypy` clean (62 files), `tsc` clean
- ✅ Migration `0002_digital_vault` applied (11 new tables); test suite rebuilds schema from Alembic
- ✅ Live exercise: PNG + 2-page PDF uploads → worker processing → COMPLETED + thumbnails; authorized download (401 unauthenticated); purchase create with region resolution (USER_ACCOUNT=IN); search by model; dashboard; email-status/system status; account close (90-day purge, login blocked after)
- ✅ Live security exercise: .exe/.zip rejected, text-as-PNG rejected by signature, `../../etc/passwd.png` stored as `passwd.png`, cross-user read/delete → 404

## Honest limitations

- Warranty dashboard buckets are real queries but report 0 until Phase 4 maps coverage (no fabricated numbers).
- `warranty_status` search filter is wired and validated; it matches real coverage data from Phase 4.
- Document *classification* (receipt/invoice/warranty-card, FR-03) and OCR/extraction arrive in Phase 2; documents default to UNKNOWN type until then.
- CLEANUP worker (purge of soft-deleted objects per retention policy) is Phase 6; soft-delete + holds are real now.
