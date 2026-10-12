"""Live E2E for Warranty Intelligence (SRS FR-10/FR-11a/FR-12/FR-13/FR-14).

Against the dev API (http://127.0.0.1:8000):
  1. register a user (account country IN)
  2. create purchases for catalog products with warranty_region IN
     - a QTech AC (sample-fixture policy)
     - a Samsung TV (real official-source policy)
  3. verify auto-mapping, timelines, FR-12 source provenance
  4. verify FR-11a: a purchase with no resolvable region is unmapped
  5. drive the review actions (confirm / reject)
  6. verify dashboard buckets + warranty_status search filter
"""

import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = f"e2e-wi-{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "Passw0rd!123"

RESULTS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    print(RESULTS[-1], flush=True)


def main() -> int:
    client = httpx.Client(timeout=60)
    r = client.post(f"{BASE}/auth/register", json={"email": EMAIL, "password": PASSWORD, "display_name": "E2E WI", "country": "IN"})
    check("register", r.status_code == 201, f"status={r.status_code}")
    token = r.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Find catalog product ids by name.
    def find_product(name: str) -> int | None:
        r = client.get(f"{BASE}/products/search", params={"q": name}, headers=auth)
        for p in r.json().get("data", {}).get("items", []):
            if p["canonical_name"] == name:
                return p["id"]
        return None

    qtech_ac = find_product("QTech Inverter Split AC 1.5 Ton")
    samsung_tv = find_product("Samsung 55-inch QLED 4K Smart TV")
    check("catalog has QTech AC + Samsung TV", bool(qtech_ac and samsung_tv), f"ac={qtech_ac} tv={samsung_tv}")
    if not qtech_ac or not samsung_tv:
        raise SystemExit(1)

    # --- Purchase 1: QTech AC, region IN -> sample-fixture policy ----------------
    r = client.post(
        f"{BASE}/purchases",
        headers=auth,
        json={
            "purchase_date": "2026-08-30",
            "warranty_region": "IN",
            "items": [{"product_id": qtech_ac, "product_name": "SPLIT AC 1.5T", "unit_price": 2899.0, "line_total": 2899.0}],
        },
    )
    check("create QTech AC purchase", r.status_code == 201, f"status={r.status_code}")
    p1 = r.json()["data"]["purchase"]
    item1 = p1["items"][0]["id"]

    r = client.get(f"{BASE}/purchases/{p1['id']}/warranty", headers=auth)
    w1 = r.json()["data"]
    it1 = w1["items"][0]
    check("AC auto-mapped (catalog)", it1["mapped"] and it1["match_method"] == "CATALOG_MATCH", f"method={it1['match_method']} conf={it1['match_confidence']}")
    check("AC timelines computed", {t["component"] for t in it1["timelines"]} == {"FULL_UNIT", "COMPRESSOR"}, f"tl={[(t['component'], t['expiry_date'], t['status']) for t in it1['timelines']]}")
    fu = next(t for t in it1["timelines"] if t["component"] == "FULL_UNIT")
    check("AC FULL_UNIT window", fu["start_date"] == "2026-08-30" and fu["expiry_date"] == "2027-08-30" and fu["status"] == "ACTIVE", f"{fu['start_date']}->{fu['expiry_date']} {fu['status']}")
    check("AC FR-12 source shown", bool(it1["policy"] and it1["policy"]["source"] and it1["policy"]["source"]["source_title"]), f"src={it1['policy'] and it1['policy']['source'] and it1['policy']['source']['source_title'][:60]}")

    # --- Purchase 2: Samsung TV -> real official-source policy ------------------
    r = client.post(
        f"{BASE}/purchases",
        headers=auth,
        json={
            "purchase_date": "2026-08-30",
            "warranty_region": "IN",
            "items": [{"product_id": samsung_tv, "product_name": "Samsung QLED TV 55in", "unit_price": 84999.0, "line_total": 84999.0}],
        },
    )
    check("create Samsung TV purchase", r.status_code == 201, f"status={r.status_code}")
    p2 = r.json()["data"]["purchase"]
    item2 = p2["items"][0]["id"]
    r = client.get(f"{BASE}/purchases/{p2['id']}/warranty", headers=auth)
    it2 = r.json()["data"]["items"][0]
    check("Samsung TV mapped to real policy", it2["mapped"] and "Samsung India" in (it2["policy"]["policy_name"] if it2["policy"] else ""), f"policy={it2['policy'] and it2['policy']['policy_name']}")
    src2 = it2["policy"]["source"] if it2["policy"] else None
    check("Samsung FR-12 official source", bool(src2 and src2["source_url"] and "samsung.com" in src2["source_url"] and src2["trust_level"] == 5), f"url={src2 and src2['source_url']} trust={src2 and src2['trust_level']}")

    # --- Review actions -----------------------------------------------------------
    r = client.post(f"{BASE}/purchases/{p1['id']}/warranty/review", headers=auth, json={"item_id": item1, "action": "confirm"})
    check("confirm AC mapping", r.status_code == 200 and r.json()["data"]["mapped"], f"status={r.status_code}")
    r = client.get(f"{BASE}/purchases/{p1['id']}/warranty", headers=auth)
    check("confirmed status persisted", r.json()["data"]["items"][0]["verification_status"] == "USER_CONFIRMED")
    r = client.post(f"{BASE}/purchases/{p2['id']}/warranty/review", headers=auth, json={"item_id": item2, "action": "reject"})
    check("reject Samsung mapping", r.status_code == 200 and not r.json()["data"]["mapped"])

    # --- FR-11a: unresolved region -> unknown (separate user, no country) --------
    email3 = f"e2e-wi-noreg-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(f"{BASE}/auth/register", json={"email": email3, "password": PASSWORD, "display_name": "E2E WI N", "country": None})
    t3 = r.json()["data"]["access_token"]
    a3 = {"Authorization": f"Bearer {t3}"}
    r = client.post(
        f"{BASE}/purchases",
        headers=a3,
        json={"purchase_date": "2026-08-30", "items": [{"product_id": qtech_ac, "product_name": "SPLIT AC 1.5T"}]},
    )
    p3 = r.json()["data"]["purchase"]
    r = client.get(f"{BASE}/purchases/{p3['id']}/warranty", headers=a3)
    check("unresolved region -> not mapped (FR-11a)", r.json()["data"]["items"][0]["mapped"] is False)
    d3 = client.get(f"{BASE}/dashboard/summary", headers=a3).json()["data"]
    check("dashboard bucket unknown (FR-11a)", d3["warranties"]["unknown"] == 1, f"buckets={d3['warranties']}")

    # --- Dashboard + search for main user ----------------------------------------
    d = client.get(f"{BASE}/dashboard/summary", headers=auth).json()["data"]
    check("dashboard buckets active", d["warranties"]["active"] == 1, f"buckets={d['warranties']}")
    lst = client.get(f"{BASE}/purchases", headers=auth, params={"warranty_status": "ACTIVE"}).json()["data"]
    check("search by warranty_status=ACTIVE", lst["total"] == 1, f"total={lst['total']}")
    lst2 = client.get(f"{BASE}/purchases", headers=auth, params={"warranty_status": "EXPIRED"}).json()["data"]
    check("search by warranty_status=EXPIRED (none)", lst2["total"] == 0, f"total={lst2['total']}")

    # --- Warranty policy browse (FR-09) ------------------------------------------
    r = client.get(f"{BASE}/warranty/policies", headers=auth, params={"manufacturer": "Samsung"})
    items_b = r.json()["data"]["items"]
    check("policy browse (FR-09)", r.status_code == 200 and len(items_b) >= 4, f"n={len(items_b)}")

    # --- Phase 6: source registry + failure-safe refresh (FR-12) ------------------
    import os
    import subprocess

    def psql(sql: str) -> str:
        env = {**os.environ, "PGPASSWORD": "warranty_dev_pw"}
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-U", "warranty", "-d", "warranty_vault", "-tAc", sql],
            env=env, capture_output=True, text=True,
        )
        return out.stdout.strip()

    r = client.get(f"{BASE}/warranty/sources", headers=auth)
    srcs = r.json()["data"]["items"]
    check(
        "source registry lists sources with retrieval status",
        r.status_code == 200 and len(srcs) >= 4 and all("retrieval_status" in s for s in srcs),
        f"n={len(srcs)}",
    )
    qtech_src = next(
        (s for s in srcs if s.get("source_type") == "CURATED_DATASET" and (s.get("manufacturer") or "").lower().startswith("qtech")),
        None,
    )
    check("registry shows QTech curated source", qtech_src is not None, f"src={qtech_src and qtech_src['id']}")

    # Admin-only refresh endpoint: a consumer gets 403.
    r = client.post(f"{BASE}/warranty/sources/{qtech_src['id']}/refresh", headers=a3, json={})
    check("refresh endpoint rejects non-admin", r.status_code == 403, f"status={r.status_code}")

    # Promote the E2E user to ADMIN (dev DB only) and log in for a fresh token.
    psql(f"UPDATE users SET role = 'ADMIN' WHERE email = '{EMAIL}'")
    r = client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    admin_token = r.json()["data"]["access_token"]
    aadmin = {"Authorization": f"Bearer {admin_token}"}

    # Point the QTech source at a dead URL -> the refresh must FAIL and the
    # previous valid policy must survive untouched.
    psql(f"UPDATE warranty_sources SET source_url = 'http://127.0.0.1:9/warranty', retrieval_status = 'SUCCESS' WHERE id = {qtech_src['id']}")
    r = client.post(f"{BASE}/warranty/sources/{qtech_src['id']}/refresh", headers=aadmin, json={})
    d = r.json()["data"]
    check("failed refresh recorded (FAILED)", r.status_code == 200 and d["status"] == "FAILED", f"detail={d.get('error', '')[:60]}")
    check("failed refresh leaves policies unchanged", d.get("policies_unchanged") is True)

    r = client.get(f"{BASE}/warranty/sources", headers=auth)
    src_now = next(s for s in r.json()["data"]["items"] if s["id"] == qtech_src["id"])
    check("registry shows FAILED + last_error", src_now["retrieval_status"] == "FAILED" and bool(src_now.get("last_error")))

    # A fresh purchase still maps to the QTech policy — nothing was deleted.
    r = client.post(
        f"{BASE}/purchases",
        headers=auth,
        json={
            "purchase_date": "2026-08-31",
            "warranty_region": "IN",
            "items": [{"product_id": qtech_ac, "product_name": "SPLIT AC 1.5T"}],
        },
    )
    p_fail = r.json()["data"]["purchase"]
    r = client.get(f"{BASE}/purchases/{p_fail['id']}/warranty", headers=auth)
    check("policy intact after failed refresh (still maps)", r.json()["data"]["items"][0]["mapped"] is True)

    # Restore the source (dev DB hygiene).
    psql(f"UPDATE warranty_sources SET source_url = NULL, retrieval_status = 'SUCCESS', last_error = NULL WHERE id = {qtech_src['id']}")

    # --- Region resolution: retailer country fallback (FR-11a) --------------------
    r = client.post(
        f"{BASE}/purchases",
        headers=auth,
        json={"purchase_date": "2026-08-31", "retailer_id": 1, "items": [{"product_name": "Widget (no product)"}]},
    )
    p_ret = r.json()["data"]["purchase"]
    check(
        "retailer country fallback (FR-11a)",
        p_ret["warranty_region"] == "US" and p_ret["warranty_region_resolution_method"] == "RETAILER_COUNTRY",
        f"region={p_ret['warranty_region']} method={p_ret['warranty_region_resolution_method']}",
    )

    # --- TSD §42 traceability chain ----------------------------------------------
    r = client.get(f"{BASE}/purchases/{p1['id']}/trace", headers=auth)
    tr = r.json().get("data", {}) if r.status_code == 200 else {}
    check("trace endpoint 200", r.status_code == 200, f"status={r.status_code}")
    legend = tr.get("provenance_legend", {})
    check(
        "trace legend covers all 5 required provenances + computed",
        all(k in legend for k in ("AI_EXTRACTED", "USER_CONFIRMED", "VERIFIED", "EXTERNAL_SOURCE", "INFERRED", "COMPUTED")),
        f"legend={sorted(legend)}",
    )
    check("trace purchase node has date+region", tr.get("purchase", {}).get("purchase_date") == "2026-08-30")
    check("trace products node mapped", any(w.get("mapped") for w in tr.get("warranty", [])))
    expiry_nodes = tr.get("expiry", [])
    check(
        "trace expiry node is COMPUTED provenance",
        any(e.get("provenance") == "COMPUTED" and e.get("expiry_date") for e in expiry_nodes),
        f"expiry={[ (e.get('component'), e.get('expiry_date')) for e in expiry_nodes]}",
    )
    check("trace receipt is null for manual purchase", tr.get("receipt") is None)

    # --- Extended warranties (where the data supports them) -----------------------
    r = client.post(
        f"{BASE}/purchases/{p1['id']}/extended-warranties",
        headers=auth,
        json={"component": "COMPRESSOR", "duration_value": 24, "duration_unit": "MONTH", "source_type": "EXTERNAL_OFFER", "source_detail": "e2e plan"},
    )
    check("add extended warranty 24mo COMPRESSOR", r.status_code == 201, f"status={r.status_code} {r.text[:120]}")
    ew = r.json().get("data", {}).get("extended_warranty", {})
    tl = (ew.get("timelines") or [{}])[0]
    check(
        "extended timeline from purchase date",
        tl.get("start_date") == "2026-08-30" and tl.get("expiry_date") == "2028-08-30" and tl.get("status") == "ACTIVE",
        f"tl={tl}",
    )

    # Multiple simultaneous coverage periods: base 60mo + extension 24mo on COMPRESSOR.
    r = client.get(f"{BASE}/purchases/{p1['id']}/warranty", headers=auth)
    items = r.json()["data"]["items"]
    item1_now = next((i for i in items if i["item_id"] == item1), None) or (items[0] if items else None)
    comp_tl = [t for t in (item1_now or {}).get("timelines", []) if t["component"] == "COMPRESSOR"]
    check(
        "two simultaneous COMPRESSOR timelines (base + extended)",
        len(comp_tl) == 2 and any(t.get("is_extended") for t in comp_tl),
        f"tl={[(t.get('expiry_date'), t.get('is_extended')) for t in comp_tl]}",
    )

    # Finite unit with no recorded duration -> UNKNOWN (never guessed).
    r = client.post(
        f"{BASE}/purchases/{p1['id']}/extended-warranties",
        headers=auth,
        json={"component": "DISPLAY", "duration_unit": "MONTH", "source_type": "USER_DECLARED"},
    )
    check("extended without duration accepted", r.status_code == 201)
    ew_unknown = r.json().get("data", {}).get("extended_warranty", {})
    tl_u = (ew_unknown.get("timelines") or [{}])[0]
    check("extended without duration is UNKNOWN (not guessed)", tl_u.get("status") == "UNKNOWN" and tl_u.get("expiry_date") is None, f"tl={tl_u}")

    # Installation-anchored extension: UNKNOWN until the date is recorded.
    r = client.post(
        f"{BASE}/purchases/{p1['id']}/extended-warranties",
        headers=auth,
        json={"component": "FULL_UNIT", "duration_value": 12, "duration_unit": "MONTH", "start_condition": "INSTALLATION_DATE", "source_type": "USER_DECLARED"},
    )
    ew_inst = r.json().get("data", {}).get("extended_warranty", {})
    tl_i = (ew_inst.get("timelines") or [{}])[0]
    check("installation-anchored extension UNKNOWN before date recorded", tl_i.get("status") == "UNKNOWN", f"tl={tl_i}")

    # Record the installation date -> recomputed from it.
    r = client.patch(
        f"{BASE}/purchases/{p1['id']}",
        headers=auth,
        json={"items": [{"product_id": qtech_ac, "product_name": "SPLIT AC 1.5T", "unit_price": 2899.0, "line_total": 2899.0, "installation_date": "2026-09-01"}]},
    )
    check("record installation date", r.status_code == 200, f"status={r.status_code}")
    item1 = r.json()["data"]["purchase"]["items"][0]["id"]  # re-applied items -> new row id
    r = client.get(f"{BASE}/purchases/{p1['id']}/extended-warranties", headers=auth)
    exts = r.json()["data"]["items"]
    inst_ext = next((e for e in exts if e["component"] == "FULL_UNIT" and e["start_condition"] == "INSTALLATION_DATE"), None)
    tl_inst = (inst_ext or {}).get("timelines") or [{}]
    check(
        "installation-anchored extension computed after date recorded",
        bool(inst_ext) and tl_inst[0].get("start_date") == "2026-09-01" and tl_inst[0].get("expiry_date") == "2027-09-01",
        f"ext={inst_ext and inst_ext.get('timelines')}",
    )

    # Deleting an extension removes its timeline but keeps the base coverage.
    for e in exts:
        if e["component"] == "DISPLAY":
            r = client.delete(f"{BASE}/purchases/{p1['id']}/extended-warranties/{e['id']}", headers=auth)
            check("delete extended warranty", r.status_code == 200)
    r = client.get(f"{BASE}/purchases/{p1['id']}/warranty", headers=auth)
    item1_now = next((i for i in r.json()["data"]["items"] if i["item_id"] == item1), None) or {}
    base_tl = [t for t in item1_now.get("timelines", []) if not t.get("is_extended")]
    check("base timelines intact after extension delete", len(base_tl) >= 2, f"n={len(base_tl)}")

    # --- NEEDS_REVIEW filter + dashboard incomplete bucket ------------------------
    # p3 (unresolved region) belongs to user3; the main user's NEEDS_REVIEW set
    # holds the rejected Samsung purchase (p2) and the unmapped retailer fallback (p_ret).
    r = client.get(f"{BASE}/purchases", headers=auth, params={"warranty_status": "NEEDS_REVIEW"})
    ids = [p["id"] for p in r.json()["data"]["items"]]
    check("NEEDS_REVIEW filter finds rejected + unmapped purchases", p2["id"] in ids and p_ret["id"] in ids, f"ids={ids}")
    r = client.get(f"{BASE}/purchases", headers=a3, params={"warranty_status": "NEEDS_REVIEW"})
    ids3 = [p["id"] for p in r.json()["data"]["items"]]
    check("NEEDS_REVIEW filter finds the unresolved-region purchase (user3)", p3["id"] in ids3, f"ids={ids3}")
    d = client.get(f"{BASE}/dashboard/summary", headers=auth).json()["data"]
    check("dashboard incomplete bucket counts the unresolved purchases", d["warranties"]["incomplete"] >= 2, f"w={d['warranties']}")

    failed = [x for x in RESULTS if x.startswith("[FAIL]")]
    print(f"\nE2E WARRANTY INTEL: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
