"""End-to-end regressions from the two user-supplied handwritten grocery lists."""

import shutil
import sys
from pathlib import Path

import pytest

from app.local_vision import plan_locally


FIXTURES = Path(__file__).parent / "fixtures"
requires_vision = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("swift") is None,
    reason="macOS Vision OCR needs Darwin and the Swift driver.",
)


def structured(name: str):
    image = (FIXTURES / name).read_bytes()
    plan = plan_locally(
        text="",
        image_bytes=image,
        image_media_type="image/jpeg",
    )
    return [
        (
            item.search_term.casefold(),
            item.context.casefold(),
            item.quantity,
            item.unit,
            item.needs_review,
        )
        for item in plan.items
    ]


@requires_vision
def test_quantity_list_is_structured_without_spurious_items():
    assert structured("grocery_list_quantities.jpeg") == [
        ("milk", "", 2, "l", False),
        ("bread", "", 1, "pack", False),
        ("pencil box", "", 1, "item", False),
        ("cornflakes", "", 2, "pack", False),
        ("butter", "amul", 1, "item", False),
        ("cheese", "amul", 1, "item", False),
    ]


@requires_vision
def test_numbered_list_has_seven_rows_and_quarantines_only_truncated_magggi():
    rows = structured("grocery_list_numbered.jpeg")

    assert rows == [
        ("oreo", "", 1, "item", False),
        ("mixed fruit juice", "real", 1, "item", False),
        ("peanut butter", "", 1, "item", False),
        ("chicken breast", "", 1, "item", False),
        ("ma", "", 1, "item", True),
        ("oregano", "", 1, "item", False),
        ("basmati rice", "", 1, "item", False),
    ]
    assert all(name != "8" for name, *_ in rows)


@requires_vision
def test_brand_heavy_numbered_list_has_eight_stable_separate_rows():
    expected = [
        ("tomato soup powder", "knorr", 1, "item", False),
        ("soap", "rin", 1, "item", False),
        ("ice cream sandwich", "", 1, "item", False),
        ("kitkat", "", 1, "item", False),
        ("blue ball pen", "", 1, "item", False),
        ("puffcorn", "kurkure", 1, "item", False),
        ("salt", "tata", 1, "item", False),
        ("cocoa powder", "", 1, "item", False),
    ]

    assert structured("grocery_list_brands.jpeg") == expected
    # Candidate ordering and the review gate must not change between identical
    # reads of the same photograph.
    assert structured("grocery_list_brands.jpeg") == expected
