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
