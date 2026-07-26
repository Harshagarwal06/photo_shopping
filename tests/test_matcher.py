import json
from pathlib import Path

from app.config import Settings
from app.matcher import _fallback_match, match_product
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


def _product(product_id: str, name: str, price: float) -> Product:
    return Product(id=product_id, name=name, price=price, handle=product_id)


def test_the_providers_top_result_wins_a_tie_price_would_otherwise_take():
    """A photographed "nhite" matches neither bread, so relevance ties and the
    cheaper loaf used to win. Blinkit ranked the right one first."""
    item = PlannedItem(search_term="Modern nhite bread", quantity=1, unit="item")
    # The five results Blinkit actually returned, in the order it returned them.
    candidates = [
        _product("white", "Modern White Bread", 45),
        _product("wheat", "Modern 100% Whole Wheat Bread (Zero Maida)", 60),
        _product("oven", "English Oven Sandwich White Bread", 45),
        _product("britannia", "Britannia Vitarich Sandwich White Bread", 40),
        _product("fruit", "Modern Fruit Bread", 28),
    ]

    decision = _fallback_match(item, candidates)

    assert decision.product_id == "white"


def test_the_providers_order_decides_when_nothing_in_the_query_matches():
    """"Thumbs u" matches no product name at all, so every candidate scored zero
    relevance and a ₹2 difference chose Coca-Cola over Thums Up."""
    item = PlannedItem(search_term="Thumbs u", quantity=1, unit="item")
    candidates = [
        _product("thums", "Thums Up Soft Drink", 40),
        _product("pepsi", "Pepsi Soft Drink", 40),
        _product("coke", "Coca-Cola Soft Drink", 38),
    ]

    decision = _fallback_match(item, candidates)

    assert decision.product_id == "thums"


def test_position_does_not_override_a_genuinely_better_match():
    """The provider's order is a prior, not the answer: a candidate that matches
    the words must still beat whatever the provider happened to rank first."""
    item = PlannedItem(search_term="fruit bread", quantity=1, unit="item")
    candidates = [
        _product("white", "Modern White Bread", 45),
        _product("fruit", "Modern Fruit Bread", 60),
    ]

    decision = _fallback_match(item, candidates)

    assert decision.product_id == "fruit"


def test_a_previously_ordered_product_still_outranks_the_top_result():
    item = PlannedItem(search_term="milk", quantity=1, unit="item")
    first = _product("new", "Amul Taaza Toned Milk", 30)
    known = _product("usual", "Mother Dairy Toned Milk", 32)
    known.past_order_count = 6

    decision = _fallback_match(item, [first, known])

    assert decision.product_id == "usual"
