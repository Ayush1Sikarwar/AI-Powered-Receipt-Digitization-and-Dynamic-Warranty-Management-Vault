"""AI tests: controlled evaluation dataset + measurement scripts (PRD §5–6).

The evaluation must be reproducible (deterministic seeds), must carry the
PRD per-document fields, and the measurement pipeline must produce actual
CER/WER/extraction metrics — no invented numbers.
"""

import json

import pytest

from ai_eval.generate_dataset import CATEGORIES, generate
from ai_eval.pipeline_eval import evaluate_one

PRD_FIELDS = [
    "document_id",
    "image",
    "ground_truth_text",
    "retailer",
    "purchase_date",
    "product",
    "sku",
    "price",
    "currency",
    "quality_category",
]


@pytest.fixture(scope="module")
def tiny_dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("eval_ds")
    generate(out, per_category=1)
    return out


def test_dataset_structure(tiny_dataset):
    manifest = json.loads((tiny_dataset / "manifest.json").read_text())
    cats = {s["quality_category"] for s in manifest.values()}
    assert cats == set(CATEGORIES), f"categories missing: {set(CATEGORIES) - cats}"
    for s in manifest.values():
        for f in PRD_FIELDS:
            assert f in s, f"PRD field {f} missing for {s['document_id']}"
        assert (tiny_dataset / s["image"]).exists()
        assert s["synthetic"] is True, "synthetic samples must be labeled synthetic"
        assert s["fields"], "per-field ground truth required"


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, per_category=1)
    generate(b, per_category=1)
    assert (a / "manifest.json").read_text() == (b / "manifest.json").read_text()


def test_evaluate_one_measures_actual_metrics(tiny_dataset):
    manifest = json.loads((tiny_dataset / "manifest.json").read_text())
    sample = next(s for s in manifest.values() if s["quality_category"] == "clear")
    r = evaluate_one(sample, tiny_dataset)
    assert 0.0 <= r["cer"] <= 1.0
    assert 0.0 <= r["wer"] <= 1.0
    assert 0.0 <= r["precision"] <= 1.0
    assert 0.0 <= r["recall"] <= 1.0
    assert 0.0 <= r["f1"] <= 1.0
    assert r["fields"], "per-field ground-truth-vs-predicted results required"
    # Model identity recorded (TSD §45).
    assert r["ocr_engine"] and r["ocr_engine_version"]
    assert r["extraction_model"] and r["extraction_model_version"]
    assert r["kept_image"] in ("original", "enhanced")


def test_run_evaluation_writes_results(tiny_dataset, tmp_path, monkeypatch):
    from ai_eval.run_evaluation import main

    monkeypatch.setattr(
        "sys.argv",
        ["run_evaluation.py", "--dataset", str(tiny_dataset), "--out", str(tmp_path), "--limit", "1"],
    )
    main()
    payload = json.loads((tmp_path / "RESULTS.json").read_text())
    assert payload["metadata"]["sample_count"] == 1
    assert payload["overall"]["n"] == 1
    assert payload["metadata"]["confidence_thresholds"] == "PROVISIONAL"
    assert (tmp_path / "RESULTS.md").exists()
    # Floor check must show the per-category shortfall honestly.
    floor = payload["floor_check"]
    assert floor["clear"]["met"] is False
    assert floor["clear"]["floor"] == 50
