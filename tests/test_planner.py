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


def test_demo_mode_parses_the_submitted_request_instead_of_a_fixed_cart():
    plan = plan_cart(
        text="cheapest coffee 2 packs under ₹500",
        image_bytes=None,
        image_media_type="image/jpeg",
        settings=Settings(_env_file=None, demo_mode=True, model_backend="hf"),
    )

    assert [item.search_term for item in plan.items] == ["coffee"]
    assert plan.items[0].quantity == 2
    assert plan.constraints.cart_budget == 500
    assert plan.constraints.preferences == ["cheapest"]
    assert "locally" in plan.processing_note.casefold()


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
    # The retry strip spans the line even when the local word box covered only
    # the first few recognized characters.
    assert sent.width >= source.width * 0.9
    assert "uncertain line crops" in result.processing_note.casefold()
    assert "remain marked for review" in result.processing_note.casefold()


def test_groq_all_uncertain_rows_use_one_context_plus_detail_request(monkeypatch):
    captured = []

    class ContextClient:
        def __init__(self, _settings):
            pass

        def complete_json(self, **kwargs):
            captured.append(kwargs)
            return {
                "corrections": [
                    {
                        "id": f"line-{index}",
                        "search_term": name,
                        "context": "",
                        "quantity": quantity,
                        "unit": unit,
                        "raw_text": raw,
                    }
                    for index, (name, quantity, unit, raw) in enumerate(
                        [
                            ("Mung Dal", 1, "item", "Mung Dal"),
                            ("Garam Masala", 200, "g", "Garam Masala 200 gm"),
                            ("Aam ka Achar", 100, "g", "Aam ka Achar 100 gm"),
                        ]
                    )
                ]
            }

    monkeypatch.setattr("app.planner.GroqModelClient", ContextClient)
    source = Image.new("RGB", (1200, 800), "white")
    image_bytes = BytesIO()
    source.save(image_bytes, format="PNG")
    plan = CartPlan(
        items=[
            PlannedItem(
                id=f"line-{index}",
                search_term="uncertain",
                raw_text=f"uncertain {index}",
                needs_review=True,
                confidence=0.2,
                crop_box=[0, y, 0.5, 0.08],
            )
            for index, y in enumerate((0.8, 0.5, 0.2))
        ]
    )

    result = retry_uncertain_with_cloud(
        plan=plan,
        image_bytes=image_bytes.getvalue(),
        settings=Settings(
            _env_file=None,
            model_backend="local",
            groq_api_key="gsk-test",
            cloud_model_backend="groq",
        ),
        blind=True,
    )

    assert len(captured) == 1
    assert "top section is the complete list" in captured[0]["prompt"].casefold()
    sent = Image.open(BytesIO(captured[0]["image_bytes"]))
    assert sent.height > source.height
    assert [item.search_term for item in result.items] == [
        "Mung Dal",
        "Garam Masala",
        "Aam ka Achar",
    ]
    assert [item.quantity for item in result.items] == [1, 200, 100]
    assert "complete image was rechecked" in result.processing_note.casefold()
