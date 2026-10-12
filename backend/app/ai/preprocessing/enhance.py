"""Receipt enhancement pipeline (SRS FR-04, TSD §25, ML-01).

Stages (applied adaptively based on quality assessment):
1. Document boundary detection + perspective correction (when skewed)
2. Deskew (rotation correction from minAreaRect / projection profile)
3. Denoise (fast non-local means)
4. Illumination normalization (morphological background subtraction / CLAHE)
5. Local contrast enhancement
6. Upscale (when small/low-DPI)
7. Mild unsharp sharpening

CRITICAL RULE (TSD §25): enhancement may improve visibility but must not be
treated as proof that an unreadable character actually existed. We never
synthesize content — only classical image operations. Each applied operation
is recorded in the metadata (provenance for the derivative).
"""

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MIN_SIDE = 400  # upscale below this
SKEW_DEGREE_THRESHOLD = 1.0


@dataclass
class EnhancementResult:
    image: np.ndarray
    operations: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    quality_before: float | None = None
    quality_after: float | None = None


def _find_contour(img: np.ndarray):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < 0.3 * img.shape[0] * img.shape[1]:
        return None
    peri = cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, 0.02 * peri, True)
    return approx if len(approx) == 4 else None


def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]
    rect[3] = pts[np.argmax(d)]
    return rect


def _four_point_transform(img: np.ndarray, pts: np.ndarray) -> np.ndarray:
    rect = _order_points(pts)
    (tl, tr, br, bl) = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_w = int(max(width_a, width_b))
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_h = int(max(height_a, height_b))
    if max_w < 20 or max_h < 20:
        return img
    dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
    m = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(img, m, (max_w, max_h))


def _estimate_skew(gray: np.ndarray, max_angle: float = 15.0) -> float:
    """Projection-profile skew estimate over the dark-ink mask."""
    inv = cv2.bitwise_not(gray)
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 100:
        return 0.0
    # Coarse search.
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_angle, max_angle + 0.5, 0.5):
        h, w = inv.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rot = cv2.warpAffine(inv, m, (w, h), flags=cv2.INTER_NEAREST)
        proj = rot.sum(axis=1)
        score = float(np.sum((proj[1:] - proj[:-1]) ** 2))
        if score > best_score:
            best_score, best_angle = score, angle
    return best_angle


def _deskew(img: np.ndarray, angle: float) -> tuple[np.ndarray, str]:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        img,
        m,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated, f"deskew({angle:.1f}deg)"


def enhance_image(img: np.ndarray, quality_before: float | None = None) -> EnhancementResult:
    operations: list[str] = []
    metrics: dict = {}
    work = img

    # 1-2. Boundary + perspective correction.
    quad = _find_contour(work)
    if quad is not None:
        warped = _four_point_transform(work, quad.reshape(4, 2).astype(np.float32))
        if warped.shape[:2] != work.shape[:2]:
            work = warped
            operations.append("perspective_correction")

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work

    # 3. Deskew.
    angle = _estimate_skew(gray)
    if abs(angle) >= SKEW_DEGREE_THRESHOLD:
        work, op = _deskew(work, angle)
        operations.append(op)
        metrics["skew_angle"] = round(angle, 2)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work

    # 4. Denoise.
    if gray.std() < 40:  # noisy flat receipts
        work = cv2.fastNlMeansDenoisingColored(work, None, 3, 3, 7, 21)
        operations.append("denoise_nlm")

    # 5. Illumination normalization (background estimation via morphological open).
    bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN, np.ones((25, 25), np.uint8))
    bg = cv2.GaussianBlur(bg, (51, 51), 0)
    norm = cv2.divide(gray, bg, scale=255)
    metrics["illumination_std_before"] = round(float(gray.std()), 2)
    metrics["illumination_std_after"] = round(float(norm.std()), 2)
    if metrics["illumination_std_after"] > metrics["illumination_std_before"]:
        work = norm
        operations.append("illumination_normalization")
        gray = norm
    else:
        work = gray if work.ndim == 2 else work

    # 6. CLAHE local contrast.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work
    contrast = clahe.apply(gray)
    if contrast.std() >= gray.std() * 0.98:
        work = contrast
        operations.append("contrast_clahe")
        gray = contrast

    # 7. Adaptive thresholding — binarization for clearly light-background,
    # faded receipts (thermal fade, washed print). Gated so crisp documents
    # keep their tonal detail: the background must be light, overall contrast
    # low, AND the darkest 5% of pixels must not be truly dark (after
    # normalization, crisp ink reaches darkness ~200+; faded ink stays well
    # below). Recorded as an operation (provenance).
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work
    if (
        float(gray.mean()) > 150.0
        and gray.std() < 80.0
        and float(np.percentile(255 - gray, 95)) < 140.0
    ):
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blockSize=51, C=15
        )
        # Accept the binarization only if it increases local edge energy.
        edge_before = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        edge_after = float(cv2.Laplacian(thresh, cv2.CV_64F).var())
        if edge_after > edge_before:
            work = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
            operations.append("adaptive_threshold")
            metrics["adaptive_threshold_edge_var_before"] = round(edge_before, 2)
            metrics["adaptive_threshold_edge_var_after"] = round(edge_after, 2)

    # 8. Upscale small images.
    min_side = min(work.shape[:2])
    if min_side < MIN_SIDE:
        scale = min(settings.enhancement_upscale_max, MIN_SIDE / min_side)
        work = cv2.resize(work, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        operations.append(f"upscale(x{scale:.2f})")

    # 9. Mild unsharp mask.
    blurred = cv2.GaussianBlur(work, (0, 0), 3)
    work = cv2.addWeighted(work, 1.25, blurred, -0.25, 0)
    operations.append("sharpen_unsharp")

    # Convert to BGR for storage consistency.
    if work.ndim == 2:
        work = cv2.cvtColor(work, cv2.COLOR_GRAY2BGR)

    from PIL import Image

    from app.ai.pipeline import quality_score

    def _q(arr: np.ndarray) -> float:
        pil = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr)
        return quality_score(pil)

    return EnhancementResult(
        image=work,
        operations=operations,
        metrics=metrics,
        quality_before=quality_before,
        quality_after=round(_q(work), 4),
    )
