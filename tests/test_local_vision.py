import pytest

from app.local_vision import _parse_item, plan_locally


@pytest.mark.parametrize(
    ("line", "term", "quantity", "unit"),
    [
        # A dozen is a count of twelve, not one of something.
        ("dozen eggs", "eggs", 12, "count"),
        ("1 dozen eggs", "eggs", 12, "count"),
        ("2 dozen bananas", "bananas", 24, "count"),
        ("eggs 1 dozen", "eggs", 12, "count"),
        # "packet" used to be left in the search term, so the provider was asked
        # for "packet maggi".
        ("2 packet maggi", "maggi", 2, "pack"),
        ("besan 1 packet", "besan", 1, "pack"),
        ("packet bread", "bread", 1, "pack"),
        # Fractions, written both ways.
        ("1/2 kg paneer", "paneer", 0.5, "kg"),
        ("½ kg tomatoes", "tomatoes", 0.5, "kg"),
        ("1½ kg atta", "atta", 1.5, "kg"),
        ("3/4 l milk", "milk", 0.75, "l"),
        # A decimal is not a numbered-list marker: "2." must not be stripped.
        ("2.5 kg rice", "rice", 2.5, "kg"),
        ("1. milk 2 l", "milk", 2, "l"),
    ],
)
def test_parses_dozens_packets_and_fractions(line, term, quantity, unit):
    item = _parse_item(line, "photo")

    assert item is not None
    assert (item.search_term, item.quantity, item.unit) == (term, quantity, unit)


@pytest.mark.parametrize("line", ["1/0 kg broken", "0 kg nothing"])
def test_unusable_quantity_falls_back_instead_of_raising(line):
    """A zero denominator must not divide by zero, and 0 fails PlannedItem's gt=0."""
    item = _parse_item(line, "photo")

    assert item is not None
    assert item.quantity == 1


def test_local_planner_combines_typed_and_recognized_items(monkeypatch):
    monkeypatch.setattr(
        "app.local_vision.recognize_text",
        lambda _bytes, _media_type: "12 eggs\nrice 2 kg",
    )

    plan = plan_locally(
        text="milk 2 l, under 800",
        image_bytes=b"image",
        image_media_type="image/jpeg",
    )

    assert [item.search_term for item in plan.items] == ["milk", "eggs", "rice"]
    assert [item.quantity for item in plan.items] == [2, 12, 2]
    assert plan.constraints.cart_budget == 800
    assert "locally on this Mac" in plan.processing_note
