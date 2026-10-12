# WarrantyVault AI --- Software Requirements Specification (SRS)

**Version:** 1.0\
**Date:** 10 August 2026\
**Status:** Pre-development baseline

## 1. Purpose

WarrantyVault AI is a consumer-facing digital warranty vault that
converts physical purchase evidence into structured digital records,
identifies applicable warranty coverage, monitors expiration dates, and
assists users in preparing warranty claims.

The SRS defines the functional, non-functional, data, AI/ML, security,
and integration requirements for the first production-oriented
implementation.

## 2. Scope

### In Scope

-   User registration and authentication
-   Receipt and warranty document upload
-   Image/PDF preprocessing
-   OCR and structured field extraction
-   Faded/degraded receipt enhancement
-   Product/SKU normalization
-   Warranty policy retrieval and mapping
-   Warranty start/end-date calculation
-   Warranty dashboard and search
-   Expiration notifications
-   Claim preparation and pre-filled claim forms
-   Audit trail and document provenance
-   Human correction of low-confidence extraction
-   Admin tools for product/warranty knowledge management

### Out of Scope for MVP

-   Direct financial transactions
-   Guaranteed automated submission to every manufacturer's claim portal
-   Legal interpretation of warranty contracts
-   Determination of statutory/consumer-protection warranty rights
    (e.g., EU minimum guarantee periods) as distinct from manufacturer
    warranty terms --- the MVP surfaces manufacturer warranty coverage
    only and must label it as such
-   Fully autonomous claim approval
-   Training a foundation OCR model from scratch
-   Scraping websites in violation of their terms

## 3. Users

### Consumer

Uploads documents, reviews extracted information, manages warranties,
receives alerts, and prepares claims.

### Administrator

Manages manufacturers, warranty sources, product mappings, failed
extraction cases, and system configuration.

### System/AI Services

Perform document processing, extraction, product matching, warranty
retrieval, validation, and alert generation.

## 4. Functional Requirements

### FR-01 Authentication

The system shall allow secure account registration, login, logout,
password reset, and session management.

### FR-02 Document Upload

The system shall accept JPG, JPEG, PNG, WEBP, and PDF documents subject
to configurable size limits.

### FR-03 Document Classification

The system shall classify uploaded documents as receipt, warranty card,
invoice, purchase order, or unknown document.

### FR-04 Image Enhancement

The system shall preprocess degraded receipts using operations such as
denoising, deblurring, contrast enhancement, adaptive thresholding,
perspective correction, and upscaling.

### FR-05 OCR

The system shall extract text from supported documents and retain OCR
confidence information.

### FR-06 Structured Extraction

The system shall extract, where available: - Retailer - Purchase date -
Product name - SKU/model number - Serial number - Quantity - Unit
price - Total price - Tax - Currency - Invoice/receipt number

### FR-07 Confidence Scoring

Every extracted field shall have a confidence score and source
reference. Low-confidence fields shall be presented for user
verification.

### FR-08 Product Normalization

The system shall normalize retailer-specific product abbreviations into
a canonical product representation.

### FR-09 Warranty Retrieval

The system shall retrieve warranty information from approved
manufacturer sources or maintained warranty datasets.

### FR-10 Warranty Mapping

The system shall map a purchase to one or more applicable warranty
components, such as parts, labor, compressor, battery, display, or
accessories.

### FR-11 Warranty Calculation

The system shall calculate warranty start and end dates using the
purchase date and applicable warranty rules.

### FR-11a Warranty Region Resolution

The system shall determine the applicable warranty region for a
purchase using, in order of preference: (1) an explicit region/country
selected by the user for that purchase, (2) the retailer's known
country of operation, (3) the user's account-level country setting.
The resolved region and its resolution method shall be stored with the
purchase so that region-dependent warranty terms remain auditable if
the user's settings later change. If no region can be resolved with
sufficient confidence, the warranty status shall be marked `UNKNOWN`
rather than defaulting silently to a region.

### FR-11b Manufacturer vs. Statutory Warranty Labeling

Where the system displays warranty coverage, it shall clearly label it
as manufacturer-warranty coverage and shall not represent it as a
complete statement of the user's legal consumer-protection rights.

### FR-12 Source Provenance

Each warranty rule shall retain source URL/document, retrieval date,
manufacturer, applicable region, and source version where available.

### FR-13 Dashboard

The system shall display active, expiring, expired, and incomplete
warranties.

### FR-14 Search

Users shall be able to search by product, manufacturer, SKU/model,
purchase date, and warranty status.

### FR-15 Expiration Alerts

The system shall generate configurable alerts, with default reminders at
60, 30, and 7 days before expiration.

### FR-16 Claim Preparation

The system shall generate a pre-filled claim package containing relevant
purchase details, warranty information, and supporting documents.

### FR-17 User Correction

Users shall be able to correct extracted data without modifying the
original uploaded document.

### FR-18 Auditability

The system shall maintain an audit trail for document uploads,
extraction versions, user corrections, warranty mappings, and
claim-package generation.

