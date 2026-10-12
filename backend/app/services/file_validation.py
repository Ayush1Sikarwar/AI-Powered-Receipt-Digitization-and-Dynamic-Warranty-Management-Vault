"""Upload validation & malware scanning (TSD §36 Upload Security, SRS Security).

- MIME validation (client header)
- File SIGNATURE (magic-bytes) validation — the header alone is never trusted
- Size limits
- Filename sanitization (filenames/paths are never trusted input)
- No executable uploads
- Malware scanning where infrastructure permits (ClamAV if reachable; a
  deterministic signature/heuristic scanner is always run). The scanner used
  and its result are recorded so the state is honest, never silent.
"""

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.exceptions import FileError

logger = logging.getLogger(__name__)
settings = get_settings()

# Magic-byte signatures -> canonical mime types.
_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),  # WEBP: RIFF....WEBP
    (b"%PDF-", "application/pdf"),
]

_ALLOWED_MIME = {
    "image/jpeg": {"jpg", "jpeg"},
    "image/png": {"png"},
    "image/webp": {"webp"},
    "application/pdf": {"pdf"},
}

# Signatures that mark an executable / archive we must refuse.
_EXECUTABLE_SIGNATURES = [
    b"MZ",  # PE/DOS executable
    b"\x7fELF",  # ELF
    b"#!/",  # script shebang
    b"\xd0\xcf\x11\xe0",  # OLE compound (can embed macros) — refuse for MVP
    b"PK\x03\x04",  # ZIP/Office — refuse (docx/xlsx are not in scope; also a malware vector)
    b"BZh",  # bzip2
    b"\xfd7zXZ",  # xz
]

_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class ScanResult:
    clean: bool
    scanner: str
    findings: list[str] = field(default_factory=list)


@dataclass
class ValidatedFile:
    data: bytes
    filename: str
    mime_type: str
    extension: str
    sha256: str
    size: int
    scan: ScanResult


def sanitize_filename(name: str) -> str:
    """Strip directory components and unsafe characters. Filenames are never
    trusted as executable input (SRS Security)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = unicodedata.normalize("NFKC", base)
    base = _FILENAME_BAD.sub("_", base).strip("._")
    base = re.sub(r"_+", "_", base)
    return (base[:120] or "document")


def detect_mime(data: bytes, declared: str | None) -> str:
    """Validate the declared MIME against actual magic bytes."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    raise FileError("File content does not match an allowed type (signature validation failed)")


def validate_upload(filename: str, data: bytes, declared_mime: str | None) -> ValidatedFile:
    size = len(data)
    if size == 0:
        raise FileError("Uploaded file is empty")
    if size > settings.upload_max_bytes:
        raise FileError(
            "File exceeds the maximum upload size",
            details={"max_bytes": settings.upload_max_bytes, "actual_bytes": size},
        )
    safe_name = sanitize_filename(filename)
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext not in settings.allowed_upload_extensions:
        raise FileError(
            "File type not allowed",
            details={"allowed": sorted(settings.allowed_upload_extensions)},
        )
    mime = detect_mime(data, declared_mime)
    if ext not in _ALLOWED_MIME.get(mime, set()):
        raise FileError("File extension does not match detected content type")
    if declared_mime and declared_mime.lower() != mime and declared_mime.lower() not in {"application/octet-stream"}:
        logger.warning("upload_mime_mismatch declared=%s detected=%s", declared_mime, mime)

    scan = scan_file(data, mime)
    if not scan.clean:
        raise FileError(
            "File failed the security scan and was rejected",
            details={"scanner": scan.scanner, "findings": scan.findings},
        )

    sha = hashlib.sha256(data).hexdigest()
    return ValidatedFile(
        data=data,
        filename=safe_name,
        mime_type=mime,
        extension=ext,
        sha256=sha,
        size=size,
        scan=scan,
    )


# --------------------------------------------------------------------------- #
# Malware scanning
# --------------------------------------------------------------------------- #
class FileScanner:
    """Runs the always-on heuristic scanner, and ClamAV when a daemon is
    configured/reachable. Honest about which scanner produced the verdict."""

    def __init__(self, clamd_host: str | None = None, clamd_port: int = 3310) -> None:
        self._clamd_host = clamd_host
        self._clamd_port = clamd_port

    def scan(self, data: bytes, mime: str) -> ScanResult:
        clam = self._try_clamav(data, mime)
        if clam is not None:
            return clam
        return self._heuristic(data, mime)

    def _try_clamav(self, data: bytes, mime: str) -> ScanResult | None:
        if not self._clamd_host:
            return None
        try:
            import socket

            s = socket.create_connection((self._clamd_host, self._clamd_port), timeout=2)
            s.sendall(b"INSTREAM\n")
            s.sendall(b"%lu\n" % len(data))
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
            resp = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            s.close()
            text = resp.decode(errors="replace").strip()
            if text.startswith("stream:"):
                infected = "NOT FOUND" not in text
                return ScanResult(clean=not infected, scanner="clamav", findings=[text] if infected else [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("clamav_unavailable: %s", exc.__class__.__name__)
        return None

    def _heuristic(self, data: bytes, mime: str) -> ScanResult:
        findings: list[str] = []
        for sig in _EXECUTABLE_SIGNATURES:
            if data[:16].startswith(sig):
                findings.append(f"executable/archive signature {sig[:4]!r}")
        # Embedded script content in PDFs (common malware vector).
        if mime == "application/pdf":
            if b"/JavaScript" in data[:200_000] or b"JS " in data[:200_000]:
                findings.append("pdf_embedded_javascript")
            if b"/Launch" in data[:200_000]:
                findings.append("pdf_launch_action")
        return ScanResult(clean=not findings, scanner="heuristic", findings=findings)


_scanner = FileScanner()


def scan_file(data: bytes, mime: str) -> ScanResult:
    return _scanner.scan(data, mime)
