"""Warranty mapping evaluation (TSD §41/§59, PRD §6): policy correctness,
duration correctness, source validity — measured on the REAL rules engine.

Seeds a deterministic policy catalog into a dedicated eval database, then
runs the unmodified `find_warranty_policy` + `compute_timeline` over a case
set whose expected outcomes are derived by hand (construction, not the
engine). Metrics are computed from actual engine output; honesty cases
(unknown anchors, out-of-window purchases) verify the engine reports
UNKNOWN / no-match instead of fabricating.

Output: WARRANTY_MAPPING_RESULTS.json / .md next to this file.
"""

import json
from datetime import date
from pathlib import Path

import _evaldb

TODAY = date(2026, 9, 4)  # fixed "today" so results are reproducible
PURCHASE = date(2026, 8, 15)


def build_seed(db):
    from app.models.product import Manufacturer, Product
    from app.models.warranty import (
        DurationUnit,
        PolicyStatus,
        SourceType,
        StartCondition,
        WarrantyCoverage,
        WarrantyPolicy,
        WarrantySource,
    )

    def mfr(name):
        from app.ai.product.normalization import make_name_key

        m = Manufacturer(name=name, name_key=make_name_key(name))
        db.add(m)
        db.flush()
        return m

    acme, wash, nimbus = mfr("AcmeCool Appliances"), mfr("WashPro"), mfr("NimbusTech")

    def src(manufacturer, stype, trust, title):
        s = WarrantySource(
            manufacturer_id=manufacturer.id if manufacturer else None,
            source_type=stype,
            source_title=title,
            trust_level=trust,
            retrieval_status="SUCCESS",
        )
        db.add(s)
        db.flush()
        return s

    src_a = src(acme, SourceType.OFFICIAL_WEB, 5, "AcmeCool India warranty terms")
    src_b = src(wash, SourceType.OFFICIAL_WEB, 5, "WashPro warranty terms")
    src_c = src(nimbus, SourceType.CURATED_DATASET, 3, "NimbusTech curated terms")
    src_d = src(None, SourceType.OTHER, 2, "Generic appliance fallback")

    def product(manufacturer, name, cat, model):
        p = Product(manufacturer_id=manufacturer.id, brand=name.split()[0], canonical_name=name, category=cat, model_number=model)
        db.add(p)
        db.flush()
        return p

    ac = product(acme, "AcmeCool Split AC 1.5T Inverter", "Air Conditioning", "AR15FSSY")
    wm = product(wash, "WashPro Front Load Washer 8kg", "Washing Machine", "WM80-FL")
    nb = product(nimbus, "NimbusTech ProBook 14 Laptop", "Laptop", "NP14-2026")

    def policy(manufacturer, name, version, category, region, pattern, status, eff_from, src_, to=None):
        p = WarrantyPolicy(
            manufacturer_id=manufacturer.id if manufacturer else None,
            policy_name=name,
            policy_version=version,
            product_category=category,
            product_model_pattern=pattern,
            region=region,
            status=status,
            effective_from=eff_from,
            effective_to=to,
            source_id=src_.id,
        )
        db.add(p)
        db.flush()
        return p

    def cov(p, component, months, start=StartCondition.PURCHASE_DATE, reg=False):
        db.add(
            WarrantyCoverage(
                policy_id=p.id,
                component=component,
                duration_value=months,
                duration_unit=DurationUnit.MONTH,
                start_condition=start,
                registration_required=reg,
            )
        )
        db.flush()

    # P1 v1.0 (ARCHIVED — history preserved, must never be returned)
    p1v1 = policy(acme, "AcmeCool AC India", "1.0", "Air Conditioning", "IN", "AR*", PolicyStatus.ARCHIVED, date(2020, 1, 1), src_a)
    cov(p1v1, "FULL_UNIT", 12)
    cov(p1v1, "COMPRESSOR", 60)
    # P1 v2.0 (ACTIVE, from 2024-01-01 — the current version)
    p1v2 = policy(acme, "AcmeCool AC India", "2.0", "Air Conditioning", "IN", "AR*", PolicyStatus.ACTIVE, date(2024, 1, 1), src_a)
    cov(p1v2, "FULL_UNIT", 12)
    cov(p1v2, "COMPRESSOR", 120)
    # P2: same product, US region
    p2 = policy(acme, "AcmeCool AC US", "1.0", "Air Conditioning", "US", "AR*", PolicyStatus.ACTIVE, date(2020, 1, 1), src_a)
    cov(p2, "FULL_UNIT", 12)
    # P3: registration-required
    p3 = policy(wash, "WashPro Washer", "1.0", "Washing Machine", None, "WM*", PolicyStatus.ACTIVE, date(2020, 1, 1), src_b)
    cov(p3, "FULL_UNIT", 24, reg=True)
    # P4: laptop with battery coverage
    p4 = policy(nimbus, "NimbusTech Laptop", "1.0", "Laptop", None, "NP*", PolicyStatus.ACTIVE, date(2020, 1, 1), src_c)
    cov(p4, "FULL_UNIT", 12)
    cov(p4, "BATTERY", 12)
    # P5: generic worldwide fallback, only from 2025-01-01
    p5 = policy(None, "Generic Appliance", "1.0", None, None, "GEN*", PolicyStatus.ACTIVE, date(2025, 1, 1), src_d)
    cov(p5, "FULL_UNIT", 12)
    db.commit()
    return {"ac": ac, "wm": wm, "nb": nb, "src_a": src_a, "src_b": src_b, "src_c": src_c, "src_d": src_d}


