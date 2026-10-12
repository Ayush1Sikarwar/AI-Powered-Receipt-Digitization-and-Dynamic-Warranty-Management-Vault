# Digital Vault — Format & Failure Verification (complete)

**Status:** ✅ Complete & verified

Task: "Implement the complete Digital Vault" (JPG/JPEG/PNG/WEBP/PDF upload,
object storage, document lifecycle, purchases, frontend, cross-user
isolation, and a specific test matrix including storage/database failures).

## Audit result: the vault was already real (Phases 0–1)

| Brief item | State | Where |
| --- | --- | --- |
| Secure upload, size limits, rate limit (20/h) | ✅ real | `POST /documents` + `file_validation` |
| File validation (extension allowlist + magic-byte signature; declared MIME never trusted; extension↔content cross-check) | ✅ real | `validate_upload` |
| Filename sanitization (traversal stripped, NFKC, 120 chars) | ✅ real | `sanitize_filename` |
| Storage key generation (UUID-based, `original/` category, no user-controlled paths) | ✅ real | `storage_key` (TSD §35) |
| Checksum + metadata persistence | ✅ real | sha256, mime, size, page count on `documents` |
| Object-storage abstraction + real dev storage | ✅ real | `ObjectStorage` → `LocalFSObjectStorage` (dev) / `S3ObjectStorage` (prod, boto3) — **no fake storage in the application path** |
| Originals immutable; processing never overwrites | ✅ real | derivatives always get fresh UUID keys under `enhanced/` `thumbnails/`; verified below |
| List / detail / status / pages / derivatives | ✅ real | `GET /documents`, `/{id}`, `/status`, detail incl. pages, `/derivatives` |
| Delete workflow (soft delete + open-claim HOLD + deferred purge) | ✅ real | `DELETE /documents/{id}` |
| Secure retrieval / authorized access | ✅ real | Bearer-authorized streaming; storage keys never exposed; 401 unauth, 404 foreign |
| Purchases + purchase items | ✅ real | full CRUD, region resolution, soft delete, search |
| Frontend (upload + progress, library, details, processing status, purchases, search) | ✅ real | Phase 0/1 UI; suites green |

No application code was missing — the gap was the **test matrix** demanded by
the brief. This phase closed it.

## New tests (12)

**`tests/api/test_vault_formats.py` (9):**

| Brief requirement | Test | Result |
| --- | --- | --- |
| Valid files — image | `test_png_upload_and_retrieval` (upload → sha256 match → download bytes identical) | ✅ |
| Valid files — JPG/JPEG | `test_jpg_upload_and_retrieval` (real JPEG, byte-identical retrieval) | ✅ |
| Valid files — WEBP | `test_webp_upload_and_retrieval` (real WEBP, byte-identical retrieval) | ✅ |
| Valid files — PDF | `test_pdf_upload_processing_and_retrieval` (real renderable PDF → pipeline COMPLETED, 1 page, original PDF retrievable byte-identical, enhanced derivative 401 unauth) | ✅ |
| Invalid extensions | `test_invalid_extension_rejected` (`.txt`, `.html` → 400 FILE_ERROR with allowed list) | ✅ |
| Invalid MIME / mismatch | `test_mime_extension_mismatch_rejected` (PNG content as `.pdf` → 400) + pre-existing `mime_spoofing_rejected`, `disguised_executable_rejected` | ✅ |
| Corrupted files | `test_corrupted_image_graceful_failure` (valid PNG signature, truncated body → upload accepted, processing fails safely: document `FAILED` + `PROCESSING_ERROR`, evidence still retrievable) | ✅ |
| Oversized files | pre-existing `test_oversized_upload_rejected` (15 MB limit) | ✅ |
| Duplicate hash behavior | `test_duplicate_hash_allowed` (same content twice → two documents, same sha256, both retrievable — dedup is deliberately not claimed) | ✅ |
| Original never overwritten | `test_original_never_overwritten_during_processing` (storage key + bytes + sha256 unchanged after full pipeline; THUMBNAIL/ENHANCED under distinct keys in their own categories) | ✅ |

**`tests/api/test_vault_failures.py` (3):**

| Brief requirement | Test | Result |
| --- | --- | --- |
| Storage failure | `test_storage_failure_safe_error_no_partial_state` (storage outage → 500 INTERNAL_ERROR envelope with request_id, **no stack trace/exception text**, no document row, no new job) | ✅ |
| Database failure | `test_database_failure_safe_error_no_partial_state` (commit outage → 500 safe envelope, no document row) | ✅ |
| Recovery | `test_healthy_upload_still_works_after_failures` (post-failure upload succeeds end-to-end) | ✅ |

Pre-existing coverage retained and re-verified: executable/zip/OLE rejection,
path-traversal sanitization, upload rate limit, IDOR (foreign read/delete →
404), unauthorized download (401), deletion + retention HOLD, full in-process
processing flow, malformed-error hygiene.

## Verification

| Suite | Result |
| --- | --- |
| Backend pytest | **166 / 166 passed** (154 + 12 new) |
| ruff / mypy | clean |
| Frontend vitest + tsc | 12/12, clean |
| Live E2E (real API + worker) | all 5 formats uploaded → worker-processed → **retrieved bytes identical to uploaded bytes**, original storage objects verified intact after processing, unauth 401 / other-user 404, list total=5, search by filename → 1, PDF derivatives ENHANCED+THUMBNAIL |

## Files created

- `backend/tests/api/test_vault_formats.py`
- `backend/tests/api/test_vault_failures.py`

## Files modified

- None. The vault implementation already satisfied the brief; only the
  verification layer was added.

## Notes & honest observations

- The upload API (and derivatives API) deliberately **never exposes storage
  keys** (TSD §35); tests read keys from the DB to verify immutability.
- `storage_key`/`sha256` are part of the document payload's metadata
  persistence; the *object key* stays server-side.
- Corrupted files with a valid signature are accepted at upload (storage is
  format-agnostic — the evidence must be kept) and fail gracefully at
  processing with a user-visible error state, per the failure path in
  `run_document_pipeline`.
- PDF uploads are capped at 20 pages and scanned for embedded JavaScript /
  launch actions; images are scanned for executable/archive signatures.
