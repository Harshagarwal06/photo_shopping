"""Robustness and fail-closed checks for realistic phone-photo degradation."""

import shutil
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageEnhance, ImageFilter

from app.local_vision import plan_locally

FIXTURE = Path(__file__).parent / "fixtures" / "grocery_list_brands.jpeg"
EXPECTED = {
    ("tomato soup powder", "knorr"),
    ("soap", "rin"),
    ("ice cream sandwich", ""),
    ("kitkat", ""),
    ("blue ball pen", ""),
    ("puffcorn", "kurkure"),
    ("salt", "tata"),
    ("cocoa powder", ""),
}
requires_vision = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("swift") is None,
    reason="macOS Vision OCR needs Darwin and the Swift driver.",
)


def evaluated(transform) -> tuple[int, list]:
    source = Image.open(FIXTURE).convert("RGB")
    image = transform(source)
    output = BytesIO()
    image.save(output, format="JPEG", quality=88)
    plan = plan_locally(
        text="",
        image_bytes=output.getvalue(),
        image_media_type="image/jpeg",
    )
    found = {
        (item.search_term.casefold(), item.context.casefold()) for item in plan.items
    }
    return len(EXPECTED & found), plan.items


@requires_vision
@pytest.mark.parametrize(
    ("transform", "minimum_exact"),
    [
        (lambda image: image.rotate(4, expand=True, fillcolor="white"), 4),
        (lambda image: image.rotate(-8, expand=True, fillcolor="white"), 5),
        (lambda image: ImageEnhance.Contrast(image).enhance(0.55), 5),
        (lambda image: ImageEnhance.Brightness(image).enhance(0.55), 5),
        (lambda image: image.filter(ImageFilter.GaussianBlur(1.5)), 7),
        (
            lambda image: image.resize(
                (450, round(image.height * 450 / image.width))
            ),
            5,
        ),
    ],
)
def test_degraded_photos_keep_a_measured_accuracy_floor(transform, minimum_exact):
    exact, _ = evaluated(transform)

    assert exact >= minimum_exact


@requires_vision
def test_tilted_photo_does_not_auto_approve_two_merged_categories():
    _, items = evaluated(
        lambda image: image.rotate(4, expand=True, fillcolor="white")
    )
    merged = [
        item
        for item in items
        if "soap" in item.search_term.casefold()
        and "sandwich" in item.search_term.casefold()
    ]

    assert merged
    assert all(item.needs_review for item in merged)
