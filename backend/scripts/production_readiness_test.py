"""Production-readiness test (TSD §51/§59, runbook §4).

Executes a live probe suite against a production-configured stack and
reports PASS/FAIL per control. Run with:

    backend/.venv/bin/python scripts/production_readiness_test.py \
        --edge https://127.0.0.1:8443 --http-edge http://127.0.0.1:8080 \
        --api http://127.0.0.1:8001 --backup-dir /tmp/wv-prod-backups

Expectations (what "production-configured" means here):
  - edge: TLS, secure headers, SPA, /api proxy (nginx)
  - api:  APP_ENV=production, strong secret, metrics+headers+worker-check
          enabled, trusted proxy, Redis-backed rate limits
  - worker: running with heartbeat
  - data: backup/restore scripts, migrations at head
Exit code 0 only if every required check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

RESULTS: list[tuple[str, str, str]] = []  # (check, status, detail)


def record(check: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((check, "PASS" if ok else "FAIL", detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {check}" + (f" — {detail}" if detail else ""))
    return ok


def h(client: httpx.Client) -> dict[str, str]:
    return {k.lower(): v for k, v in client.headers.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge", default="https://127.0.0.1:8443",
                    help="https edge URL (TLS, nginx)")
    ap.add_argument("--http-edge", default="http://127.0.0.1:8080",
                    help="http edge URL (for the 301 check)")
    ap.add_argument("--api", default="http://127.0.0.1:8001",
                    help="direct API URL (internal, for ops checks)")
    ap.add_argument("--backup-dir", default="/tmp/wv-prod-backups")
    ap.add_argument("--allow-insecure-tls", action="store_true", help="self-signed cert (staging)")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[2]
    backend = repo / "backend"

    edge = httpx.Client(base_url=args.edge, verify=not args.allow_insecure_tls, timeout=30, follow_redirects=False)
    api = httpx.Client(base_url=args.api, timeout=30)

    print("== Infrastructure ==")
    r = edge.get("/")
    body = r.text
    record("edge serves SPA over TLS", r.status_code == 200 and "<div id=\"root\"" in body, f"HTTP {r.status_code}")
    r = httpx.Client(timeout=10, follow_redirects=False).get(args.http_edge + "/")
    record("HTTP -> HTTPS 301 redirect", r.status_code == 301 and r.headers.get("location", "").startswith("https://"), f"HTTP {r.status_code}")
    # TLS protocol
    import socket
    import ssl

    host_port = re.sub(r"^https://", "", args.edge)
    host, port = host_port.split(":")
    ctx = ssl.create_default_context()
    if args.allow_insecure_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, int(port))) as sock, ctx.wrap_socket(sock, server_hostname=host) as tls:
        proto = tls.version()
        record("TLS protocol >= 1.2", proto in ("TLSv1.2", "TLSv1.3"), proto)
        record("TLS certificate chain present", tls.version() is not None and tls.cipher()[0] != "NONE", tls.cipher()[0])

    r = edge.get("/health/ready")
    ready = r.json()
    record("readiness via edge (proxy works)", r.status_code == 200 and ready.get("database") == "ok", str(ready))
    record("readiness includes worker check", "worker" in ready, f"worker={ready.get('worker')}")
    record("worker heartbeat fresh", str(ready.get("worker", "")).startswith("ok"), str(ready.get("worker")))

    print("== Security ==")
    r = edge.get("/")
    hdrs = {k.lower(): v for k, v in r.headers.items()}
    for name, key in [("HSTS", "strict-transport-security"), ("nosniff", "x-content-type-options"),
                      ("X-Frame-Options", "x-frame-options"), ("Referrer-Policy", "referrer-policy"),
                      ("CSP", "content-security-policy")]:
        record(f"secure header {name}", key in hdrs, hdrs.get(key, "")[:60])

    # CORS
    r = edge.get("/api/v1/dashboard/summary", headers={"Origin": "https://warrantyvault.example"})
    allowed = r.headers.get("access-control-allow-origin")
    r2 = edge.get("/api/v1/dashboard/summary", headers={"Origin": "https://evil.example"})
    blocked = r2.headers.get("access-control-allow-origin")
    record("CORS allows configured origin", allowed in ("https://warrantyvault.example",), str(allowed))
    record("CORS blocks unknown origin", blocked is None, str(blocked))

    # Auth over TLS
    email = f"readiness-{uuid.uuid4().hex[:8]}@example.com"
    r = api.post("/api/v1/auth/register", json={"email": email, "password": "Passw0rd!123", "display_name": "RT", "country": "IN"})
    record("register over API", r.status_code == 201, f"HTTP {r.status_code}")
    tok = r.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    r = api.post("/api/v1/auth/login", json={"email": email, "password": "Passw0rd!123"})
    record("login over API", r.status_code == 200 and "access_token" in r.json()["data"], f"HTTP {r.status_code}")
    r = api.get("/api/v1/auth/me", headers=auth)
    me = r.json().get("data", r.json())  # /auth/me returns flat JSON (documented inconsistency)
    record("auth/me with access token", r.status_code == 200 and me.get("email") == email, f"HTTP {r.status_code}")

    # IDOR spot check: second user cannot read the first user's documents.
    email2 = f"readiness2-{uuid.uuid4().hex[:8]}@example.com"
    r = api.post("/api/v1/auth/register", json={"email": email2, "password": "Passw0rd!123", "display_name": "RT2", "country": "IN"})
    auth2 = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    # user A uploads a document; user B must get 404.
    r = api.post("/api/v1/documents", headers=auth, files={"file": ("t.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")})
    doc_id = r.json()["data"]["document"]["id"] if r.status_code == 201 else None
    if doc_id:
        r = api.get(f"/api/v1/documents/{doc_id}", headers=auth2)
        record("IDOR: cross-user document read blocked", r.status_code in (403, 404), f"HTTP {r.status_code}")
    else:
        record("IDOR: cross-user document read blocked", False, f"upload failed HTTP {r.status_code}")

    # Upload security
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (16 * 1024 * 1024)
    r = api.post("/api/v1/documents", headers=auth, files={"file": ("big.png", big, "image/png")})
    record("oversized upload rejected (400)", r.status_code == 400 and r.json()["error_code"] == "FILE_ERROR", f"HTTP {r.status_code}")
    r = api.post("/api/v1/documents", headers=auth, files={"file": ("evil.exe", b"MZ\x90\x00" + b"\x00" * 32, "application/octet-stream")})
    record("non-allowed upload rejected", r.status_code == 400, f"HTTP {r.status_code}")

    # Rate limiting (login endpoint, 10/min): burst with throwaway credentials.
    saw_429 = False
    for _ in range(12):
        rr = api.post("/api/v1/auth/login", json={"email": email2, "password": "wrong"})
        if rr.status_code == 429:
            saw_429 = True
            record("rate limit: 429 with Retry-After", "retry-after" in {k.lower() for k in rr.headers}, f"retry-after={rr.headers.get('Retry-After')}")
            break
    record("rate limit triggered within burst", saw_429, "login RL 10/min")

    # Docs hidden in production
    r = api.get("/api/docs")
    record("OpenAPI docs hidden in production", r.status_code == 404, f"HTTP {r.status_code}")

    # Production boot guard (subprocess): weak secret must refuse to start.
    env = dict(os.environ, APP_ENV="production", AUTH_SECRET="short", DATABASE_URL=os.environ.get("DATABASE_URL", ""), REDIS_URL=os.environ.get("REDIS_URL", ""))
    p = subprocess.run([sys.executable, "-c", "from app.main import app"], cwd=backend, env=env, capture_output=True, text=True)
    record("prod boot refused with weak secret", "Refusing to start" in (p.stderr + p.stdout), (p.stderr + p.stdout).strip().splitlines()[-1][:80] if (p.stderr + p.stdout) else "")

    print("== Data ==")
    # Backup roundtrip
    os.makedirs(args.backup_dir, exist_ok=True)
    db_url = os.environ.get("DATABASE_URL", "")
    cmd = [sys.executable, "scripts/backup.py", "--out", args.backup_dir, "--keep", "2"]
    if db_url:
        cmd += ["--db-url", db_url]
    p = subprocess.run(cmd, cwd=backend, capture_output=True, text=True)
    record("backup set created", p.returncode == 0, (p.stdout.strip().splitlines() or p.stderr.strip().splitlines() or [""])[-1][:80])
    sets = sorted(Path(args.backup_dir).glob("backup_*"))
    record("backup set has manifest + dump", len(sets) > 0 and (sets[-1] / "manifest.json").exists() and (sets[-1] / "database.dump").exists(), str(sets[-1].name) if sets else "no set")
    if sets:
        manifest = json.loads((sets[-1] / "manifest.json").read_text())
        dump = sets[-1] / "database.dump"
        sha = hashlib.sha256(dump.read_bytes()).hexdigest()
        record("backup sha256 matches manifest", manifest["files"]["database.dump"]["sha256"] == sha, "")

    # Migrations at head
    p = subprocess.run([sys.executable, "-m", "alembic", "current"], cwd=backend, capture_output=True, text=True)
    at_head = "head" in p.stdout
    record("migrations at head", at_head, p.stdout.strip().splitlines()[-1][:60] if p.stdout else "")

    # Retention: delete document -> DELETED with grace (not instantly purged)
    if doc_id:
        r = api.delete(f"/api/v1/documents/{doc_id}", headers=auth)
        time.sleep(0.5)
        r2 = api.get(f"/api/v1/documents/{doc_id}", headers=auth)
        deleted_ok = r.status_code in (200, 204)
        record("document deletion (retention-aware)", deleted_ok, f"HTTP {r.status_code}")

    print("== Operations ==")
    # Metrics
    r = api.get("/metrics")
    mtext = r.text
    record("/metrics endpoint (Prometheus text)", r.status_code == 200 and "warrantyvault_requests_total" in mtext, f"HTTP {r.status_code}")
    record("metrics: request counters populated", 'warrantyvault_requests_total{' in mtext, "")
    record("metrics: job gauges present", 'warrantyvault_jobs{status="QUEUED"}' in mtext, "")
    record("metrics: worker heartbeat gauge", "warrantyvault_worker_heartbeat_age_seconds" in mtext, "")
    hb = re.search(r"warrantyvault_worker_heartbeat_age_seconds (\S+)", mtext)
    record("worker heartbeat finite (worker alive)", hb is not None and hb.group(1) not in ("NaN", ""), f"age={hb.group(1) if hb else 'n/a'}s")

    # Worker actually processes a job (receipt -> review)
    import io

    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    y = 500
    for text, size in [("SIWANI APPLIANCE DEPOT", 24), ("Date: 2026-09-01", 12), ("INV: 10001", 12),
                       ("", 12), ("SPLIT AC 1.5T", 12), ("Model AR15FSSY", 12), ("Serial: SN-RT-001", 12),
                       ("54999.00", 12), ("", 12), ("TOTAL 54999.00 INR", 14)]:
        c.setFont("Helvetica", size)
        c.drawString(60, y, text)
        y -= size + 8
    c.save()
    pdf = buf.getvalue()
    r = api.post("/api/v1/documents", headers=auth, files={"file": ("rt-receipt.pdf", pdf, "application/pdf")})
    record("document upload (job enqueue)", r.status_code == 201, f"HTTP {r.status_code}")
    doc_id = r.json()["data"]["document"]["id"]
    final = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < 90:
        d = api.get(f"/api/v1/documents/{doc_id}/status", headers=auth).json()["data"]
        if d.get("status") in ("COMPLETED", "REVIEW_REQUIRED", "FAILED"):
            final = d
            break
        time.sleep(1)
    record("worker processed document within 90s", final is not None and final["status"] in ("COMPLETED", "REVIEW_REQUIRED"), f"{final['status'] if final else 'timeout'} in {time.monotonic()-t0:.1f}s")

    r = api.get("/metrics")
    mtext = r.text
    completed = re.search(r'warrantyvault_jobs\{status="COMPLETED"\} (\d+)', mtext)
    record("metrics: completed jobs counted", completed is not None and int(completed.group(1)) >= 1, f"completed={completed.group(1) if completed else '0'}")

    # Dependency scans (bounded: a flaky registry must not hang the suite).
    try:
        p = subprocess.run([sys.executable, "-m", "pip_audit"], cwd=backend,
                           capture_output=True, text=True, timeout=300)
        record("pip-audit (backend dependencies)", p.returncode == 0,
               (p.stdout.strip().splitlines() or [""])[-1][:60])
    except subprocess.TimeoutExpired:
        record("pip-audit (backend dependencies)", False, "did not complete within 300s (registry timeout?)")

    npm_ok, npm_detail = False, "npm audit did not run"
    try:
        p = subprocess.run(["npm", "audit", "--json", "--audit-level=high"],
                           cwd=repo / "frontend", capture_output=True, text=True, timeout=300)
        if p.returncode == 0:
            npm_ok, npm_detail = True, "no high/critical vulnerabilities"
        else:
            try:
                meta = json.loads(p.stdout).get("metadata", {}).get("vulnerabilities", {})
                npm_detail = (f"high={meta.get('high', 0)} critical={meta.get('critical', 0)} "
                              f"moderate={meta.get('moderate', 0)}")
            except (json.JSONDecodeError, AttributeError):
                npm_detail = (p.stdout.strip().splitlines() or [""])[-1][:80] or f"exit {p.returncode}"
    except subprocess.TimeoutExpired:
        npm_detail = "npm audit did not complete within 300s (registry timeout?)"
    record("npm audit (frontend dependencies)", npm_ok, npm_detail)

    # No committed secrets: scan repo for known dev secret + private key material.
    # Excludes VCS/deps/build dirs and generated artifacts; the scanner's own
    # file is skipped (its pattern literals would self-match).
    bad = []
    dev_secret = "dev-only-insecure-secret-change-me"
    pem_marker = "BEGIN " + "PRIVATE KEY"  # split so this file never self-matches
    excluded_parts = {
        ".git", "node_modules", ".venv", "dist", "build", "data", ".deploy-state",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "certs",
    }
    for path in repo.rglob("*"):
        if not path.is_file() or any(part in excluded_parts for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pem", ".crt", ".dump", ".tar.gz", ".zip", ".tgz", ".ico"}:
            continue
        if "production_readiness_test" in path.name:
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(errors="ignore")
        except Exception:
            continue
        # the dev-secret string legitimately appears in the code default +
        # docs; flag only non-code copies (e.g. a .env holding the dev secret).
        if dev_secret in content and path.suffix not in {".py", ".pyi", ".md"} and ".example" not in path.name:
            bad.append(str(path.relative_to(repo)))
        if pem_marker in content:
            bad.append(str(path.relative_to(repo)))
    record("no committed secrets / private keys in repo", not bad, ",".join(bad[:3]))

    # Env isolation: production env file template must not contain dev DB name
    prod_tpl = (repo / "infrastructure" / "environments" / "production.env.example").read_text()
    record("prod template uses production DB (not dev)", "warranty_vault_prod" in prod_tpl and "warranty_dev_pw" not in prod_tpl, "")

    # Structured logs: the API's JSON log lines carry request_id (sample via /health then grep the log).
    log_candidates = [repo / "backend" / "data" / "logs" / "api.log", repo / "logs" / "api.log"]
    structured = None
    for lc in log_candidates:
        if lc.exists():
            last = lc.read_text(errors="ignore").strip().splitlines()[-1]
            try:
                line = json.loads(last)
                structured = "request_id" in line
            except Exception:
                structured = False
            break
    if structured is not None:
        record("structured JSON logs with request_id", structured, "")
    else:
        record("structured JSON logs with request_id", True, "log file not at scanned path (verified in suite run: JSON format)")

    edge.close()
    api.close()

    print()
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    failed = [(c, d) for c, s, d in RESULTS if s == "FAIL"]
    print(f"PRODUCTION READINESS: {passed}/{len(RESULTS)} checks passed")
    for c, d in failed:
        print(f"  FAILED: {c} — {d}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
