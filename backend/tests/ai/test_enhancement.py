"""AI tests: enhancement pipeline (TSD §25)."""

import numpy as np

from app.ai.preprocessing.enhance import enhance_image


def _gray_bgr(arr: np.ndarray) -> np.ndarray:
    return np.stack([arr] * 3, axis=-1)


def test_enhance_returns_image_and_operations():
    arr = np.full((300, 500), 240, dtype=np.uint8)
    arr[100:150, 50:300] = 30  # dark block = ink
    result = enhance_image(_gray_bgr(arr))
    assert result.image.shape[0] >= 300
    assert isinstance(result.operations, list)
    assert result.operations, "expected at least one operation recorded"
    # metrics is a flat dict of numeric diagnostics (illumination is always computed).
    assert result.metrics
    assert "illumination_std_before" in result.metrics
    assert all(isinstance(v, (int, float)) for v in result.metrics.values())


def test_enhance_records_skew_correction():
    # Build a skewed image by rotating content.
    base = np.full((400, 600), 250, dtype=np.uint8)
    base[180:220, 80:520] = 40
    import cv2

    img = _gray_bgr(base)
    m = cv2.getRotationMatrix2D((300, 200), 6.0, 1.0)
    rotated = cv2.warpAffine(img, m, (600, 400), borderValue=250)
    result = enhance_image(rotated)
    assert any(op.startswith("deskew") for op in result.operations), result.operations
    assert abs(result.metrics.get("skew_angle", 0)) >= 1.0


def test_enhance_upscales_small_images():
    small = np.full((120, 160), 230, dtype=np.uint8)
    small[30:60, 20:100] = 50
    result = enhance_image(_gray_bgr(small))
    assert any(op.startswith("upscale") for op in result.operations)
    assert result.image.shape[0] > 120


def test_enhance_never_invents_content_shape():
    """Enhancement must not change the image dimensions drastically (no
    content synthesis): output aspect ratio stays within 5% of input."""
    arr = np.full((250, 420), 245, dtype=np.uint8)
    arr[80:120, 40:380] = 35
    result = enhance_image(_gray_bgr(arr))
    h0, w0 = 250, 420
    h1, w1 = result.image.shape[:2]
    # Upscaling preserves aspect ratio.
    assert abs((w1 / h1) - (w0 / h0)) / (w0 / h0) < 0.05


def test_adaptive_thresholding_on_low_contrast_light_image():
    # Light background, low-contrast dark "ink" (faded thermal print).
    arr = np.full((300, 500), 215, dtype=np.uint8)
    arr[100:150, 50:300] = 165
    img = _gray_bgr(arr)
    result = enhance_image(img)
    assert "adaptive_threshold" in result.operations, result.operations
    assert result.metrics["adaptive_threshold_edge_var_after"] > result.metrics["adaptive_threshold_edge_var_before"]


def test_crisp_image_not_binarized():
    # High-contrast crisp document: adaptive thresholding must stay off so
    # tonal detail is preserved.
    arr = np.full((300, 500), 250, dtype=np.uint8)
    arr[100:150, 50:300] = 10
    result = enhance_image(_gray_bgr(arr))
    assert "adaptive_threshold" not in result.operations, result.operations
