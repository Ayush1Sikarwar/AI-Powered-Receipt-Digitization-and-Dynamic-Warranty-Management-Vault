# Phase Report — Complete MVP Claims Workflow

Date: 2026-09-04 · Status: **COMPLETE** — the full claims workflow is
implemented: claim creation, status (DRAFT → READY), issue description,
evidence selection (stored documents + uploaded media), receipt & warranty
evidence, pre-filled package with per-item provenance, claim-ready report,
portable evidence bundle, and user data export (JSON + ZIP). The MVP stops
at `CLAIM_READY` (TSD §57) — there is no submission path, and the boundary
is stated inside the artifact itself. The complete generation flow is
tested against a **real stored test document**. All verification green.

## 1. What was built

### 1.1 Claim model & status (`app/models/claim.py`)

- `Claim`: status (`DRAFT/READY/SUBMITTED_EXTERNALLY/IN_PROGRESS/RESOLVED/
  CLOSED` — terminal: RESOLVED/CLOSED), issue description + category,
  contact fields, notes, stored package JSON + `package_generated_at`.
- `ClaimFile`: uploaded evidence (sha256, mime, size).
- **`ClaimDocument` (new, migration `4b8e2c6d9a17_0011_claim_documents`)**:
  a stored vault document selected as claim evidence with a declared role
  `RECEIPT | WARRANTY | EVIDENCE` — evidence selection without re-uploading.
  One attachment per (claim, document); re-attach updates the role.

### 1.2 Claim package v1.1 (`app/services/claim_service.py`)

Pre-filled, **every item carries a provenance/trust state** (TSD §33/§42):

| Section | Provenance |
| --- | --- |
| Purchase block | from `Purchase.verification_status` (DRAFT ⇒ INFERRED, AI ⇒ AI_EXTRACTED, confirmed ⇒ USER_CONFIRMED, partial ⇒ USER_CONFIRMED + note) |
| Items (model/SKU, **serial number**, qty, prices) | USER_CONFIRMED (user-entered) |
| Issue description / contact | USER_CONFIRMED |
| Warranty policy + source | **EXTERNAL_SOURCE** (source type + trust level/5) |
| Timelines (start/expiry/state) | **COMPUTED** (deterministic) |
| Match (method/confidence/status) | AI_EXTRACTED or USER_CONFIRMED; **REJECTED matches never assert coverage** |
| Receipt (stored document) | attachment USER_CONFIRMED; **each extracted field** keeps its own state (AI_EXTRACTED / USER_CONFIRMED; rejected fields excluded) |
| Warranty documents / attached docs | USER_CONFIRMED (user-selected) |
| Evidence (uploaded media) | USER_CONFIRMED + `media_kind` (PHOTO/VIDEO/DOCUMENT/OTHER) |

Package also carries: `submission` boundary block (mode `CLAIM_READY`,
`external_submission: false`, explicit note — no automatic manufacturer
submission, **never bypasses CAPTCHA / MFA / anti-bot controls / access
restrictions**, TSD §57), the `disclaimer`, and the rendered
**claim-ready report** (markdown) embedded as `report`.

### 1.3 Deliverables (API, TSD §23)

- `POST /claims/{id}/package` — generate/regenerate (DRAFT → READY).
- `GET /claims/{id}/package` / `…/package/download` — JSON.
- `GET /claims/{id}/package/report` — **claim-ready report** (markdown).
- `GET /claims/{id}/package/bundle` — **portable evidence bundle** (ZIP):
  `package.json`, `claim-report.md`, `README.txt` (sha256 manifest),
  `evidence/*` (uploaded files, byte-exact), `documents/*` (receipt +
  attached documents).
- `POST/GET/DELETE /claims/{id}/documents` — evidence selection.
- `PATCH /claims/{id}` — editable fields only; the schema has **no status
  field**, so the API cannot move a claim past READY.
- **No submission endpoint exists** (verified in tests/e2e: 404).

### 1.4 User data export (`app/api/v1/account.py`)

- `GET /account/export` — portable JSON (profile, documents + extracted
  fields, purchases + warranty, claims + evidence, notifications, audit).
