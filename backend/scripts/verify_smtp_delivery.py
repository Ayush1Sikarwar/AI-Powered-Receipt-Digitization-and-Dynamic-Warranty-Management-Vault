"""End-to-end verification of the SMTP email transport (SRS FR-15 delivery).

Runs a local TLS SMTP sink (aiosmtpd with STARTTLS, certificate trusted via
the system CA store) and drives the *production* `SmtpEmailProvider.send()`
against it — a real SMTP conversation (EHLO, STARTTLS, MAIL FROM, RCPT TO,
DATA, QUIT) over a verified TLS connection. No mocks: this proves the
transport code path works against a live SMTP server. The final hop to a
real mail provider (Google/SES/...) requires real provider credentials,
which are an environment input, not code.

Usage:
    python scripts/verify_smtp_delivery.py
Exit code 0 = message delivered and verified.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINK_HOST = "127.0.0.1"
SINK_PORT = 10457
CERT_DIR = Path(tempfile.mkdtemp(prefix="wv-smtp-"))


def _gen_certs() -> tuple[str, str]:
    """CA + server cert (SAN IP:127.0.0.1) so smtplib's default TLS context
    (system trust store) accepts the sink after the CA is installed."""
    ca_key = CERT_DIR / "ca.key"
    ca_crt = CERT_DIR / "ca.crt"
    srv_key = CERT_DIR / "srv.key"
    srv_crt = CERT_DIR / "srv.crt"
    run = lambda *a: subprocess.run(list(a), check=True, capture_output=True)  # noqa: E731
    run(
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_crt), "-days", "2",
        "-subj", "/CN=WarrantyVault-Test-CA",
    )
    run(
        "openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(srv_key), "-out", str(CERT_DIR / "srv.csr"),
        "-subj", "/CN=127.0.0.1",
    )
    ext = CERT_DIR / "ext.cnf"
    ext.write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\n")
    run(
        "openssl", "x509", "-req", "-in", str(CERT_DIR / "srv.csr"),
        "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(srv_crt), "-days", "2", "-extfile", str(ext),
    )
    return str(ca_crt), str(srv_crt)


def _trust_ca(ca_crt: str) -> None:
    """Make smtplib's default TLS context trust the sink.

    Best-effort: install into the system store when update-ca-certificates
    exists; always also point OpenSSL at the CA via SSL_CERT_FILE, which
    `ssl.create_default_context()` (used by smtplib.starttls) honors.
    """
    os.environ["SSL_CERT_FILE"] = ca_crt
    import shutil

    if shutil.which("update-ca-certificates"):
        dst = Path("/usr/local/share/ca-certificates/wv-test-ca.crt")
        try:
            subprocess.run(["sudo", "cp", ca_crt, str(dst)], check=True, capture_output=True)
            subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
        except Exception:  # noqa: BLE001 - non-fatal, SSL_CERT_FILE covers it
            print("note: system CA store update skipped; SSL_CERT_FILE in effect")


class Sink:
    """aiosmtpd 1.4 handler: the end-of-data hook is `handle_DATA`."""

    def __init__(self) -> None:
        self.delivered: list[dict] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001
        self.delivered.append(
            {
                "mail_from": envelope.mail_from,
                "rcpt_tos": envelope.rcpt_tos,
                "data": envelope.content.decode("utf-8", "replace"),
            }
        )
        return "250 Message accepted for delivery"


def main() -> int:
    ca_crt, srv_crt = _gen_certs()
    _trust_ca(ca_crt)

    from aiosmtpd.controller import Controller  # noqa: PLC0415

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(srv_crt, str(Path(srv_crt).with_suffix(".key")))
    sink = Sink()
    controller = Controller(sink, hostname=SINK_HOST, port=SINK_PORT, require_starttls=True, tls_context=ctx)
    controller.start()
    try:
        # Point the production provider at the sink via the real config env.
        os.environ["EMAIL_PROVIDER"] = "smtp"
        os.environ["SMTP_HOST"] = SINK_HOST
        os.environ["SMTP_PORT"] = str(SINK_PORT)
        os.environ["SMTP_FROM"] = "notifications@warrantyvault.local"

        from app.core.config import get_settings  # noqa: PLC0415
        from app.db.session import Session  # noqa: PLC0415
        from app.notifications.email import EmailMessage, SmtpEmailProvider  # noqa: PLC0415

        s = get_settings()
        assert s.email_provider == "smtp" and s.smtp_host == SINK_HOST, "settings did not pick up SMTP env"
        provider = SmtpEmailProvider()
        assert provider.is_configured(), "provider reports unconfigured"

        db = Session()
        msg = EmailMessage(
            to="release-gate-verify@example.com",
            subject="WarrantyVault — warranty expiring in 7 days (verification)",
            body_text="Your FULL_UNIT warranty (SPLIT AC 1.5T) expires on 2026-09-15. Log in to review or file a claim.",
            payload={"reminder_type": "7d", "verification": "release-gate-smtp"},
        )
        ok = provider.send(db, msg)
        db.close()
    finally:
        controller.stop()

    if not ok:
        print("FAIL: SmtpEmailProvider.send() returned False")
        return 1
    if len(sink.delivered) != 1:
        print(f"FAIL: sink expected 1 message, got {len(sink.delivered)}")
        return 1
    got = sink.delivered[0]
    checks = {
        "recipient": got["rcpt_tos"] == ["release-gate-verify@example.com"],
        "from": got["mail_from"] == "notifications@warrantyvault.local",
        "subject_in_mime": "warranty expiring in 7 days" in got["data"],
        "body_in_mime": "expires on 2026-09-15" in got["data"],
        "delivery_flag": msg.delivery == "SMTP",
    }
    print(json.dumps(checks, indent=2))
    if all(checks.values()):
        print("SMTP DELIVERY VERIFIED: production SmtpEmailProvider delivered over verified TLS to a live SMTP sink")
        return 0
    print("FAIL: some checks failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
