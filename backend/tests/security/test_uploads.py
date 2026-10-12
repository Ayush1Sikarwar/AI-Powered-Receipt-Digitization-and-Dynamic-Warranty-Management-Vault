"""Security tests: malicious file upload, spoofing, traversal, rate limits (TSD §41)."""

import io

from PIL import Image, ImageDraw

from tests.conftest import auth_headers

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def png_bytes():
    img = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(img).text((10, 10), "x", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_executable_upload_rejected(app_client, registered_user):
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("payload.exe", b"MZ\x90\x00" + b"x" * 20, "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "FILE_ERROR"


def test_zip_upload_rejected(app_client, registered_user):
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("doc.zip", b"PK\x03\x04" + b"x" * 20, "application/zip")},
    )
    assert resp.status_code == 400


def test_mime_spoofing_rejected(app_client, registered_user):
    """A text file renamed to .png must fail signature validation."""
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("fake.png", b"this is definitely not a png", "image/png")},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "FILE_ERROR"


def test_disguised_executable_rejected(app_client, registered_user):
    """MZ bytes with a .png extension must be rejected (content wins)."""
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("photo.png", b"MZ\x90\x00" + b"x" * 20, "image/png")},
    )
    assert resp.status_code == 400


def test_path_traversal_filename_sanitized(app_client, registered_user):
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("../../etc/passwd.png", png_bytes(), "image/png")},
    )
    assert resp.status_code == 201
    stored = resp.json()["data"]["document"]["original_filename"]
    assert ".." not in stored
    assert stored == "passwd.png"


def test_oversized_upload_rejected(app_client, registered_user):
    big = b"x" * (15 * 1024 * 1024 + 1)
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "FILE_ERROR"


def test_upload_rate_limit_enforced(app_client, registered_user):
    codes = []
    for _ in range(22):
        resp = app_client.post(
            "/api/v1/documents",
            headers=auth_headers(registered_user),
            files={"file": (f"r{_}.png", png_bytes(), "image/png")},
        )
        codes.append(resp.status_code)
    assert 429 in codes, f"expected a 429 within 22 uploads, got {codes}"


def test_no_sensitive_data_in_error(app_client, registered_user):
    resp = app_client.post(
        "/api/v1/documents",
        headers=auth_headers(registered_user),
        files={"file": ("bad.exe", b"MZ" + b"x" * 10, "application/octet-stream")},
    )
    body = resp.json()
    assert "Traceback" not in str(body)
    assert "stack" not in str(body).lower()
