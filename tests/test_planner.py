import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.models import CartPlan, PlannedItem
from app.planner import plan_cart, retry_uncertain_with_cloud


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


def test_cloud_retry_sends_only_uncertain_line_crops_and_merges_correction(monkeypatch):
    captured = {}

    class CorrectionClient:
        def __init__(self, _settings):
            pass

        def complete_json(self, **kwargs):
            captured.update(kwargs)
            return {
                "corrections": [
                    {
                        "id": "uncertain",
                        "search_term": "Maggi",
                        "context": "",
                        "quantity": 1,
                        "unit": "item",
                        "raw_text": "Maggi",
                    }
                ]
            }

    monkeypatch.setattr("app.planner.HFModelClient", CorrectionClient)
    source = Image.new("RGB", (1200, 1600), "white")
    image_bytes = BytesIO()
    source.save(image_bytes, format="JPEG")
    plan = CartPlan(
        items=[
            PlannedItem(
                id="certain",
                search_term="peanut butter",
                crop_box=[0.1, 0.7, 0.4, 0.05],
            ),
            PlannedItem(
                id="uncertain",
                search_term="Ma",
                raw_text="5. Ma",
                needs_review=True,
                confidence=0.2,
                crop_box=[0.1, 0.5, 0.25, 0.05],
            ),
        ]
    )

    result = retry_uncertain_with_cloud(
        plan=plan,
        image_bytes=image_bytes.getvalue(),
        settings=Settings(_env_file=None, model_backend="local", hf_token="test"),
    )

    assert [item.search_term for item in result.items] == ["peanut butter", "Maggi"]
    assert result.items[1].needs_review is True
    assert result.items[1].confirmed is False
    sent = Image.open(BytesIO(captured["image_bytes"]))
    assert sent.width < source.width
    assert sent.height < source.height
    assert "uncertain line crops" in result.processing_note.casefold()
    assert "remain marked for review" in result.processing_note.casefold()
