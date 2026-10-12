"""Simple concurrent load test for the dev API (PRD Phase 6: load testing).

Creates a few seed users, then drives a weighted mix of read-heavy
endpoints with N concurrent asyncio workers for a fixed duration.
Reports overall + per-endpoint latency percentiles and error rates, and
persists a JSON result next to the repo data dir.

Usage:
    python scripts/load_test.py [--base-url http://127.0.0.1:8000/api/v1]
                                [--users 8] [--duration 20] [--seed 4]

The workload is deliberately read-centric (list/browse/search) — the
dominant real-world traffic pattern. One login per worker validates the
auth path under load.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

# endpoint -> (weight, factory) — factory(client, ctx) -> (method, path)
ENDPOINTS: list[tuple[str, float]] = [
    ("GET /purchases", 0.28),
    ("GET /dashboard/summary", 0.18),
    ("GET /warranty/policies", 0.18),
    ("GET /products/search", 0.14),
    ("GET /purchases/{id}/warranty", 0.12),
    ("POST /auth/login", 0.10),
]
WEIGHTS = [w for _, w in ENDPOINTS]
NAMES = [n for n, _ in ENDPOINTS]


def _pick() -> str:
    return random.choices(NAMES, weights=WEIGHTS, k=1)[0]


async def _seed_user(client: httpx.AsyncClient, base: str, i: int) -> tuple[str, dict]:
    email = f"loadtest-{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post(
        f"{base}/auth/register",
        json={"email": email, "password": "Passw0rd!123", "display_name": f"Load {i}", "country": "IN"},
    )
    r.raise_for_status()
    data = r.json()["data"]
    return email, data


async def _worker(client: httpx.AsyncClient, base: str, email: str, creds: dict, deadline: float, results: list, errors: list) -> None:
    token = creds["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    purchase_ids: list[int] = []

    # Warm-up: create two purchases so read endpoints have data.
    for name in ("SPLIT AC 1.5T", "QLED TV 55"):
        r = await client.post(
            f"{base}/purchases",
            headers=auth,
            json={"purchase_date": "2026-08-15", "warranty_region": "IN", "items": [{"product_name": name}]},
        )
        if r.status_code == 201:
            purchase_ids.append(r.json()["data"]["purchase"]["id"])

    while time.monotonic() < deadline:
        ep = _pick()
        t0 = time.perf_counter()
        status = 0
        try:
            if ep == "POST /auth/login":
                r = await client.post(f"{base}/auth/login", json={"email": email, "password": "Passw0rd!123"})
            elif ep == "GET /purchases/{id}/warranty":
                pid = random.choice(purchase_ids) if purchase_ids else 1
                r = await client.get(f"{base}/purchases/{pid}/warranty", headers=auth)
            elif ep == "GET /products/search":
                r = await client.get(f"{base}/products/search", params={"q": "ac"}, headers=auth)
            else:
                path = ep.split(" ", 1)[1]
                r = await client.get(f"{base}{path}", headers=auth)
            status = r.status_code
        except Exception as exc:  # noqa: BLE001 - network errors count as errors
            errors.append({"endpoint": ep, "error": f"{exc.__class__.__name__}: {str(exc)[:120]}"})
            continue
        dt_ms = (time.perf_counter() - t0) * 1000
        results.append({"endpoint": ep, "status": status, "ms": dt_ms})
        if status >= 500:
            errors.append({"endpoint": ep, "error": f"HTTP {status}"})


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


async def run(base: str, users: int, duration: float, seed: int) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        workers = []
        for i in range(seed):
            email, creds = await _seed_user(client, base, i)
            workers.append((email, creds))
        # Each seed user is used by multiple concurrent workers (repeated logins
        # in the mix keep the auth path hot).
        assignment = [workers[i % len(workers)] for i in range(users)]

        results: list[dict] = []
        errors: list[dict] = []
        start = time.monotonic()
        deadline = start + duration
        await asyncio.gather(*[_worker(client, base, e, c, deadline, results, errors) for e, c in assignment])
        elapsed = time.monotonic() - start

    total = len(results)
    lats = sorted(r["ms"] for r in results)
    per_ep: dict[str, list[float]] = defaultdict(list)
    for r in results:
        per_ep[r["endpoint"]].append(r["ms"])
    ep_stats = {}
    for ep, vals in per_ep.items():
        vals = sorted(vals)
        ep_stats[ep] = {
            "n": len(vals),
            "p50_ms": round(_percentile(vals, 0.50), 1),
            "p95_ms": round(_percentile(vals, 0.95), 1),
            "p99_ms": round(_percentile(vals, 0.99), 1),
            "max_ms": round(vals[-1], 1),
        }
    http_errors = [e for e in errors if e["error"].startswith("HTTP")]
    return {
        "duration_s": round(elapsed, 1),
        "concurrency": users,
        "total_requests": total,
        "rps": round(total / elapsed, 1) if elapsed > 0 else 0,
        "overall_ms": {
            "p50": round(_percentile(lats, 0.50), 1),
            "p95": round(_percentile(lats, 0.95), 1),
            "p99": round(_percentile(lats, 0.99), 1),
            "mean": round(statistics.fmean(lats), 1) if lats else 0,
        },
        "error_rate_pct": round(100 * len(errors) / total, 2) if total else 0.0,
        "http_5xx": len(http_errors),
        "errors_sample": errors[:10],
        "per_endpoint": ep_stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dev API load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--users", type=int, default=8, help="concurrent workers")
    parser.add_argument("--duration", type=float, default=20, help="seconds")
    parser.add_argument("--seed", type=int, default=4, help="seed users to register")
    args = parser.parse_args(argv)

    result = asyncio.run(run(args.base_url, args.users, args.duration, args.seed))
    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result["base_url"] = args.base_url

    out_dir = Path(__file__).resolve().parents[1] / "data" / "loadtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"loadtest_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.json"
    out_path.write_text(json.dumps(result, indent=2))

    print(f"\nLoad test: {result['total_requests']} requests over {result['duration_s']}s at {result['concurrency']} concurrency")
    print(f"RPS: {result['rps']}  |  p50 {result['overall_ms']['p50']}ms  p95 {result['overall_ms']['p95']}ms  p99 {result['overall_ms']['p99']}ms  mean {result['overall_ms']['mean']}ms")
    print(f"Errors: {result['error_rate_pct']}% (HTTP 5xx: {result['http_5xx']})")
    print(f"{'endpoint':34} {'n':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}")
    for ep, s in sorted(result["per_endpoint"].items(), key=lambda kv: -kv[1]["n"]):
        print(f"{ep:34} {s['n']:>5} {s['p50_ms']:>8} {s['p95_ms']:>8} {s['p99_ms']:>8} {s['max_ms']:>8}")
    if result["errors_sample"]:
        print("Error sample:")
        for e in result["errors_sample"]:
            print(f"  {e['endpoint']}: {e['error']}")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
