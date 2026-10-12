"""Live E2E for the complete MVP claims workflow (SRS FR-16, TSD §23/§33/§57).

Against the dev API (http://127.0.0.1:8000) with the worker running, using a
REAL stored test document:

  1. register a user
  2. upload a real receipt image -> the AI pipeline stores + processes it
  3. confirm one extracted field (user-confirmed provenance)
  4. upload a real warranty-card image -> stored + classified
  5. create a purchase linked to the stored receipt (with model/serial)
  6. create a claim; SELECT the stored receipt + warranty card as claim
     evidence (document attachments); upload a photo as evidence
  7. generate the claim package and verify every section + its provenance
     (verified / AI extracted / user confirmed / external source / inferred /
     computed)
  8. download the claim-ready report (markdown)
  9. download the portable evidence bundle (ZIP) and verify its files
 10. exercise the MVP boundary: no submission endpoints (stop at CLAIM_READY)
 11. verify the portable account export bundle (manifest + files)
"""

import hashlib
import io
import json
import time
import uuid
import zipfile
from datetime import date, timedelta

import httpx
from PIL import Image, ImageDraw, ImageFont

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = f"e2e-claims-{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "Passw0rd!123"

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


def wait_for_doc(client: httpx.Client, auth: dict, doc_id: int, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"{BASE}/documents/{doc_id}/status", headers=auth)
        last = r.json().get("data", {})
        st = last.get("status")
        if st in ("COMPLETED", "REVIEW_REQUIRED", "FAILED"):
            return last
        time.sleep(2)
    raise RuntimeError(f"document {doc_id} did not finish: {last}")


