"""Live E2E for Alerts & Claims (SRS FR-15 / FR-16).

Against the dev API (http://127.0.0.1:8000) with the worker running:
  1. register a user (account country IN)
  2. create three QTech AC purchases with backdated purchase dates so their
     FULL_UNIT (12 mo) expiries land at ~50 days (30d+7d tiers future),
     ~5 days (7d tier immediately due), and ~14 months (expired FULL_UNIT,
     active 60-mo COMPRESSOR with three future tiers)
  3. verify reminder scheduling (best-effort, synchronous at purchase time)
  4. verify the worker sweep sends the due reminder (outbox delivery)
  5. verify idempotency across repeated sweeps
  6. create a claim, upload evidence, generate the pre-filled package,
     fetch + download it, and verify the CLAIM_READY notification
  7. exercise the notifications API (mark read / read-all)
"""

import base64
import json
import time
import uuid
from datetime import date, timedelta

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = f"e2e-alerts-{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "Passw0rd!123"

# 1x1 valid PNG (content-signature validated on upload).
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

RESULTS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    print(RESULTS[-1], flush=True)


def today() -> date:
    return date.today()


def main() -> int:
    client = httpx.Client(timeout=60)
    r = client.post(
        f"{BASE}/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "E2E Alerts", "country": "IN"},
    )
    check("register", r.status_code == 201, f"status={r.status_code}")
    token = r.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Find the catalog QTech AC (sample-fixture policy, region IN).
    r = client.get(f"{BASE}/products/search", params={"q": "QTech Inverter Split AC"}, headers=auth)
    ac = next((p for p in r.json()["data"]["items"] if p["canonical_name"] == "QTech Inverter Split AC 1.5 Ton"), None)
    check("catalog has QTech AC", ac is not None, f"product={ac and ac['id']}")
    if not ac:
        return 1

    def make_purchase(days_out: int | None = None, months_ago: int | None = None) -> dict:
        if days_out is not None:
            expiry = today() + timedelta(days=days_out)
            purchase_date = expiry.replace(year=expiry.year - 1).isoformat()
        else:
            purchase_date = (today() - timedelta(days=30 * months_ago)).isoformat()
        r = client.post(
            f"{BASE}/purchases",
            headers=auth,
            json={
                "purchase_date": purchase_date,
                "warranty_region": "IN",
                "items": [{"product_id": ac["id"], "product_name": "SPLIT AC 1.5T", "unit_price": 2899.0, "line_total": 2899.0}],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["data"]["purchase"]

    p_a = make_purchase(days_out=50)  # 30d + 7d tiers future
    p_b = make_purchase(days_out=5)  # 7d tier immediately due
    p_c = make_purchase(months_ago=14)  # FULL_UNIT expired, COMPRESSOR active

    def warranty_expiry(purchase: dict, component: str) -> str:
        r = client.get(f"{BASE}/purchases/{purchase['id']}/warranty", headers=auth)
        it = r.json()["data"]["items"][0]
        return next(t["expiry_date"] for t in it["timelines"] if t["component"] == component)

    exp_a = warranty_expiry(p_a, "FULL_UNIT")
    exp_b = warranty_expiry(p_b, "FULL_UNIT")
    exp_c_full = warranty_expiry(p_c, "FULL_UNIT")
    exp_c_comp = warranty_expiry(p_c, "COMPRESSOR")
    check(
        "timelines computed for all three purchases",
        bool(exp_a and exp_b and exp_c_full and exp_c_comp),
        f"a={exp_a} b={exp_b} c_full={exp_c_full} c_comp={exp_c_comp}",
    )
    check("C FULL_UNIT is in the past (expired)", date.fromisoformat(exp_c_full) < today(), exp_c_full)

    def user_notifications() -> list[dict]:
        r = client.get(f"{BASE}/notifications", params={"page_size": 100}, headers=auth)
        return r.json()["data"]["items"]

    def tiers_for(purchase_id: int, snapshot: str | None = None) -> dict[str, list[dict]]:
        rows = [n for n in user_notifications() if n["purchase_id"] == purchase_id]
        if snapshot is not None:
            rows = [n for n in rows if n["expiry_date_snapshot"] == snapshot]
        out: dict[str, list[dict]] = {}
        for n in rows:
            out.setdefault(n["reminder_type"] or "?", []).append(n)
        return out

    # --- FR-15: scheduling (synchronous best-effort at purchase time) ----------
    # NOTE: each purchase has TWO timelines (FULL_UNIT 12 mo + COMPRESSOR 60 mo).
    # Tier assertions are made per coverage (expiry_date_snapshot).
    ta = tiers_for(p_a["id"], exp_a)
    check(
        "A FULL_UNIT (~50d out): 30d + 7d scheduled, 60d skipped (moment passed)",
        sorted(ta.keys()) == ["30d", "7d"],
        f"tiers={sorted(ta.keys())}",
    )
    tb = tiers_for(p_b["id"], exp_b)
    check("B FULL_UNIT (~5d out): immediate 7d only", sorted(tb.keys()) == ["7d"], f"tiers={sorted(tb.keys())}")
    tc_comp = tiers_for(p_c["id"], exp_c_comp)
    tc_full = tiers_for(p_c["id"], exp_c_full)
    check(
        "C (expired FULL_UNIT): COMPRESSOR gets 60/30/7, expired window gets none",
        sorted(tc_comp.keys()) == ["30d", "60d", "7d"] and not tc_full,
        f"comp={sorted(tc_comp.keys())} full={sorted(tc_full.keys())}",
    )
    # COMPRESSOR (60 mo) windows are far out -> all three future moments scheduled.
    ta_comp = tiers_for(p_a["id"])
    ta_comp_types = {t for t, rows in ta_comp.items() for n in rows if n["expiry_date_snapshot"] != exp_a}
    check("A COMPRESSOR (60 mo): 3 future tier moments scheduled", ta_comp_types == {"30d", "60d", "7d"}, f"types={sorted(ta_comp_types)}")
    # B's 7d row is created immediately due, so the live worker may have
    # delivered it (SENT) before this check — that is correct behaviour,
    # not a regression. Nothing may be FAILED / CANCELLED / SUPPRESSED.
    statuses = {n["status"] for n in user_notifications()}
    check("all initial reminders SCHEDULED (immediately-due rows may be SENT)", statuses <= {"SCHEDULED", "SENT"}, f"statuses={sorted(statuses)}")
    total_initial = len(user_notifications())
    check(
        "twelve reminders in total (A:2+3, B:1+3, C:0+3)",
        total_initial == 12,
        f"total={total_initial}",
    )

    # --- FR-15: worker sweep sends the due reminder ---------------------------
    sent = None
    for _ in range(24):  # up to ~120s; the worker sweeps every 60s of idle
        tb = tiers_for(p_b["id"], exp_b)
        n7 = tb.get("7d", [None])[0]
        if n7 and n7["status"] == "SENT":
            sent = n7
            break
        time.sleep(5)
    check("worker sweep delivered the due 7d reminder (SENT)", sent is not None, f"sent_at={sent and sent['sent_at']}")

    # Idempotency: a later sweep must not duplicate.
    time.sleep(10)
    total_later = len(user_notifications())
    check("no duplicates after additional sweeps", total_later == total_initial, f"before={total_initial} after={total_later}")

    # --- FR-16: claim + pre-filled package ------------------------------------
    r = client.post(
        f"{BASE}/claims",
        headers=auth,
        json={
            "purchase_id": p_a["id"],
            "issue_description": "Compressor noise; unit not cooling under load",
            "issue_category": "DEFECT",
            "contact_name": "E2E Claimant",
            "contact_email": EMAIL,
        },
    )
    check("create claim", r.status_code == 201, f"status={r.status_code}")
    claim = r.json()["data"]["claim"]
    check("claim starts DRAFT", claim["status"] == "DRAFT")

    r = client.post(
        f"{BASE}/claims/{claim['id']}/evidence",
        headers=auth,
        files={"file": ("compressor-noise.png", PNG, "image/png")},
    )
    check("upload evidence", r.status_code == 201, f"status={r.status_code}")
    ev = r.json()["data"]["evidence"]

    r = client.get(f"{BASE}/claims/{claim['id']}/package", headers=auth)
    check("package before generation -> 409", r.status_code == 409, f"status={r.status_code}")

    r = client.post(f"{BASE}/claims/{claim['id']}/package", headers=auth)
    check("generate package", r.status_code == 200, f"status={r.status_code}")
    c2 = r.json()["data"]["claim"]
    check("claim -> READY with package_generated_at", c2["status"] == "READY" and c2["package_generated_at"] is not None)

    r = client.get(f"{BASE}/claims/{claim['id']}/package", headers=auth)
    pkg = r.json()["data"]["package"]
    check("package: purchase pre-filled", pkg["purchase"]["purchase_date"] == p_a["purchase_date"] and pkg["purchase"]["warranty_region"] == "IN")
    check("package: item rows", pkg["items"][0]["product_name"] == "SPLIT AC 1.5T")
    w = pkg["warranty"][0]
    check(
        "package: warranty policy + source provenance",
        w["policy"]["policy_name"].startswith("QTech Sample AC Warranty")
        and w["policy"]["source"] is not None
        and w["policy"]["source"]["trust_level"] == 2,
        f"policy={w['policy']['policy_name']}",
    )
    tl = {t["component"]: t for t in w["timelines"]}
    check("package: timelines match computed windows", tl["FULL_UNIT"]["expiry_date"] == exp_a and tl["COMPRESSOR"]["expiry_date"] is not None)
    check("package: evidence pre-filled with sha256", pkg["evidence"][0]["original_filename"] == ev["original_filename"] and pkg["evidence"][0]["sha256"] == ev["sha256"])
    check("package: disclaimer present", "legal rights" in pkg.get("disclaimer", ""))

    r = client.get(f"{BASE}/claims/{claim['id']}/package/download", headers=auth)
    body = json.loads(r.content)
    check("package download (JSON)", r.status_code == 200 and body["claim_uuid"] == claim["uuid"], f"content-type={r.headers.get('content-type')}")

    # CLAIM_READY notification (idempotent key claim-READY-{id}).
    claim_notes = [n for n in user_notifications() if n.get("claim_id") == claim["id"]]
    check("CLAIM_READY notification created", len(claim_notes) == 1 and claim_notes[0]["category"] == "CLAIM_READY")

    # Worker sweep delivers CLAIM_READY too; then mark read / read-all.
    claim_sent = None
    for _ in range(24):
        claim_notes = [n for n in user_notifications() if n.get("claim_id") == claim["id"]]
        if claim_notes and claim_notes[0]["status"] == "SENT":
            claim_sent = claim_notes[0]
            break
        time.sleep(5)
    check("worker delivered CLAIM_READY", claim_sent is not None)
    if claim_sent:
        r = client.patch(f"{BASE}/notifications/{claim_sent['id']}/read", headers=auth)
        check("mark read", r.status_code == 200 and r.json()["data"]["status"] == "READ")
        before = client.get(f"{BASE}/notifications", headers=auth).json()["data"]["unread_count"]
        r = client.post(f"{BASE}/notifications/read-all", headers=auth)
        after = client.get(f"{BASE}/notifications", headers=auth).json()["data"]["unread_count"]
        check("read-all clears unread", r.status_code == 200 and after == 0 and before >= 1, f"before={before} after={after}")

    # Claim list + status filter.
    r = client.get(f"{BASE}/claims", headers=auth)
    check("claim list", r.status_code == 200 and r.json()["data"]["total"] == 1)
    r = client.get(f"{BASE}/claims", params={"status": "READY"}, headers=auth)
    check("claim status filter READY", r.json()["data"]["total"] == 1)

    failed = [x for x in RESULTS if x.startswith("[FAIL]")]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
