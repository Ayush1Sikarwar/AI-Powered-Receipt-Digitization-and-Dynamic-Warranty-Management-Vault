"""Evaluation metrics (PRD §6, TSD §41).

Implementation lives in :mod:`app.ai.evaluation.metrics`; re-exported
here for package-level imports.
"""

from app.ai.evaluation.metrics import (
    ExtractionEval,
    char_error_rate,
    false_match_rate,
    top_k_accuracy,
    word_error_rate,
)

__all__ = [
    "ExtractionEval",
    "char_error_rate",
    "false_match_rate",
    "top_k_accuracy",
    "word_error_rate",
]
