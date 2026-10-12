"""Model registry (TSD §45).

Every production AI component identifies: model_name, model_version,
pipeline_version, configuration_version, deployment_date.

Components: document_classifier, image_quality_model, ocr_engine,
extraction_model, embedding_model (not configured), product_ranker.

Thresholds are flagged PROVISIONAL until the PRD §5 evaluation-dataset floor
(~50 labeled samples per quality category) is met (SRS §11, SDD §8).
"""

from dataclasses import dataclass, field
from datetime import date

from app.core.config import get_settings

settings = get_settings()


@dataclass(frozen=True)
class ModelInfo:
    component: str
    model_name: str
    model_version: str
    pipeline_version: str
    configuration_version: str
    deployment_date: str
    notes: str = field(default="")

    def as_dict(self) -> dict:
        return {
            "component": self.component,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "pipeline_version": self.pipeline_version,
            "configuration_version": self.configuration_version,
            "deployment_date": self.deployment_date,
            "notes": self.notes,
        }


_DEPLOY = date(2026, 8, 31).isoformat()

# Authoritative model identities for the rule-based components (TSD §45).
# Business logic (the pipeline/extractor) MUST import these from here —
# versions are never hard-coded in processing code.
EXTRACTION_MODEL_NAME = "rule-based-schema-constrained"
EXTRACTION_MODEL_VERSION = "1.0.0"
CLASSIFIER_MODEL_NAME = "rule-based-keyword-structural"
CLASSIFIER_MODEL_VERSION = "1.0.0"
QUALITY_MODEL_NAME = "heuristic-contrast-sharpness-illumination"
QUALITY_MODEL_VERSION = "1.0.0"
PREPROCESSING_MODEL_NAME = "opencv-classic-pipeline"
PREPROCESSING_MODEL_VERSION = "1.0.0"
PRODUCT_RANKER_MODEL_NAME = "rule-based-product-matcher"
PRODUCT_RANKER_MODEL_VERSION = "1.0.0"


def ocr_engine_info(available: bool = True) -> ModelInfo:
    return ModelInfo(
        component="ocr_engine",
        model_name="tesseract",
        model_version="5.x (system) / pytesseract",
        pipeline_version=settings.pipeline_version,
        configuration_version=f"psm{settings.tesseract_psm}:{settings.ocr_lang}",
        deployment_date=_DEPLOY,
        notes="OCR result carries per-word confidence and bounding boxes"
        if available
        else "tesseract binary NOT available on this host — OCR reported as unavailable, not faked",
    )


def classifier_info() -> ModelInfo:
    return ModelInfo(
        component="document_classifier",
        model_name=CLASSIFIER_MODEL_NAME,
        model_version=CLASSIFIER_MODEL_VERSION,
        pipeline_version=settings.pipeline_version,
        configuration_version="kw-v1",
        deployment_date=_DEPLOY,
        notes="Deterministic keyword+structure classifier over OCR text; returns UNKNOWN when evidence is weak",
    )


def quality_model_info() -> ModelInfo:
    return ModelInfo(
        component="image_quality_model",
        model_name=QUALITY_MODEL_NAME,
        model_version=QUALITY_MODEL_VERSION,
        pipeline_version=settings.pipeline_version,
        configuration_version="h3",
        deployment_date=_DEPLOY,
        notes="Diagnostic readability score, not a legibility guarantee",
    )


def extraction_model_info() -> ModelInfo:
    return ModelInfo(
        component="extraction_model",
        model_name=EXTRACTION_MODEL_NAME,
        model_version=EXTRACTION_MODEL_VERSION,
        pipeline_version=settings.pipeline_version,
        configuration_version="rules-v1",
        deployment_date=_DEPLOY,
        notes=(
            "Evidence-grounded field extraction; returns null when no evidence (never invents values). "
            "Confidence thresholds are PROVISIONAL until the evaluation dataset floor is met."
        ),
    )


def enhancement_model_info() -> ModelInfo:
    return ModelInfo(
        component="preprocessing",
        model_name=PREPROCESSING_MODEL_NAME,
        model_version=PREPROCESSING_MODEL_VERSION,
        pipeline_version=settings.pipeline_version,
        configuration_version="cv-v1",
        deployment_date=_DEPLOY,
        notes="Deskew/perspective/denoise/illumination/contrast/upscale; never invents content",
    )


def product_ranker_info() -> ModelInfo:
    return ModelInfo(
        component="product_ranker",
        model_name=PRODUCT_RANKER_MODEL_NAME,
        model_version=PRODUCT_RANKER_MODEL_VERSION,
        pipeline_version=settings.pipeline_version,
        configuration_version="score-v1",
        deployment_date=_DEPLOY,
        notes=(
            "Deterministic lexical/structural product matcher (TSD §28); returns no candidate "
            "when evidence is weak — never invents a product"
        ),
    )


def embedding_model_info() -> ModelInfo:
    return ModelInfo(
        component="embedding_model",
        model_name="sentence-transformers",
        model_version="not-configured",
        pipeline_version=settings.pipeline_version,
        configuration_version="none",
        deployment_date=_DEPLOY,
        notes="Semantic matching disabled in this deployment (no model weights); lexical matching is used instead",
    )


def registry() -> list[ModelInfo]:
    import shutil

    tesseract_available = shutil.which("tesseract") is not None
    return [
        ocr_engine_info(tesseract_available),
        classifier_info(),
        quality_model_info(),
        extraction_model_info(),
        enhancement_model_info(),
        product_ranker_info(),
        embedding_model_info(),
    ]


def thresholds_provisional() -> bool:
    """Whether the confidence thresholds are still PROVISIONAL.

    Derived from the latest measured evaluation (PRD §5 dataset floor,
    ~50 labeled samples per quality category): reads `metadata.floor_met`
    from `ai_eval/RESULTS.json`. A missing or unreadable artifact returns
    True — conservative: thresholds are never presented as final without
    measured evidence (see docs/ai/evaluation.md).
    """
    import json
    from pathlib import Path

    results_path = Path(__file__).resolve().parents[2] / "ai_eval" / "RESULTS.json"
    try:
        meta = json.loads(results_path.read_text())["metadata"]
    except (OSError, ValueError, KeyError):
        return True
    return not bool(meta.get("floor_met", False))
