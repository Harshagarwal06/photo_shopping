from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageEnhance, ImageFilter

from app.image_quality import analyze_image_quality
from app.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "grocery_list_brands.jpeg"


def encoded(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="JPEG", quality=88)
    return output.getvalue()


def test_clear_handwriting_fixture_passes_capture_quality():
    report = analyze_image_quality(FIXTURE.read_bytes())

    assert report.status == "good"
    assert report.score >= 0.8
    assert report.issues == []


def test_small_blurred_and_dark_photos_fail_with_specific_guidance():
    source = Image.open(FIXTURE).convert("RGB")
    cases = {
        "resolution": source.resize((300, 533)),
        "blur": source.filter(ImageFilter.GaussianBlur(4)),
        "darkness": ImageEnhance.Brightness(source).enhance(0.25),
    }

    for expected_issue, image in cases.items():
        report = analyze_image_quality(encoded(image))
        assert report.status == "retake"
        assert expected_issue in report.issues
        assert report.guidance


def test_quality_endpoint_returns_only_metrics_and_capture_guidance():
    response = TestClient(app).post(
        "/api/images/quality",
        files={"image": ("list.jpg", FIXTURE.read_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "good"
    assert payload["metrics"]["shortest_edge"] == 900
    assert "text" not in payload
