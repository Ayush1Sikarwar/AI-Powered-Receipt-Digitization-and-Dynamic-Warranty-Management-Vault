# Product Matching Evaluation (rule-based matcher)

- Engine: **rule-based-product-matcher** (live code, identity from model registry)
- Dataset: synthetic deterministic catalog (20 products / 5 manufacturers) + 126 queries (120 positive incl. noisy/alias/brand variants, 6 negative grocery lines)
- Thresholds: auto ≥ 0.9, review ≥ 0.7 (FINAL)

| Metric | Value |
| --- | --- |
| top1_accuracy | 0.8559 |
| top3_accuracy | 0.8919 |
| auto_match_rate | 0.6396 |
| false_match_rate | 0.0 |
| false_matches | 0 |
| correct_reject_rate_negatives | 1.0 |
| review_required_rate | 0.2613 |

By query kind (top-1 accuracy / auto rate):

| kind | n | top1_acc | auto_rate |
| exact_model | 20 | 1.0 | 1.0 |
| exact_sku | 20 | 1.0 | 1.0 |
| name_only | 20 | 1.0 | 0.0 |
| brand_model | 20 | 1.0 | 1.0 |
| alias | 11 | 1.0 | 1.0 |
| noisy | 20 | 0.2 | 0.0 |

Honest caveats: synthetic catalog and receipt lines (exact ground truth by construction);
real-world retail abbreviations, OCR noise and catalog coverage shift all numbers.
Threshold status: FINAL (PRD §5 evaluation dataset floor met — see RESULTS.json metadata)