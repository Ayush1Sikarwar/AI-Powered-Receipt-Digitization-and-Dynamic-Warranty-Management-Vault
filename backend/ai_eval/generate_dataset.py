"""Generate the controlled evaluation dataset (PRD §5).

Each sample is a rendered receipt with EXACT ground truth captured at
generation time. Categories (PRD): clear, faded, low-contrast, crumpled,
skewed, shadowed, water-damaged, plus a multiple-retailer-format set.

Usage:
    python -m ai_eval.generate_dataset [--out ai_eval/dataset] [--per-category 5]

Output:
    <out>/manifest.json          (PRD per-document fields)
    <out>/images/<category>/<document_id>.png
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

CATEGORIES = [
    "clear",
    "faded",
    "low_contrast",
    "crumpled",
    "skewed",
    "shadowed",
    "water_damaged",
    "retailer_formats",
]

PRODUCTS = {
    "nortech": [
        ("QLED 55in 4K TV", (1199.00, 1499.00)),
        ("Inverter Split AC 1.5 Ton", (899.00, 1099.00)),
        ("Front Load Washing Machine 8kg", (649.00, 799.00)),
        ("Air Fryer 5.5L Digital", (129.00, 189.00)),
    ],
    "citymart": [
        ("Whole Milk 1L", (1.29, 2.19)),
        ("Sourdough Bread 500g", (3.49, 4.29)),
        ("Organic Eggs 12pk", (4.99, 6.49)),
        ("Cold Brew Coffee 355ml", (5.29, 6.99)),
    ],
    "bluebox": [
        ("4K OLED TV QT55Q90RA", (1499.00, 1899.00)),
        ("Gaming Laptop GX16-R", (1899.00, 2499.00)),
        ("Noise Cancelling Headphones X90", (279.00, 399.00)),
        ("Mechanical Keyboard K870", (149.00, 229.00)),
    ],
}


def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# --- retailer formats -------------------------------------------------------
# Each template returns (lines, fields, doc_info):
#   lines    - (text, font_size) pairs rendered onto the receipt
#   fields   - extractor field name -> ground truth rendered on the document
#              (None = no ground truth on this document for that field)
#   doc_info - PRD manifest-level values (product/price) + optional
#              exempt_fields (legitimately extractable but ambiguous)

def _nortech(rng: random.Random) -> tuple[list[tuple[str, int]], dict, dict]:
    product, (lo, hi) = rng.choice(PRODUCTS["nortech"])
    unit = round(rng.uniform(lo, hi), 2)
    service = round(unit * 0.08, 2)
    total = round(unit + service, 2)
    date = f"2026-{rng.randint(7, 8):02d}-{rng.randint(1, 28):02d}"
    receipt = f"R-{rng.randint(100000, 999999)}"
    lines = [
        ("NORTECH HOME APPLIANCES", 22),
        ("12 Industrial Park Rd, Springfield", 15),
        (f"Receipt No: {receipt}", 16),
        (f"Date: {date}", 16),
        (f"{product} ${unit:.2f}", 16),
        ("Warranty Service 2yr " + f"${service:.2f}", 16),
        ("Qty: 1", 16),
        (f"Total: ${total:.2f}", 18),
    ]
    fields = {
        "retailer": "NORTECH HOME APPLIANCES",
        "invoice_number": receipt,
        "purchase_date": date,
        "product_name": product,
        "quantity": "1",
        "unit_price": f"{unit:.2f}",
        "total_price": f"{total:.2f}",
        "currency": "USD",
    }
    doc_info = {"product": product, "price": f"{unit:.2f}"}
    return lines, fields, doc_info


def _citymart(rng: random.Random) -> tuple[list[tuple[str, int]], dict, dict]:
    items = rng.sample(PRODUCTS["citymart"], 2)
    lines: list[tuple[str, int]] = [
        ("CITYMART GROCERS", 22),
        ("Store 042 - Riverside Ave", 15),
    ]
    txn = f"{rng.randint(100000, 999999)}"
    day = rng.randint(1, 28)
    month = rng.choice([7, 8])
    date_iso = f"2026-{month:02d}-{day:02d}"
    lines.append((f"Receipt No: {txn}", 16))
    lines.append(((f"{day} {'July' if month == 7 else 'August'} 2026"), 16))
    sub = 0.0
    primary_product, primary_unit = None, None
    for i, (name, (lo, hi)) in enumerate(items):
        unit = round(rng.uniform(lo, hi), 2)
        qty = rng.randint(1, 3)
        sub += unit * qty
        if i == 0:
            primary_product, primary_unit = name, unit
        lines.append((f"{name} x{qty} {unit * qty:.2f}", 16))
    tax = round(sub * 0.08, 2)
    total = round(sub + tax, 2)
    lines.append(("Qty: 1", 16))
    lines.append((f"Subtotal {sub:.2f}", 16))
    lines.append((f"Tax {tax:.2f}", 16))
    lines.append((f"TOTAL {total:.2f}", 18))
    fields = {
        "retailer": "CITYMART GROCERS",
        "invoice_number": txn,
        "purchase_date": date_iso,
        # Multi-item receipt: "primary product" is ambiguous, so no product/
        # unit-price ground truth. No currency symbol or code is printed, so
        # no currency ground truth either (nothing to extract with evidence).
        "product_name": None,
        "quantity": "1",
        "unit_price": None,
        "tax": f"{tax:.2f}",
        "total_price": f"{total:.2f}",
        "currency": None,
    }
    doc_info = {
        "product": primary_product,
        "price": f"{primary_unit:.2f}",
        # Legitimately extractable but ambiguous on multi-item receipts —
        # predictions here are not counted as false positives.
        "exempt_fields": ["product_name", "unit_price"],
    }
    return lines, fields, doc_info


def _bluebox(rng: random.Random) -> tuple[list[tuple[str, int]], dict, dict]:
    product, (lo, hi) = rng.choice(PRODUCTS["bluebox"])
    unit = round(rng.uniform(lo, hi), 2)
    inv = f"INV-2026-{rng.randint(1000, 9999)}"
    date = f"2026-{rng.randint(7, 8):02d}-{rng.randint(1, 28):02d}"
    sku = f"BX-{product.split()[0][:2].upper()}{rng.randint(10, 99)}"
    serial = f"SN{rng.randint(100000, 999999)}-{rng.randint(10, 99)}"
    lines = [
        ("BLUEBOX ELECTRONICS", 22),
        ("INVOICE", 16),
        (f"Invoice No: {inv}", 16),
        (f"Date: {date}", 16),
        (f"{product}", 16),
        (f"Model: {product.split()[-1]}", 16),
        (f"SKU: {sku}", 16),
        (f"Serial No: {serial}", 16),
        ("Qty: 1", 16),
        (f"Amount Due: ${unit:.2f}", 18),
    ]
    fields = {
        "retailer": "BLUEBOX ELECTRONICS",
        "invoice_number": inv,
        "purchase_date": date,
        "product_name": product,
        "model_number": product.split()[-1],
        "sku": sku,
        "serial_number": serial,
        "quantity": "1",
        "unit_price": f"{unit:.2f}",
        "total_price": f"{unit:.2f}",
        "currency": "USD",
    }
    doc_info = {"product": product, "price": f"{unit:.2f}"}
    return lines, fields, doc_info


RETAILERS = [
    ("nortech", _nortech),
    ("citymart", _citymart),
    ("bluebox", _bluebox),
]

# --- rendering --------------------------------------------------------------

def render_receipt(lines: list[tuple[str, int]]) -> Image.Image:
    font_cache: dict[int, object] = {}

    def f(size: int):
        if size not in font_cache:
            font_cache[size] = _font(size)
        return font_cache[size]

    width = 800
    line_h = 40
    margin = 40
    height = margin * 2 + line_h * len(lines)
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    y = margin
    for text, size in lines:
        d.text((margin, y), text, fill="black", font=f(size))
        y += line_h
    return img


# --- degradations (deterministic per seed) ----------------------------------

def _fade(img: Image.Image, rng: random.Random) -> Image.Image:
    arr = np.asarray(img, dtype=np.float32) / 255.0
    gamma = rng.uniform(1.5, 1.9)
    arr = arr**gamma
    noise = rng.uniform(4, 10)
    np_rng = np.random.RandomState(rng.getrandbits(31))
    arr = np.clip(arr + (np_rng.random_sample(arr.shape) - 0.5) * 2 * noise / 255.0, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _low_contrast(img: Image.Image, rng: random.Random) -> Image.Image:
    arr = np.asarray(img, dtype=np.float32)
    mean = arr.mean()
    factor = rng.uniform(0.35, 0.5)
    arr = mean + (arr - mean) * factor
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _crumple(img: Image.Image, rng: random.Random) -> Image.Image:
    import cv2

    arr = np.asarray(img)
    h, w = arr.shape[:2]
    # Smooth random warp (low-res displacement grid, upsampled).
    grid = 6
    np_rng = np.random.RandomState(rng.getrandbits(31))
    disp = np_rng.random_sample((grid, grid, 2)).astype(np.float32)
    amp = rng.uniform(6, 14)
    disp = cv2.resize(disp, (w, h), interpolation=cv2.INTER_CUBIC) * amp
    # (1,w) + (h,w) broadcasts to (h,w) — no explicit tiling.
    map_x = np.arange(w, dtype=np.float32)[None, :] + disp[..., 0]
    map_y = np.arange(h, dtype=np.float32)[:, None] + disp[..., 1]
    warped = cv2.remap(arr, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # Creases: a few thin dark lines with soft shadow.
    out = warped.astype(np.float32)
    for _ in range(rng.randint(2, 3)):
        y0 = rng.randint(0, h)
        y1 = rng.randint(0, h)
        xs = np.arange(w, dtype=np.float32)
        np_rng = np.random.RandomState(rng.getrandbits(31))
        ys = (y0 + (y1 - y0) * xs / w + (np_rng.random_sample(w) - 0.5) * 6).astype(int)
        xsi = np.clip(xs.astype(int), 0, w - 1)
        for dy, alpha in ((0, 0.25), (2, 0.12)):
            idx = np.clip(ys + dy, 0, h - 1)
            out[idx, xsi] = out[idx, xsi] * (1 - alpha)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def _skew(img: Image.Image, rng: random.Random) -> Image.Image:
    angle = rng.choice([-1, 1]) * rng.uniform(4.0, 8.0)
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white", expand=False)


def _shadow(img: Image.Image, rng: random.Random) -> Image.Image:
    arr = np.asarray(img, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    y = np.linspace(1.0, rng.uniform(0.5, 0.65), h)[:, None]
    x = np.linspace(1.0, rng.uniform(0.75, 0.85), w)[None, :]
    grad = y * x
    arr = np.clip(arr * grad[..., None], 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _water_damage(img: Image.Image, rng: random.Random) -> Image.Image:
    arr = np.asarray(img, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(rng.randint(2, 3)):
        cx, cy = rng.randint(50, w - 50), rng.randint(50, h - 50)
        rx, ry = rng.randint(40, 110), rng.randint(30, 80)
        mask = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        blob = np.clip(1.0 - mask, 0, 1) * rng.uniform(0.25, 0.4)
        arr = arr * (1.0 - blob[..., None])
    out = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(1.0, 2.0)))
    return out


DEGRADATIONS = {
    "clear": lambda img, rng: img,
    "faded": _fade,
    "low_contrast": _low_contrast,
    "crumpled": _crumple,
    "skewed": _skew,
    "shadowed": _shadow,
    "water_damaged": _water_damage,
    "retailer_formats": lambda img, rng: img,
}


# --- generation -------------------------------------------------------------

def generate(out_dir: Path, per_category: int = 5) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    img_root = out_dir / "images"
    manifest: dict[str, dict] = {}

    for category in CATEGORIES:
        cat_dir = img_root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        # Slots cycle through retailer formats (i % len(RETAILERS)), so any
        # per-category count is reachable; each slot still gets its own
        # deterministic content via the per-slot seed below.
        slots = list(range(per_category))
        for i in slots:
            rng = random.Random(f"{category}:{i}")
            fmt_key, template = RETAILERS[i % len(RETAILERS)]
            lines, fields, doc_info = template(rng)
            text = "\n".join(t for t, _ in lines)
            img = render_receipt(lines)
            img = DEGRADATIONS[category](img, rng)
            doc_id = f"{category}_{fmt_key}_{i:02d}"
            path = cat_dir / f"{doc_id}.png"
            img.save(path, "PNG")
            manifest[doc_id] = {
                "document_id": doc_id,
                "image": f"images/{category}/{doc_id}.png",
                "ground_truth_text": text,
                "retailer": fields["retailer"],
                "purchase_date": fields["purchase_date"],
                "product": doc_info["product"],
                "sku": fields.get("sku"),
                "price": doc_info["price"],
                "currency": fields.get("currency"),
                "quantity": fields.get("quantity"),
                "total": fields.get("total_price"),
                "tax": fields.get("tax"),
                "model": fields.get("model_number"),
                "serial": fields.get("serial_number"),
                "receipt_number": fields.get("invoice_number"),
                "quality_category": category,
                "retailer_format": fmt_key,
                "fields": fields,
                "exempt_fields": doc_info.get("exempt_fields", []),
                "synthetic": True,
            }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(manifest)} samples -> {manifest_path}")
    return manifest_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).parent / "dataset"))
    ap.add_argument("--per-category", type=int, default=5)
    args = ap.parse_args()
    generate(Path(args.out), args.per_category)


if __name__ == "__main__":
    main()
