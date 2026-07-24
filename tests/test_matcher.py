import json
from pathlib import Path

from app.config import Settings
from app.matcher import match_product
from app.models import PlannedItem, Product


FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, _settings):
        pass

    def complete_json(self, **_kwargs):
        return json.loads((FIXTURES / "matcher_pick.json").read_text())


def test_matcher_accepts_fixture_pick(monkeypatch):
    monkeypatch.setattr("app.matcher.HFModelClient", FakeClient)
    item = PlannedItem(search_term="milk", quantity=2, unit="l", raw_text="doodh 2L")
    candidates = [
        Product(
            id="milk-1l",
            name="Toned Milk",
            pack_size="1 L",
            price=56,
            handle="milk-1l",
        )
    ]

    decision = match_product(item, candidates, Settings(hf_token="test-token"))

    assert decision.product_id == "milk-1l"
    assert decision.units_to_add == 2


def test_matcher_returns_unmatched_for_empty_candidates():
    item = PlannedItem(search_term="rare ingredient")
    decision = match_product(item, [], Settings(hf_token="test-token"))
    assert decision.product_id is None
    assert decision.units_to_add == 0


def test_local_matcher_prefers_a_strong_previous_order():
    item = PlannedItem(search_term="toned milk", quantity=1, unit="l")
    candidates = [
        Product(
            id="cheap",
            name="Budget Toned Milk",
            pack_size="1 l",
            price=52,
            handle="cheap",
        ),
        Product(
            id="repeat",
            name="Everyday Toned Milk",
            pack_size="1 l",
            price=57,
            past_order_count=3,
            handle="repeat",
        ),
    ]

    decision = match_product(item, candidates, Settings(_env_file=None, model_backend="local"))

    assert decision.product_id == "repeat"
    assert "ordered 3 times before" in decision.reason


def test_local_matcher_uses_ratings_and_total_price_without_fabricating_them():
    item = PlannedItem(search_term="basmati rice", quantity=1, unit="kg")
    candidates = [
        Product(
            id="low-rating",
            name="Basmati Rice",
            pack_size="1 kg",
            price=190,
            rating=3.2,
            review_count=20,
            handle="low-rating",
        ),
        Product(
            id="best",
            name="Basmati Rice",
            pack_size="1 kg",
            price=195,
            rating=4.7,
            review_count=2500,
            handle="best",
        ),
    ]

    decision = match_product(item, candidates, Settings(_env_file=None, model_backend="local"))

    assert decision.product_id == "best"
    assert "4.7★" in decision.reason
