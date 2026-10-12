"""AI tests: evaluation metrics (PRD §6, TSD §41)."""

from app.ai.evaluation import (
    ExtractionEval,
    char_error_rate,
    false_match_rate,
    top_k_accuracy,
    word_error_rate,
)


# --- CER / WER ---------------------------------------------------------------
def test_cer_perfect_is_zero():
    assert char_error_rate("abc", "abc") == 0.0


def test_cer_single_substitution():
    assert char_error_rate("abd", "abc") == 1 / 3


def test_cer_empty_reference():
    assert char_error_rate("", "") == 0.0
    assert char_error_rate("abc", "") == 1.0


def test_cer_bounded_and_monotonic_in_errors():
    assert char_error_rate("abcd", "abce") == 0.25
    assert char_error_rate("xxxx", "abcd") >= char_error_rate("abxd", "abcd")


def test_wer_perfect_is_zero():
    assert word_error_rate("hello world", "hello world") == 0.0


def test_wer_substitution_rate():
    # one wrong word of four
    assert word_error_rate("hello wrong world foo", "hello right world foo") == 0.25


def test_wer_empty_reference():
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("x", "") == 1.0


# --- Extraction precision/recall/f1 -------------------------------------------
def test_extraction_eval_all_correct():
    ev = ExtractionEval()
    ev.add("total_price", "100.00", "100.00")
    ev.add("retailer", "Nortech", "nortech")  # case-insensitive exact match
    assert (ev.precision, ev.recall, ev.f1) == (1.0, 1.0, 1.0)
    assert ev.tp == 2 and ev.fp == 0 and ev.fn == 0


def test_extraction_eval_mismatch_counts_as_fp_and_fn():
    ev = ExtractionEval()
    ev.add("total_price", "100.00", "200.00")
    assert ev.fp == 1 and ev.fn == 1 and ev.tp == 0
    assert ev.precision == 0.0 and ev.recall == 0.0
    # "No data" convention: with no positives at all, f1 falls back to 1.0
    # (same as precision/recall defaults) so empty runs don't read as 0.
    assert ev.f1 == 1.0


def test_extraction_eval_missing_and_extra():
    ev = ExtractionEval()
    ev.add("sku", "A1", None)  # false positive (invented)
    ev.add("serial_number", None, "AB12")  # false negative (missed)
    ev.add("tax", None, None)  # correctly absent
    assert ev.fp == 1 and ev.fn == 1 and ev.tp == 0


def test_extraction_eval_no_predictions_defaults_to_one():
    ev = ExtractionEval()
    assert ev.precision == 1.0 and ev.recall == 1.0 and ev.f1 == 1.0


def test_extraction_eval_known_f1():
    ev = ExtractionEval()
    ev.add("a", "x", "x")  # tp
    ev.add("b", "y", None)  # fp
    assert ev.precision == 0.5
    assert ev.recall == 1.0
    assert abs(ev.f1 - 2 / 3) < 1e-9


# --- Product matching metrics (Phase 3) ---------------------------------------
def test_top_k_accuracy_hit_and_miss():
    assert top_k_accuracy(["p1", "p2", "p3"], "p2", 1) is False
    assert top_k_accuracy(["p1", "p2", "p3"], "p2", 2) is True
    assert top_k_accuracy(["p1"], "p9", 3) is False
    assert top_k_accuracy([], "p1", 1) is False


def test_false_match_rate():
    results = [
        {"returned_id": "p1", "expected_id": "p1", "requires_confirmation": False},
        {"returned_id": "p2", "expected_id": "p1", "requires_confirmation": False},  # false match
        {"returned_id": "p3", "expected_id": "p1", "requires_confirmation": True},  # needs confirmation -> not a false match
    ]
    assert false_match_rate(results) == 1 / 3
    assert false_match_rate([]) == 0.0
