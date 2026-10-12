"""Evaluation metrics (PRD §6, TSD §41).

OCR: Character Error Rate (CER), Word Error Rate (WER) vs ground-truth text.
Extraction: Exact match, Precision, Recall, F1 per field and aggregated.
Product matching (Phase 3): Top-1 / Top-3 accuracy, false-match rate.
"""

from dataclasses import dataclass, field


# --- OCR metrics -----------------------------------------------------------
def char_error_rate(hypothesis: str, reference: str) -> float:
    """CER = edit distance / len(reference). 0.0 = perfect."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _levenshtein(hypothesis, reference) / len(reference)


def word_error_rate(hypothesis: str, reference: str) -> float:
    """WER = word-level edit distance / len(reference words)."""
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _levenshtein_seq(hyp_words, ref_words) / len(ref_words)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _levenshtein_seq(a: list[str], b: list[str]) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --- Extraction metrics ----------------------------------------------------
@dataclass
class ExtractionEval:
    """Per-field precision/recall/f1 computed from (predicted, expected) maps.

    Exact match on normalized values.
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    per_field: dict = field(default_factory=dict)

    def add(self, field_name: str, predicted: str | None, expected: str | None) -> None:
        p = (predicted or "").strip().lower() or None
        e = (expected or "").strip().lower() or None
        if e is None and p is None:
            return  # correctly absent
        if e is None and p is not None:
            self.fp += 1
            self.per_field.setdefault(field_name, []).append("fp")
            return
        if e is not None and p is None:
            self.fn += 1
            self.per_field.setdefault(field_name, []).append("fn")
            return
        if p == e:
            self.tp += 1
            self.per_field.setdefault(field_name, []).append("tp")
        else:
            # Count as both a false positive and false negative (value mismatch).
            self.fp += 1
            self.fn += 1
            self.per_field.setdefault(field_name, []).append("mismatch")

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 1.0


# --- Product matching metrics (Phase 3) -------------------------------------
def top_k_accuracy(predicted_ids: list[str], expected_id: str, k: int) -> bool:
    return expected_id in predicted_ids[:k]


def false_match_rate(results: list[dict]) -> float:
    """A false match = a returned match that is wrong (confidence above
    threshold but wrong product). results: [{returned_id, expected_id,
    requires_confirmation}]"""
    if not results:
        return 0.0
    false_matches = [
        r
        for r in results
        if not r.get("requires_confirmation") and r.get("returned_id") != r.get("expected_id")
    ]
    return len(false_matches) / len(results)
