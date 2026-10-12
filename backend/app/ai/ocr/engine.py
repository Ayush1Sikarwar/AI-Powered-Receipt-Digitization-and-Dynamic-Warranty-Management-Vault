"""OCR engine contract (TSD §26, ML-02).

OCR output preserves: text, per-word confidence, bounding boxes, page number,
line/word structure, engine name, engine version.

Fallback strategy: the primary engine (Tesseract) runs first; if its mean
confidence is below the review threshold and a fallback engine is available,
the fallback runs and the better per-line result is kept. If NO engine is
available, OCR raises OcrError and the document enters a recoverable FAILED
state (TSD §26) — we never fabricate text.

PaddleOCR is declared as the fallback slot; it is an honest
"not-configured" state here (no weights installed) rather than a fake result.
"""

import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from app.core.config import get_settings
from app.core.exceptions import OcrError

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class OcrWord:
    text: str
    confidence: float  # 0..100 (tesseract scale)
    bbox: tuple[int, int, int, int]  # x, y, w, h
    page: int = 1
    line: int = 0
    word_in_line: int = 0


@dataclass
class OcrLine:
    line_number: int
    text: str
    confidence: float  # mean word confidence, 0..100
    words: list[OcrWord] = field(default_factory=list)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    page: int = 1


@dataclass
class OcrResult:
    engine: str
    engine_version: str
    pages: list[list[OcrLine]]  # [page][line]
    mean_confidence: float  # 0..100

    def text(self, page: int | None = None) -> str:
        if page is not None:
            return "\n".join(line.text for line in self.pages[page - 1])
        return "\n".join("\n".join(line.text for line in p) for p in self.pages)


class OcrEngine(ABC):
    name: str = "abstract"
    version: str = "0"

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def recognize(self, image: "np.ndarray", page_number: int = 1) -> list[OcrLine]:
        """Recognize one page image (grayscale or BGR)."""


class TesseractEngine(OcrEngine):
    name = "tesseract"
    version = "5.x"

    def is_available(self) -> bool:
        if shutil.which("tesseract") is None:
            return False
        try:
            import pytesseract  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def recognize(self, image: "np.ndarray", page_number: int = 1) -> list[OcrLine]:
        if not self.is_available():
            raise OcrError("Tesseract OCR engine is not available on this host")
        import pytesseract

        data = pytesseract.image_to_data(
            image,
            lang=settings.ocr_lang,
            config=f"--psm {settings.tesseract_psm}",
            output_type=pytesseract.Output.DICT,
        )
        lines: dict[tuple[int, int, int], list[OcrWord]] = {}
        for i, text in enumerate(data["text"]):
            token = (text or "").strip()
            if not token:
                continue
            conf = data["conf"][i]
            try:
                conf_f = float(conf)
            except (TypeError, ValueError):
                conf_f = -1.0
            word = OcrWord(
                text=token,
                confidence=conf_f if conf_f >= 0 else 0.0,
                bbox=(
                    int(data["left"][i] or 0),
                    int(data["top"][i] or 0),
                    int(data["width"][i] or 0),
                    int(data["height"][i] or 0),
                ),
                page=page_number,
            )
            line_key = (int(data["block_num"][i] or 0), int(data["par_num"][i] or 0), int(data["line_num"][i] or 0))
            lines.setdefault(line_key, []).append(word)

        ocr_lines: list[OcrLine] = []
        for idx, (_key, words) in enumerate(sorted(lines.items()), start=1):
            text = " ".join(w.text for w in words)
            confs = [w.confidence for w in words if w.confidence >= 0]
            mean_conf = sum(confs) / len(confs) if confs else 0.0
            x0 = min(w.bbox[0] for w in words)
            y0 = min(w.bbox[1] for w in words)
            x1 = max(w.bbox[0] + w.bbox[2] for w in words)
            y1 = max(w.bbox[1] + w.bbox[3] for w in words)
            for w in words:
                w.line = idx
            ocr_lines.append(
                OcrLine(
                    line_number=idx,
                    text=text,
                    confidence=round(mean_conf, 2),
                    words=words,
                    bbox=(x0, y0, x1 - x0, y1 - y0),
                    page=page_number,
                )
            )
        return ocr_lines


