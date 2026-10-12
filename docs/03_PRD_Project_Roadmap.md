# WarrantyVault AI --- Product Requirements & Development Roadmap

**Version:** 1.0\
**Date:** 10 August 2026

## 1. Product Vision

Create a trusted digital vault that turns physical purchase evidence
into permanent, searchable, warranty-aware records.

The core promise is:

> Upload the receipt once. Verify the purchase once. Know what is
> covered and when coverage expires.

## 2. MVP Definition

The MVP should prove the complete journey:

``` text
Receipt Upload
     ↓
AI Enhancement
     ↓
OCR + Structured Extraction
     ↓
Product Identification
     ↓
Warranty Retrieval
     ↓
Warranty Calculation
     ↓
Dashboard
     ↓
Expiry Alert
     ↓
Claim Package
```

The MVP should not attempt to solve every manufacturer's portal
integration.

## 3. Priority Features

### P0 --- Must Have

-   User authentication
-   Receipt upload
-   PDF/image support
-   OCR
-   Receipt enhancement
-   Structured extraction
-   Manual correction
-   Product identification
-   Warranty knowledge base
-   Warranty calculation
-   Dashboard
-   Expiration alerts
-   Claim package generation

### P1 --- Should Have

-   Warranty document upload
-   Serial-number extraction
-   Multiple warranties per product
-   Semantic product search
-   Email notifications
-   PDF claim report
-   Admin dashboard
-   Extraction analytics

### P2 --- Future

-   Mobile application
-   Manufacturer portal integrations
-   Automatic claim submission
-   Predictive maintenance models
-   Retailer integrations
-   Browser/email receipt ingestion
-   Extended warranty recommendations

## 4. Development Phases

### Phase 0 --- Foundation

Deliverables: - Repository - Backend skeleton - Frontend skeleton -
Database - Docker environment - Environment configuration - CI
pipeline - Basic authentication

### Phase 1 --- Digital Vault

Deliverables: - Upload API - Object storage - Document metadata -
Purchase records - Dashboard - Search

### Phase 2 --- Receipt AI

Deliverables: - Image preprocessing - OCR - Field extraction -
Confidence scores - Human verification UI - Evaluation dataset

### Phase 3 --- Product Intelligence

Deliverables: - Product schema - SKU normalization - Alias mapping -
Product matching - Confidence thresholds

### Phase 4 --- Warranty Intelligence

Deliverables: - Warranty schema - Source registry - Curated manufacturer
data - Retrieval service - Rules engine - Coverage timeline

### Phase 5 --- Alerts & Claims

Deliverables: - Notification scheduler - 60/30/7-day reminders - Claim
creation - Claim package generation - Evidence bundle

### Phase 6 --- Production Hardening

Deliverables: - Security audit - Rate limits - File scanning -
Backup/restore - Monitoring - Error handling - Model evaluation - Load
testing

## 5. Initial Dataset Strategy

Do not begin by attempting to collect a "massive" corpus.

Start with a controlled evaluation dataset.

### Dataset Categories

-   Clear thermal receipts
-   Faded receipts
-   Low-contrast receipts
-   Crumpled receipts
-   Skewed receipts
-   Shadowed receipts
-   Water-damaged samples
-   Multiple retailer formats
-   Different languages/currencies where relevant

For each document maintain:

``` text
document_id
image
ground_truth_text
retailer
purchase_date
product
sku
price
currency
quality_category
```

### Minimum Dataset Volume (baseline, pre-calibration)

Before any confidence threshold (SDD Confidence Strategy) is treated as
final, the evaluation dataset should contain at least:

``` text
~50 labeled samples per quality category (clear, faded, low-contrast,
crumpled, skewed, shadowed, water-damaged, per major retailer format)
```

This is a starting floor, not a statistically final target --- it
should be revisited once initial CER/WER and extraction F1 results are
available, since categories with high variance will need more samples
than categories with low variance to produce a trustworthy threshold.
Thresholds set before this floor is met should be treated as
provisional and flagged as such in the model registry.

## 6. Model Evaluation

### OCR Metrics

-   Character Error Rate (CER)
-   Word Error Rate (WER)

### Extraction Metrics

-   Exact match
-   Precision
-   Recall
-   F1 score

### Product Matching

