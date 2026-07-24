import json
from pathlib import Path

from app.config import Settings
from app.planner import plan_cart


FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, _settings):
        pass

    def complete_json(self, **_kwargs):
        return json.loads((FIXTURES / "planner_cart.json").read_text())


def test_planner_validates_and_keeps_expansion_source(monkeypatch):
    monkeypatch.setattr("app.planner.HFModelClient", FakeClient)
    settings = Settings(hf_token="test-token", model_backend="hf")

    plan = plan_cart(
        text="doodh 2L and paneer butter masala for 4 under 800",
        image_bytes=None,
        image_media_type="image/jpeg",
        settings=settings,
    )

    assert plan.items[0].search_term == "milk"
    assert plan.items[1].source == "expanded from: paneer butter masala"
    assert plan.constraints.cart_budget == 800
    assert plan.constraints.item_caps["milk"] == 130


def test_planner_requires_photo_or_text():
    settings = Settings(hf_token="test-token")
    try:
        plan_cart(
            text="",
            image_bytes=None,
            image_media_type="image/jpeg",
            settings=settings,
        )
    except ValueError as exc:
        assert "photo" in str(exc)
    else:
        raise AssertionError("Empty requests must be rejected")
