"""Unit tests: upload validation & malware scan (TSD §36)."""

import pytest

from app.core.exceptions import FileError
from app.services import file_validation as fv

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PDF = b"%PDF-1.4\n" + b"0" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 16
EXE = b"MZ\x90\x00" + b"0" * 16
ZIP = b"PK\x03\x04" + b"0" * 16


def test_detect_mime_by_signature():
    assert fv.detect_mime(PNG, None) == "image/png"
    assert fv.detect_mime(JPEG, None) == "image/jpeg"
    assert fv.detect_mime(PDF, None) == "application/pdf"
    assert fv.detect_mime(WEBP, None) == "image/webp"


def test_mismatched_signature_rejected():
    with pytest.raises(FileError):
        fv.detect_mime(b"plain text content", "image/png")


def test_sanitize_filename_strips_traversal():
    assert fv.sanitize_filename("../../etc/passwd") == "passwd"
    assert fv.sanitize_filename("C:\\Users\\x\\photo.PNG") == "photo.PNG"
    assert fv.sanitize_filename("a/b/c.png") == "c.png"
    assert fv.sanitize_filename("   ") == "document"
    assert fv.sanitize_filename("ok name_1.2.png") == "ok_name_1.2.png"


def test_executable_rejected_by_extension():
    with pytest.raises(FileError):
        fv.validate_upload("evil.exe", EXE, "application/octet-stream")


def test_executable_disguised_as_png_rejected_by_signature():
    with pytest.raises(FileError):
        fv.validate_upload("disguised.png", EXE, "image/png")


def test_zip_rejected():
    with pytest.raises(FileError):
        fv.validate_upload("archive.zip", ZIP, "application/zip")


def test_size_limit_enforced(monkeypatch):
    fv.settings.upload_max_bytes = 10
    with pytest.raises(FileError):
        fv.validate_upload("big.png", PNG, "image/png")
    fv.settings.upload_max_bytes = 15 * 1024 * 1024


def test_empty_rejected():
    with pytest.raises(FileError):
        fv.validate_upload("empty.png", b"", "image/png")


def test_valid_png_accepted():
    result = fv.validate_upload("photo.png", PNG, "image/png")
    assert result.mime_type == "image/png"
    assert result.sha256
    assert result.clean if hasattr(result, "clean") else True
    assert result.scan.clean is True


def test_scan_flags_pdf_with_javascript():
    evil_pdf = PDF + b"/JavaScript (function(){alert(1)})"
    result = fv.scan_file(evil_pdf, "application/pdf")
    assert result.clean is False
    assert "pdf_embedded_javascript" in result.findings


def test_scan_clean_pdf():
    result = fv.scan_file(PDF, "application/pdf")
    assert result.clean is True
    assert result.scanner == "heuristic"