-   Top-1 accuracy
-   Top-3 accuracy
-   False-match rate

### Warranty Mapping

-   Correct policy rate
-   Correct duration rate
-   Source validity rate

## 7. Product Trust Model

The platform should distinguish:

### Verified

Directly supported by original evidence or authoritative source.

### AI Extracted

Extracted from a document but not yet confirmed.

### User Confirmed

Reviewed and accepted by the consumer.

### External Source

Obtained from a manufacturer/approved source.

### Inferred

Derived by rules or matching and requiring appropriate confidence
handling.

This prevents AI guesses from being presented as facts.

## 8. Business KPIs

Track: - Documents uploaded - Successful extraction percentage - Manual
correction percentage (target ceiling to be set once the evaluation
dataset baseline in Section 5 is established --- this percentage
determines the human-review operating cost per document and directly
affects whether the product's unit economics are viable) - Product
matching accuracy - Active warranties - Warranties saved from
expiration - Claim packages generated - User-reported successful
claims - Average processing time - Monthly active users - Human review
queue volume and average review time per item (operating cost proxy)

## 9. Key Product Risks

### Risk: Fake/Incorrect Warranty Mapping

Mitigation: official sources, provenance, confidence scoring, user
confirmation.

### Risk: AI Hallucination

Mitigation: evidence-grounded extraction and strict schemas.

### Risk: Website Changes

Mitigation: source adapters, cached policy versions, monitoring, and
official APIs where available.

### Risk: Warranty Source Acquisition Is Not Guaranteed to Exist

Most manufacturers are unlikely to expose an official API, and many
warranty pages may not be reliably scrapable within their terms of
service. This is a data-acquisition and possibly business-development
problem, not only an engineering one.

Mitigation: before committing to a manufacturer/product-category
coverage roadmap, validate for a representative sample of target
manufacturers which of the four source tiers (official API / official
document / official website / curated dataset) is actually available.
Where none of the automatable tiers is viable, coverage should
initially rely on a manually curated dataset maintained by an
operational team, with automation added opportunistically. The product
roadmap (Section 4) should not assume automated retrieval coverage
for a manufacturer until that manufacturer's source tier has been
confirmed.

### Risk: Regional/Legal Warranty Ambiguity

Manufacturer warranty terms are not the same as statutory
consumer-protection rights, which vary by country (e.g., minimum
statutory guarantee periods in some jurisdictions). Presenting only
manufacturer terms without this distinction could mislead users about
their actual rights.

Mitigation: label all displayed warranty information explicitly as
manufacturer coverage (see SRS FR-11b), resolve applicable region
per-purchase rather than per-account only where the two differ (see
SRS FR-11a), and keep statutory-rights interpretation explicitly out
of scope per the SRS rather than implying completeness.

### Risk: Privacy

Mitigation: encryption, minimal data collection, deletion controls,
access isolation.

### Risk: Impossible Receipt Recovery

Mitigation: clearly label uncertain fields and request user confirmation
instead of fabricating values.

## 10. Definition of Done for MVP

A feature is complete only when it has: - Backend implementation -
Frontend integration - Database migration - Validation - Error
handling - Unit tests - API tests where applicable - Logging -
Documentation - Security review

## 11. Recommended Repository Structure

``` text
warranty-vault/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── ai/
│   │   │   ├── preprocessing/
│   │   │   ├── ocr/
│   │   │   ├── extraction/
│   │   │   └── matching/
│   │   ├── warranty/
│   │   └── workers/
│   └── tests/
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── services/
│   │   └── hooks/
│   └── tests/
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── evaluation/
│
├── infrastructure/
│   ├── docker/
│   └── deployment/
│
├── docs/
│
├── .env.example
├── docker-compose.yml
├── README.md
└── LICENSE
```

## 12. First Implementation Order

Do not start with the generative AI model.

Build in this order:

1.  Database schema
2.  Authentication
3.  Document upload/storage
4.  Basic OCR pipeline
5.  Structured extraction
6.  Human verification
7.  Product database/matching
8.  Warranty knowledge base
9.  Warranty rules engine
10. Dashboard
11. Notifications
12. Claim package
13. Advanced degraded-receipt AI
14. Manufacturer integrations

This order gives the project a working end-to-end backbone before
expensive AI optimization.
