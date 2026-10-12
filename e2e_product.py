"""Live E2E for Product Intelligence (SRS ML-04/ML-06/ML-07).

Runs against the dev API (http://127.0.0.1:8000) + worker. It:
  1. registers a throwaway user
  2. uploads three receipts (clean alias / typo+brand / unknown product)
  3. waits for the real AI pipeline to finish
  4. asserts the matching invariants (never auto-link low confidence,
     preserve originals, surface candidates)
  5. drives the confirm / reject / create-from-receipt decisions
"""

import io
import time
import uuid

import httpx

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = f"e2e-pi-{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "Passw0rd!123"

RESULTS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    print(RESULTS[-1], flush=True)


def make_receipt(lines: list[tuple[str, str]]) -> bytes:
    size = 32
    img = Image.new("RGB", (760, 30 + 48 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    font = _font(size)
    y = 12
    for left, right in lines:
        d.text((28, y), left, fill="black", font=font)
        if right:
            d.text((560, y), right, fill="black", font=font)
        y += 48
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def wait_for_doc(client: httpx.Client, token: str, doc_id: int, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"{BASE}/documents/{doc_id}/status", headers={"Authorization": f"Bearer {token}"})
        last = r.json().get("data", {})
        st = last.get("status")
        if st in ("COMPLETED", "REVIEW_REQUIRED", "FAILED"):
            return last
        time.sleep(2)
    raise RuntimeError(f"document {doc_id} did not finish: {last}")


def main() -> int:
    client = httpx.Client(timeout=60)

    r = client.post(f"{BASE}/auth/register", json={"email": EMAIL, "password": PASSWORD, "display_name": "E2E PI"})
    check("register", r.status_code == 201, f"status={r.status_code}")
    token = r.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    def upload(name: str, data: bytes) -> int:
        r = client.post(f"{BASE}/documents", headers=auth, files={"file": (name, data, "image/png")})
        assert r.status_code == 201, r.text
        return r.json()["data"]["document"]["id"]

    # --- Doc 1: clean receipt alias -> expect AUTO_MATCHED (never review) ---
    d1 = upload("ac_alias.png", make_receipt([
        ("NORTECH HOME APPLIANCES", ""),
        ("Date: 2026-08-30", ""),
        ("SPLIT AC 1.5T", "2899.00"),
        ("Tax", "260.91"),
        ("TOTAL", "3159.91"),
    ]))
    s1 = wait_for_doc(client, token, d1)
    m1 = client.get(f"{BASE}/documents/{d1}/product-match", headers=auth).json()["data"]["match"]
    check("doc1 alias auto-matched", m1 and m1["status"] == "AUTO_MATCHED", f"status={m1 and m1['status']} method={m1 and m1['match_method']} conf={m1 and m1['match_confidence']}")
    check("doc1 product linked", m1 and m1["product_id"] is not None, f"product_id={m1 and m1['product_id']}")
    check("doc1 original preserved", m1 and (m1["product_name_raw"] or "").upper().startswith("SPLIT AC"), f"raw={m1 and m1['product_name_raw']!r}")

    # --- Doc 2: typo in name + brand present -> expect REVIEW_REQUIRED, NOT auto-linked ---
    d2 = upload("keyboard_typo.png", make_receipt([
        ("BLUEBOX ELECTRONICS", ""),
        ("Date: 2026-08-30", ""),
        ("QTech Mechanical Keyboad K870", "89.99"),
        ("Tax", "0.00"),
        ("TOTAL", "89.99"),
    ]))
    s2 = wait_for_doc(client, token, d2)
    m2 = client.get(f"{BASE}/documents/{d2}/product-match", headers=auth).json()["data"]["match"]
    auto2 = m2 and m2["status"] == "AUTO_MATCHED"
    check("doc2 typo NOT auto-linked", not auto2, f"status={m2 and m2['status']} conf={m2 and m2['match_confidence']}")
    check("doc2 has candidates to review", m2 and len(m2["candidates"]) >= 1, f"n={m2 and len(m2['candidates'])}")
    # Confirm the top candidate through the API.
    if m2 and m2["candidates"]:
        top = m2["candidates"][0]["product_id"]
        r = client.post(f"{BASE}/documents/{d2}/product-match", headers=auth, json={"action": "confirm", "product_id": top})
        m2b = r.json()["data"]["match"]
        check("doc2 confirm links product", r.status_code == 200 and m2b["status"] == "CONFIRMED" and m2b["product_id"] == top, f"status={m2b['status']}")

    # --- Doc 3: unknown product -> NO_MATCH, then create-from-receipt ---
    d3 = upload("unknown.png", make_receipt([
        ("BLUEBOX ELECTRONICS", ""),
        ("Date: 2026-08-30", ""),
        ("Ceramic Espresso Grinder X1", "149.99"),
        ("Tax", "0.00"),
        ("TOTAL", "149.99"),
    ]))
    s3 = wait_for_doc(client, token, d3)
    m3 = client.get(f"{BASE}/documents/{d3}/product-match", headers=auth).json()["data"]["match"]
    check("doc3 unknown not auto-linked", m3 and m3["status"] != "AUTO_MATCHED", f"status={m3 and m3['status']}")
    r = client.post(f"{BASE}/documents/{d3}/product-match", headers=auth, json={"action": "create_confirm"})
    body3 = r.json()["data"]
    check("doc3 create-from-receipt", r.status_code == 200 and body3["match"]["status"] == "CONFIRMED" and body3["match"]["product_id"] is not None, f"product_id={body3['match']['product_id']}")
    check("doc3 created product named from receipt", "Espresso Grinder" in (body3["product"]["canonical_name"]), f"name={body3['product']['canonical_name']!r}")

    # --- Cross-user isolation: a second user must not see doc1's match ---
    email2 = f"e2e-pi2-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(f"{BASE}/auth/register", json={"email": email2, "password": PASSWORD, "display_name": "E2E PI 2"})
    token2 = r.json()["data"]["access_token"]
    r = client.get(f"{BASE}/documents/{d1}/product-match", headers={"Authorization": f"Bearer {token2}"})
    check("cross-user 404", r.status_code == 404, f"status={r.status_code}")

    failed = [x for x in RESULTS if x.startswith("[FAIL]")]
    print(f"\nE2E PRODUCT INTEL: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
