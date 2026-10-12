"""AI tests: model registry (TSD §45).

Every production AI component must identify model_name, model_version,
pipeline_version, configuration_version, deployment_date — and unconfigured
components must be reported as such, never faked.
"""

from app.ai.registry import ModelInfo, registry, thresholds_provisional
from app.core.config import get_settings


def test_registry_covers_all_components():
    components = {m.component for m in registry()}
    assert components == {
        "ocr_engine",
        "document_classifier",
        "image_quality_model",
        "extraction_model",
        "preprocessing",
        "product_ranker",
        "embedding_model",
    }


def test_registry_identifies_every_component():
    settings = get_settings()
    for m in registry():
        assert isinstance(m, ModelInfo)
        assert m.model_name
        assert m.model_version
        assert m.pipeline_version == settings.pipeline_version
        assert m.configuration_version
        assert m.deployment_date
        d = m.as_dict()
        assert set(d) == {
            "component",
            "model_name",
            "model_version",
            "pipeline_version",
            "configuration_version",
            "deployment_date",
            "notes",
        }


def test_embedding_model_honestly_reports_not_configured():
    emb = next(m for m in registry() if m.component == "embedding_model")
    assert emb.model_version == "not-configured"
    assert "not configured" in emb.notes.lower() or "disabled" in emb.notes.lower()


def test_registry_never_fakes_unavailable_ocr():
    import shutil

    ocr = next(m for m in registry() if m.component == "ocr_engine")
    if shutil.which("tesseract") is None:
        assert "not available" in ocr.notes.lower()


def test_thresholds_flag_consistent_with_measured_floor():
    # The provisional flag must agree with the measured dataset floor
    # (metadata.floor_met) recorded by ai_eval/run_evaluation.py.
    import json
    from pathlib import Path

    results = Path(__file__).resolve().parents[2] / "ai_eval" / "RESULTS.json"
    try:
        meta = json.loads(results.read_text())["metadata"]
        floor_met = bool(meta.get("floor_met", False))
    except (OSError, ValueError, KeyError):
        floor_met = False  # no artifact -> conservative default in the flag
    assert thresholds_provisional() is (not floor_met)