- `GET /account/export/bundle` (new) — the same payload as `manifest.json`
  **plus the actual file bytes** (stored documents + claim evidence) in a
  self-verifying ZIP, so warranty records and supporting evidence export
  together in one portable archive.

### 1.5 Frontend

- `ClaimDetail.tsx`: every package section renders `ProvenanceChip`s —
  **Verified / AI Extracted / User Confirmed / External Source / Inferred /
  Computed** are visually distinct (AI-inferred data can never be mistaken
  for a verified fact). New: receipt section with per-field provenance,
  attached/warranty documents, media-kind badges, a claim-ready-only
  boundary notice, and download buttons (JSON / report / bundle ZIP).
  **Evidence selection UI**: pick a stored document + role (Receipt /
  Warranty document / Supporting evidence), attach/remove.
- `Settings.tsx`: "Your data" card — Export data (JSON) + Export bundle (ZIP).
- `types/claims.ts`, `services/claims.ts`: provenance types, new
  endpoints, blob downloads.

## 2. Verification

### 2.1 Backend — `tests/api/test_claims_full.py` (9 tests)

Package provenance states (INFERRED purchase, USER_CONFIRMED items/fields,
EXTERNAL_SOURCE policy, COMPUTED timelines, per-field receipt provenance,
rejected fields excluded), purchase confirmation flips provenance,
stored-document selection (attach/detach/idempotent re-attach, foreign
document 404, bad role 422, audit), bundle ZIP contents (byte-exact files,
sha256 in README, embedded package == API package, 409 before
generation), report markdown, PATCH with no status field, MVP boundary
(no submit/external-submission/status endpoints; boundary block in
package), export bundle (manifest + files).

- New suite: **9/9** · full backend: **317/317 passed** · ruff + mypy clean
  on all changed files.

### 2.2 Frontend

- New `tests/claims-package.test.tsx` (3 tests): every provenance state
  rendered distinctly + boundary note + download buttons; document
  selection posts the right role; Settings export buttons hit the right
  endpoints.
- Full vitest: **41/41 passed** (11 files) · `tsc --noEmit` clean.

### 2.3 E2E — real stored test document (`e2e_claims.py`, 45 checks)

Against the live dev API + worker, a real receipt image is uploaded and
processed by the AI pipeline (7 fields extracted), one field is confirmed
by the user, and a real warranty card is uploaded and classified as
`WARRANTY_CARD`. The purchase is linked to the stored receipt (with model +
serial); a claim is created; the receipt + warranty card are **selected**
as claim evidence; a photo is uploaded; the package is generated and
verified end to end:

- provenance states in the package (INFERRED purchase, USER_CONFIRMED
  confirmed field, AI_EXTRACTED unconfirmed fields, EXTERNAL_SOURCE source,
  COMPUTED timelines)
- receipt + warranty document sections, per-field provenance
- claim-ready report (markdown) download
- evidence bundle ZIP: `package.json` + `claim-report.md` + README with
  sha256s, byte-exact evidence + document files
- **MVP boundary**: `POST …/submit` → 404; `PATCH` cannot change status
- portable export bundle: manifest + document + evidence files

### 2.4 E2E regression (live API + worker)

- `e2e_alerts.py` **31/31** · `e2e_warranty.py` **46/46** ·
  `e2e_product.py` **11/11** · `e2e_claims.py` **45/45** — **133/133**.

## 3. Documents updated

- TSD §23: full claims API surface incl. document attachments, report,
  bundle; explicit "no submission endpoint" note.
- TSD §33: package contents (model/SKU, serial, receipt with fields),
  provenance list extended with **inferred** + **computed**, bundle/report
  delivery, stored-document evidence selection.
- TSD §57 (existing): claim automation safety — implemented and enforced
  (no status field, no submit endpoints, boundary text in the package).
- SDD §4.9: MVP boundary, provenance requirement, report/bundle/export.

## 4. Notes / known boundaries

- `SUBMITTED_EXTERNALLY` exists in the status model for a future official
  integration; nothing in the MVP can reach it through the API.
- Document roles are user-declared (honest provenance: the attachment is
  USER_CONFIRMED even when the document's fields are AI-extracted).
- The bundle/export stream files from the same object-storage layer with a
  per-file try/except: one missing file degrades the archive with a log
  line, never breaks the export.