def main() -> int:
    client = httpx.Client(timeout=60)
    r = client.post(
        f"{BASE}/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "E2E Claims", "country": "IN"},
    )
    check("register", r.status_code == 201, f"status={r.status_code}")
    token = r.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # --- 2. real stored receipt document ------------------------------------
    r = client.get(f"{BASE}/products/search", params={"q": "QTech Inverter Split AC"}, headers=auth)
    ac = next((p for p in r.json()["data"]["items"] if p["canonical_name"] == "QTech Inverter Split AC 1.5 Ton"), None)
    check("catalog has QTech AC", ac is not None)
    if not ac:
        return 1

    purchase_date = (date.today() - timedelta(days=14)).isoformat()
    receipt_png = make_receipt([
        ("QTECH HOME APPLIANCES", ""),
        (f"Date: {purchase_date}", ""),
        ("SPLIT AC 1.5T", "2899.00"),
        ("Model AR15FSSY", ""),
        ("Serial: SN-AC-2026-001", ""),
        ("TOTAL", "2899.00"),
    ])
    r = client.post(f"{BASE}/documents", headers=auth, files={"file": ("ac-receipt.png", receipt_png, "image/png")})
    check("receipt uploaded", r.status_code == 201, f"status={r.status_code}")
    receipt_id = r.json()["data"]["document"]["id"]
    s = wait_for_doc(client, auth, receipt_id)
    check(
        "receipt processed by pipeline (stored document)",
        s["status"] in ("COMPLETED", "REVIEW_REQUIRED"),
        f"status={s['status']}",
    )

    # --- 3. confirm one extracted field --------------------------------------
    fields = client.get(f"{BASE}/documents/{receipt_id}/fields", headers=auth).json()["data"]["items"]
    by_name = {f["field_name"]: f for f in fields}
    check("receipt extracted product_name", by_name.get("product_name") is not None, f"fields={sorted(by_name)}")
    check("receipt extracted serial_number", by_name.get("serial_number") is not None)

    confirmed_name = None
    if by_name.get("purchase_date"):
        r = client.patch(
            f"{BASE}/documents/{receipt_id}/fields",
            headers=auth,
            json={"field_name": "purchase_date", "action": "confirm"},
        )
        check("purchase_date confirmed (user-confirmed provenance)", r.status_code == 200, r.text[:120])
        confirmed_name = "purchase_date"
    elif by_name.get("product_name"):
        r = client.patch(
            f"{BASE}/documents/{receipt_id}/fields",
            headers=auth,
            json={"field_name": "product_name", "action": "confirm"},
        )
        check("product_name confirmed (user-confirmed provenance)", r.status_code == 200, r.text[:120])
        confirmed_name = "product_name"

    # --- 4. real stored warranty card ----------------------------------------
    warranty_png = make_receipt([
        ("QTECH LIMITED WARRANTY", ""),
        ("WARRANTY PERIOD: 12 MONTHS", ""),
        ("Compressor: 60 MONTHS", ""),
        ("Serial: SN-AC-2026-001", ""),
    ])
    r = client.post(
        f"{BASE}/documents", headers=auth, files={"file": ("ac-warranty-card.png", warranty_png, "image/png")}
    )
    check("warranty card uploaded", r.status_code == 201, f"status={r.status_code}")
    card_id = r.json()["data"]["document"]["id"]
    s = wait_for_doc(client, auth, card_id)
    check(
        "warranty card processed (stored document)",
        s["status"] in ("COMPLETED", "REVIEW_REQUIRED"),
        f"status={s['status']}",
    )
    doc_type = client.get(f"{BASE}/documents/{card_id}", headers=auth).json()["data"]["document_type"]
    check("warranty card classified as WARRANTY_CARD", doc_type == "WARRANTY_CARD", f"type={doc_type}")

    # --- 5. purchase linked to the stored receipt ----------------------------
    r = client.post(
        f"{BASE}/purchases",
        headers=auth,
        json={
            "document_id": receipt_id,
            "purchase_date": purchase_date,
            "warranty_region": "IN",
            "total": 2899.0,
            "items": [
                {
                    "product_id": ac["id"],
                    "product_name": "SPLIT AC 1.5T",
                    "model_number": "AR15FSSY",
                    "serial_number": "SN-AC-2026-001",
                    "quantity": 1,
                    "unit_price": 2899.0,
                    "line_total": 2899.0,
                }
            ],
        },
    )
    check("purchase created (linked to stored receipt)", r.status_code == 201, f"status={r.status_code}")
    purchase = r.json()["data"]["purchase"]
    check("purchase has warranty (mapped)", purchase.get("warranty_bucket") in ("active", "expiring", "expired"),
          f"bucket={purchase.get('warranty_bucket')}")

    # --- 6. claim + evidence selection + photo evidence -----------------------
    r = client.post(
        f"{BASE}/claims",
        headers=auth,
        json={
            "purchase_id": purchase["id"],
            "issue_description": "Compressor noise; unit not cooling under load",
            "issue_category": "DEFECT",
            "contact_name": "E2E Claimant",
            "contact_email": EMAIL,
        },
    )
    check("claim created (DRAFT)", r.status_code == 201 and r.json()["data"]["claim"]["status"] == "DRAFT")
    claim = r.json()["data"]["claim"]

    r = client.post(
        f"{BASE}/claims/{claim['id']}/documents",
        headers=auth,
        json={"document_id": receipt_id, "role": "RECEIPT"},
    )
    check("receipt selected as claim evidence", r.status_code == 201, r.text[:120])
    r = client.post(
        f"{BASE}/claims/{claim['id']}/documents",
        headers=auth,
        json={"document_id": card_id, "role": "WARRANTY"},
    )
    check("warranty card selected as claim evidence", r.status_code == 201, r.text[:120])

    photo_png = make_receipt([("COMPRESSOR DAMAGE PHOTO", ""), (f"unit {purchase_date}", "")])
    r = client.post(
        f"{BASE}/claims/{claim['id']}/evidence",
        headers=auth,
        files={"file": ("compressor-damage.png", photo_png, "image/png")},
    )
    check("photo evidence uploaded", r.status_code == 201, f"status={r.status_code}")
    photo_sha = hashlib.sha256(photo_png).hexdigest()

    # --- 7. claim package with provenance -------------------------------------
    r = client.post(f"{BASE}/claims/{claim['id']}/package", headers=auth)
    check("package generated -> READY", r.status_code == 200 and r.json()["data"]["claim"]["status"] == "READY")
    pkg = client.get(f"{BASE}/claims/{claim['id']}/package", headers=auth).json()["data"]["package"]

    check("purchase provenance present (INFERRED until confirmed)",
          pkg["purchase"]["provenance"] == "INFERRED", f"p={pkg['purchase']['provenance']}")
    item = pkg["items"][0]
    check("item carries model + serial + provenance",
          item["model_number"] == "AR15FSSY" and item["serial_number"] == "SN-AC-2026-001"
          and item["provenance"] == "USER_CONFIRMED")
    w = pkg["warranty"][0]
    check("warranty source is EXTERNAL_SOURCE with trust level",
          w["source_provenance"] == "EXTERNAL_SOURCE" and w["source"] is not None
          and 1 <= w["source"]["trust_level"] <= 5)
    check("timelines are COMPUTED", all(t["provenance"] == "COMPUTED" for t in w["timelines"]))
    check("match provenance recorded", w["match_provenance"] in ("AI_EXTRACTED", "USER_CONFIRMED"),
          f"m={w['match_provenance']}")
    rec = pkg["receipt"]
    check("receipt in package (stored document)", rec is not None and rec["id"] == receipt_id)
    rfields = {f["field_name"]: f for f in rec.get("fields", [])}
    check("receipt fields carry per-field provenance", len(rfields) >= 2, f"n={len(rfields)}")
    if confirmed_name and confirmed_name in rfields:
        check("confirmed field shows USER_CONFIRMED", rfields[confirmed_name]["provenance"] == "USER_CONFIRMED",
              f"field={confirmed_name}")
    ai_fields = [n for n, f in rfields.items() if f["provenance"] == "AI_EXTRACTED"]
    check("unconfirmed fields stay AI_EXTRACTED", len(ai_fields) >= 1, f"ai={ai_fields}")
    check("warranty document in package", len(pkg["warranty_documents"]) == 1
          and pkg["warranty_documents"][0]["id"] == card_id)
    check("evidence has media_kind + provenance",
          pkg["evidence"][0]["media_kind"] == "PHOTO" and pkg["evidence"][0]["provenance"] == "USER_CONFIRMED"
          and pkg["evidence"][0]["sha256"] == photo_sha)
    check("submission boundary in package (CLAIM_READY, no external submission)",
          pkg["submission"]["mode"] == "CLAIM_READY" and pkg["submission"]["external_submission"] is False)
    check("boundary note names CAPTCHA/MFA/anti-bot",
          "CAPTCHA" in pkg["submission"]["note"] and "MFA" in pkg["submission"]["note"]
          and "anti-bot" in pkg["submission"]["note"])
    check("claim-ready report present", "## Manufacturer Warranty Coverage" in pkg.get("report", ""))

    # --- 8. report download ----------------------------------------------------
    r = client.get(f"{BASE}/claims/{claim['id']}/package/report", headers=auth)
    check("report download (markdown)", r.status_code == 200
          and r.headers["content-type"].startswith("text/markdown")
          and "# Claim-Ready Report" in r.text)
    check("report shows serial + provenance", "SN-AC-2026-001" in r.text and "provenance: COMPUTED" in r.text)

    # --- 9. evidence bundle ------------------------------------------------------
    r = client.get(f"{BASE}/claims/{claim['id']}/package/bundle", headers=auth)
    check("bundle download (zip)", r.status_code == 200 and r.headers["content-type"].startswith("application/zip"))
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    check("bundle has package.json + report + README",
          "package.json" in names and "claim-report.md" in names and "README.txt" in names)
    ev_files = [n for n in names if n.startswith("evidence/")]
    doc_files = [n for n in names if n.startswith("documents/")]
    check("bundle contains evidence + document files", len(ev_files) == 1 and len(doc_files) == 2,
          f"ev={ev_files} docs={doc_files}")
    if ev_files:
        check("bundle evidence is byte-exact", zf.read(ev_files[0]) == photo_png)
    embedded = json.loads(zf.read("package.json"))
    check("bundle package matches API package", embedded == pkg)
    readme = zf.read("README.txt").decode()
    check("bundle README carries sha256 verification", photo_sha in readme)

    # --- 10. MVP boundary: no submission ----------------------------------------
    for method in ("POST",):
        r = client.request(method, f"{BASE}/claims/{claim['id']}/submit", headers=auth, json={})
        check("no submit endpoint (MVP stops at CLAIM_READY)", r.status_code == 404, f"status={r.status_code}")
        break
    r = client.patch(f"{BASE}/claims/{claim['id']}", headers=auth, json={"notes": "Called helpline", "status": "RESOLVED"})
    check("PATCH cannot change status", r.status_code == 200
          and r.json()["data"]["claim"]["status"] == "READY"
          and r.json()["data"]["claim"]["notes"] == "Called helpline")

    # --- 11. portable account export bundle --------------------------------------
    r = client.get(f"{BASE}/account/export/bundle", headers=auth)
    check("account export bundle (zip)", r.status_code == 200 and r.headers["content-type"].startswith("application/zip"))
    zfx = zipfile.ZipFile(io.BytesIO(r.content))
    namesx = zfx.namelist()
    check("export has manifest + document + evidence files",
          "manifest.json" in namesx
          and any(n.startswith("documents/") and "ac-receipt" in n for n in namesx)
          and any(n.startswith("claims/") and "compressor-damage" in n for n in namesx))
    manifest = json.loads(zfx.read("manifest.json"))
    check("export manifest has purchase + warranty + claim",
          manifest["purchases"][0]["id"] == purchase["id"]
          and len(manifest["claims"]) == 1)
    check("export includes stored receipt document",
          any(d["id"] == receipt_id for d in manifest["documents"]))

    failed = [x for x in RESULTS if x.startswith("[FAIL]")]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