### FR-19 Data Export

Users shall be able to export their warranty records and supporting
evidence in a portable format.

### FR-20 Deletion

Users shall be able to delete documents and associated personal data
according to the application's retention policy.

## 5. AI/ML Requirements

### ML-01 Receipt Enhancement

The enhancement pipeline shall improve readability without silently
inventing missing characters.

### ML-02 OCR Ensemble

The architecture should support multiple OCR engines/models so that
difficult documents can be processed using fallback strategies.

### ML-03 Structured Extraction

A document-understanding model or LLM-assisted extraction layer shall
convert OCR output into a validated schema.

### ML-04 Product Matching

Product matching shall combine exact SKU/model matching, fuzzy matching,
manufacturer constraints, and semantic similarity.

### ML-05 Warranty Mapping

Warranty mapping shall use deterministic rules first and NLP/semantic
retrieval as a supporting mechanism.

### ML-06 Hallucination Control

AI-generated values shall never be treated as verified purchase facts
without evidence from the source document or an approved external
source.

### ML-07 Human-in-the-Loop

The system shall route uncertain extraction or product matches for user
confirmation.

## 6. Non-Functional Requirements

### NFR-01 Security

Sensitive user data shall be encrypted in transit and at rest.

### NFR-02 Privacy

The system shall minimize collection of personal data and provide
user-controlled deletion/export.

Default retention periods (configurable per deployment/region):

-   Active user accounts and their documents: retained until the user
    deletes them or closes the account.
-   Closed accounts: original documents and personal data purged within
    90 days of account closure, unless a longer period is required by
    law or an open claim references the data.
-   Documents referenced by an open (non-`CLOSED`) claim: retention of
    the document and its linked purchase/warranty records is extended
    until the claim reaches a terminal state (`RESOLVED` or `CLOSED`),
    even if the user requests deletion; the user shall be informed of
    this hold at the time of the deletion request.
-   Audit logs: retained for a minimum of 1 year for security and
    dispute-resolution purposes, independent of document deletion.

### NFR-03 Availability

Target MVP service availability: 99.5%, excluding planned maintenance.

### NFR-04 Performance

Normal document processing should provide an initial result within 10
seconds for typical single-page receipts, subject to model and hardware
constraints.

### NFR-05 Scalability

Document processing shall be separable from the web/API layer so workers
can scale independently.

### NFR-06 Reliability

Uploaded originals shall remain immutable. Processing failures shall be
retryable.

### NFR-07 Observability

The system shall provide structured logs, metrics, error tracking, and
processing-job status.

### NFR-08 Accessibility

The web interface should follow WCAG-oriented accessibility practices.

## 7. Security Requirements

-   Passwords shall be stored using a modern password hashing algorithm.
-   Authentication tokens/sessions shall have expiration and revocation
    mechanisms.
-   Object/document access shall be authorization-controlled per user.
-   Uploaded files shall be validated and malware-scanned where
    infrastructure permits.
-   File names and paths shall never be trusted as executable input.
-   External warranty content shall be treated as untrusted data.
-   Secrets shall not be stored in source code.
-   Administrative actions shall be audited.
-   Rate limits shall protect authentication, upload, and API endpoints.

## 8. Data Integrity Requirements

The original document shall be stored separately from all processed
derivatives.

Each extracted field should contain: - value - confidence - extraction
method/model - source page/region where available - extraction
timestamp - verification status

## 9. Acceptance Criteria

The MVP is acceptable when a test user can: 1. Register and log in. 2.
Upload a receipt image/PDF. 3. View enhanced image and extracted fields.
4. Correct incorrect fields. 5. Identify the purchased product. 6.
Retrieve an applicable warranty rule from an approved source. 7.
Calculate the warranty expiry date. 8. See the item on a dashboard. 9.
Receive an expiry reminder. 10. Generate a claim-ready package
containing the verified evidence.

## 10. Risks

-   Severely degraded receipts may contain information that cannot be
    reliably recovered.
-   Manufacturer warranty terms may vary by country, product category,
    registration status, and purchase channel.
-   Web scraping can create legal, technical, and maintenance issues.
-   Retailer SKU abbreviations may be ambiguous.
-   Automated claim submission may require authentication or CAPTCHA.
-   AI-generated interpretations can create false warranty claims if not
    grounded in evidence.

## 11. Success Metrics

-   Field extraction accuracy
-   Product matching accuracy
-   Warranty mapping accuracy
-   Percentage of documents requiring manual correction (target: below
    an agreed threshold to be set once the evaluation dataset in the
    PRD's Dataset Strategy has produced a baseline; this percentage
    directly affects unit economics and must not remain unbounded)
-   Processing time per document
-   Reminder delivery success rate
-   Claim-package completion rate
-   User-reported successful warranty recovery

Confidence thresholds (see SDD Confidence Strategy) shall not be
finalized until they have been validated against the evaluation
dataset described in the PRD; the minimum dataset size and composition
required before a threshold is considered calibrated shall be defined
before Phase 2 begins.
