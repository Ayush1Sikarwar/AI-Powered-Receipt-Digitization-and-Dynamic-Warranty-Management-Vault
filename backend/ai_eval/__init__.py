"""Controlled evaluation dataset + measurement scripts for the document AI
pipeline (PRD §5–6, TSD §41–43).

This is a *synthetic* dataset: receipts are rendered from templates with
exact ground truth captured at generation time. It exists to make the
baseline measurable (CER/WER, extraction exact match / P / R / F1) so the
confidence thresholds can be calibrated against real numbers instead of
invented ones. The PRD floor is ~50 labeled samples per quality category;
until the floor is met, all thresholds remain **provisional** (flagged in
the model registry and in RESULTS.md).
"""
