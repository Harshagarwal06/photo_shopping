from fastapi.testclient import TestClient

from app.main import app


def test_cross_origin_browser_post_is_rejected_before_cart_routes():
    response = TestClient(app).post(
        "/api/confirm",
        headers={"Origin": "https://malicious.example"},
        json={},
    )

    assert response.status_code == 403


def test_same_origin_browser_post_reaches_normal_validation():
    response = TestClient(app).post(
        "/api/confirm",
        headers={"Origin": "http://testserver"},
        json={},
    )

    assert response.status_code == 422


def test_spoofed_or_unsupported_image_uploads_fail_before_ocr():
    client = TestClient(app)

    spoofed = client.post(
        "/api/plans/preview",
        files={"image": ("fake.jpg", b"not an image", "image/jpeg")},
    )
    unsupported = client.post(
        "/api/plans/preview",
        files={"image": ("list.svg", b"<svg/>", "image/svg+xml")},
    )

    assert spoofed.status_code == 422
    assert unsupported.status_code == 415


def test_arbitrary_bytes_cannot_reach_ocr_behind_a_heic_content_type():
    """Pillow cannot decode HEIC, so the container is checked instead of trusted."""
    client = TestClient(app)

    spoofed = client.post(
        "/api/plans/preview",
        files={"image": ("list.heic", b"not an image at all", "image/heic")},
    )
    real_shape = client.post(
        "/api/plans/preview",
        files={"image": (
            "list.heic",
            b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1",
            "image/heic",
        )},
    )

    assert spoofed.status_code == 422
    # A genuine container gets past upload validation and fails later, in OCR,
    # rather than being rejected as unreadable bytes.
    assert real_shape.status_code != 422


def test_security_and_privacy_headers_cover_static_and_api_responses():
    client = TestClient(app)

    static = client.get("/")
    api = client.get("/api/health")

    for response in (static, api):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        policy = response.headers["content-security-policy"]
        assert "default-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "script-src 'self'" in policy
        assert "fonts.googleapis.com" not in response.text
    assert api.headers["cache-control"] == "no-store"