class PaddleOcrEngine(OcrEngine):
    """Fallback slot. Honest 'not configured' when the library/weights are
    absent — never produces fabricated output."""

    name = "paddleocr"
    version = "n/a"

    def __init__(self) -> None:
        self._module = None
        try:
            import paddleocr  # type: ignore

            self._module = paddleocr
        except Exception:  # noqa: BLE001
            self._module = None

    def is_available(self) -> bool:
        return self._module is not None

    def recognize(self, image: "np.ndarray", page_number: int = 1) -> list[OcrLine]:
        raise OcrError("PaddleOCR fallback engine is not configured in this deployment")


_primary: OcrEngine | None = None
_fallback: OcrEngine | None = None


def get_engine() -> OcrEngine:
    global _primary
    if _primary is None:
        _primary = TesseractEngine() if settings.ocr_primary_engine == "tesseract" else PaddleOcrEngine()
    return _primary


def get_fallback_engine() -> OcrEngine | None:
    global _fallback
    if not settings.ocr_fallback_enabled:
        return None
    if _fallback is None:
        _fallback = PaddleOcrEngine()
    return _fallback


def is_available() -> bool:
    return get_engine().is_available()


def run_ocr_on_pages(
    pages_images: list["np.ndarray"],
    low_confidence_trigger: float,
) -> tuple[OcrResult, dict]:
    """Two-stage OCR with fallback (TSD §24 OCR Pass 1/2 + comparison).

    Returns (result, comparison_report). Pass 2 = fallback engine when the
    primary's mean confidence is below the trigger and a fallback exists.
    """
    engine = get_engine()
    if not engine.is_available():
        raise OcrError("No OCR engine is available on this host (tesseract missing)")

    line_source = "primary"
    primary_pages = [engine.recognize(img, i + 1) for i, img in enumerate(pages_images)]
    all_words = [w for p in primary_pages for line in p for w in line.words]
    confs = [w.confidence for w in all_words if w.confidence >= 0]
    mean_conf = sum(confs) / len(confs) if confs else 0.0

    report: dict = {
        "primary_engine": engine.name,
        "primary_engine_version": engine.version,
        "primary_mean_confidence": round(mean_conf, 2),
        "fallback_used": False,
        "fallback_engine": None,
        "comparison": "primary only",
    }

    fallback = get_fallback_engine()
    if mean_conf < low_confidence_trigger and fallback is not None and fallback.is_available():
        try:
            fb_pages = [fallback.recognize(img, i + 1) for i, img in enumerate(pages_images)]
            # Per-line comparison: keep the higher-confidence line per index.
            merged_pages = []
            for i, page in enumerate(primary_pages):
                fb_page = fb_pages[i]
                merged: list[OcrLine] = []
                for n in range(max(len(page), len(fb_page))):
                    a = page[n] if n < len(page) else None
                    b = fb_page[n] if n < len(fb_page) else None
                    if a is None:
                        assert b is not None  # n < max(len(page), len(fb_page))
                        merged.append(b)
                    elif b is None:
                        merged.append(a)
                    else:
                        merged.append(b if b.confidence > a.confidence else a)
                merged_pages.append(merged)
            fb_words = [w for p in fb_pages for line in p for w in line.words]
            fb_confs = [w.confidence for w in fb_words if w.confidence >= 0]
            fb_mean = sum(fb_confs) / len(fb_confs) if fb_confs else 0.0
            pages = merged_pages if fb_mean > mean_conf else primary_pages
            if pages is merged_pages:
                line_source = "primary+fallback(per-line best)"
            report["fallback_used"] = True
            report["fallback_engine"] = fallback.name
            report["fallback_mean_confidence"] = round(fb_mean, 2)
            report["comparison"] = f"kept {fallback.name} lines where confidence was higher" if fb_mean > mean_conf else "kept primary"
        except OcrError:
            pages = primary_pages
            report["comparison"] = "fallback failed; kept primary"
    elif mean_conf < low_confidence_trigger:
        pages = primary_pages
        report["comparison"] = "low confidence, no fallback configured — flagged for review"
    else:
        pages = primary_pages

    # Persist the per-line OCR record (text, confidence, bbox, page) so
    # downstream evaluation and provenance can reference exactly what each
    # engine saw. `engine`/`engine_version` identify the line source.
    report["engine"] = engine.name
    report["engine_version"] = engine.version
    report["line_source"] = line_source
    report["lines"] = [
        {
            "page": ln.page,
            "line": ln.line_number,
            "text": ln.text,
            "confidence": ln.confidence,
            "bbox": list(ln.bbox),
        }
        for p in pages
        for ln in p
    ]

    return OcrResult(engine=engine.name, engine_version=engine.version, pages=pages, mean_confidence=round(mean_conf, 2)), report
