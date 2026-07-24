from app.local_vision import plan_locally


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