def main() -> None:
    _evaldb.ensure_database()
    SessionLocal = _evaldb.session_factory()
    db = SessionLocal()
    seed = build_seed(db)

    from app.models.product import Manufacturer
    from app.models.warranty import StartCondition, WarrantySource
    from app.warranty.rules.matching import find_warranty_policy
    from app.warranty.rules.timeline import compute_timeline

    # (label, product, region, purchase_date, registration, expected_policy_name,
    #  expected_version, expected_source_title, expected_expiry by component or "UNKNOWN")
    cases = [
        ("IN purchase → current v2.0, 120-month compressor",
         "ac", "IN", PURCHASE, None, "AcmeCool AC India", "2.0", "AcmeCool India warranty terms",
         {"FULL_UNIT": date(2027, 8, 15), "COMPRESSOR": date(2036, 8, 15)}),
        ("US purchase → region-specific policy",
         "ac", "US", PURCHASE, None, "AcmeCool AC US", "1.0", "AcmeCool India warranty terms",
         {"FULL_UNIT": date(2027, 8, 15)}),
        # Unknown region: region-specific policies (IN/US) are excluded and
        # the unlinked generic policy must NOT auto-apply (no fabrication) →
        # honest NO mapping. (Worldwide-policy-for-unknown-region is covered
        # by the washer/laptop cases below.)
        ("Unknown region + region-specific policies only → NO mapping (honest)",
         "ac", None, PURCHASE, None, None, None, None, {}),
        ("Registration required, not recorded → honest UNKNOWN (never guessed)",
         "wm", None, PURCHASE, None, "WashPro Washer", "1.0", "WashPro warranty terms",
         {"FULL_UNIT": "UNKNOWN"}),
        ("Registration recorded → 24 months from registration date",
         "wm", None, PURCHASE, date(2026, 8, 20), "WashPro Washer", "1.0", "WashPro warranty terms",
         {"FULL_UNIT": date(2028, 8, 20)}),
        ("Laptop → battery + unit coverage",
         "nb", None, PURCHASE, None, "NimbusTech Laptop", "1.0", "NimbusTech curated terms",
         {"FULL_UNIT": date(2027, 8, 15), "BATTERY": date(2027, 8, 15)}),
        ("Purchase before effective window → NO policy (no fabrication)",
         "ac", "IN", date(2023, 6, 1), None, None, None, None, {}),
    ]

    rows = []
    for label, pkey, region, pdate, reg, exp_name, exp_ver, exp_src, exp_cov in cases:
        product = seed[pkey]
        manufacturer = db.get(Manufacturer, product.manufacturer_id)
        match, notes = find_warranty_policy(
            db,
            product=product,
            manufacturer=manufacturer,
            region=region,
            purchase_date=pdate,
            item_model=product.model_number,
            item_sku=product.sku,
        )
        got_name = match.policy.policy_name if match else None
        got_ver = match.policy.policy_version if match else None
        src = db.get(WarrantySource, match.policy.source_id) if match and match.policy.source_id else None
        got_src = src.source_title if src else None

        cov_out: dict[str, dict] = {}
        if match:
            for c in match.policy.coverages:
                tl = compute_timeline(c, pdate, TODAY, anchors={StartCondition.REGISTRATION_DATE: reg, StartCondition.PURCHASE_DATE: pdate})
                cov_out[c.component] = {
                    "start": tl["start_date"].isoformat() if tl["start_date"] else None,
                    "expiry": tl["expiry_date"].isoformat() if tl["expiry_date"] else None,
                    "status": str(tl["status"]),
                }

        policy_ok = got_name == exp_name and got_ver == exp_ver
        source_ok = (got_src == exp_src) if exp_name else (got_src is None)
        duration_ok = True
        detail = {}
        for comp, expected in exp_cov.items():
            got = cov_out.get(comp)
            if expected == "UNKNOWN":
                ok = got is not None and got["expiry"] is None and got["status"] == "UNKNOWN"
            else:
                ok = got is not None and got["expiry"] == expected.isoformat()
            duration_ok = duration_ok and ok
            expected_str = "UNKNOWN" if expected == "UNKNOWN" else expected.isoformat()
            detail[comp] = {"expected": expected_str, "got": (got["expiry"] if got else None), "ok": ok}

        rows.append(
            {
                "case": label,
                "expected_policy": f"{exp_name} {exp_ver}" if exp_name else None,
                "got_policy": f"{got_name} {got_ver}" if got_name else None,
                "policy_ok": policy_ok,
                "source_ok": source_ok,
                "duration_ok": duration_ok,
                "duration_detail": detail,
                "notes": notes,
            }
        )

    n = len(rows)
    correct_policy = sum(1 for r in rows if r["policy_ok"])
    correct_duration = sum(1 for r in rows if r["duration_ok"])
    valid_source = sum(1 for r in rows if r["source_ok"])

    out = {
        "metadata": {
            "engine": "rule-based warranty matching (find_warranty_policy) + timeline rules (compute_timeline)",
            "engine_source": "app.warranty.rules (unmodified)",
            "dataset": "deterministic synthetic policy catalog (7 cases: region priority, versioning, generic fallback, registration UNKNOWN, registration anchored, battery coverage, out-of-window no-match)",
            "today": TODAY.isoformat(),
            "date": date.today().isoformat(),
        },
        "overall": {
            "cases": n,
            "policy_correctness": round(correct_policy / n, 4),
            "duration_correctness": round(correct_duration / n, 4),
            "source_validity": round(valid_source / n, 4),
        },
        "per_case": rows,
    }

    here = Path(__file__).parent
    (here / "WARRANTY_MAPPING_RESULTS.json").write_text(json.dumps(out, indent=2))
    md = [
        "# Warranty Mapping Evaluation (rules engine)",
        "",
        f"- Engine: **{out['metadata']['engine']}** (live code)",
        f"- Dataset: {out['metadata']['dataset']}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| policy correctness | {out['overall']['policy_correctness']} |",
        f"| duration correctness (expiry date or honest UNKNOWN) | {out['overall']['duration_correctness']} |",
        f"| source validity (mapped source = expected valid source) | {out['overall']['source_validity']} |",
        "",
        "Per case:",
        "",
        "| case | policy ok | source ok | duration ok |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        md.append(f"| {r['case']} | {'PASS' if r['policy_ok'] else 'FAIL'} | {'PASS' if r['source_ok'] else 'FAIL'} | {'PASS' if r['duration_ok'] else 'FAIL'} |")
    md.append("")
    md.append("Honest caveats: synthetic policy catalog with hand-derived expectations; real manufacturer")
    md.append("term tables (effective windows, regional overrides, component exceptions) will shift these")
    md.append("numbers. The honesty cases (UNKNOWN / no-match) are the strongest signal for the")
    md.append("'never guess a warranty duration' constraint.")
    (here / "WARRANTY_MAPPING_RESULTS.md").write_text("\n".join(md))

    print(json.dumps(out["overall"], indent=2))
    for r in rows:
        flag = "PASS" if (r["policy_ok"] and r["source_ok"] and r["duration_ok"]) else "FAIL"
        print(f"  [{flag}] {r['case']}: got {r['got_policy']}")
    db.close()
    print("wrote WARRANTY_MAPPING_RESULTS.json / .md")


if __name__ == "__main__":
    main()
