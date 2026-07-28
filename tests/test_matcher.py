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


def test_cheapest_preference_can_outweigh_provider_position():
    item = PlannedItem(search_term="milk", context="prefer lowest total price")
    candidates = [
        _product("premium", "Toned Milk", 100),
        _product("value", "Toned Milk Value Pack", 55),
    ]

    assert _fallback_match(item, candidates).product_id == "value"


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


def test_provider_order_can_resolve_a_close_typo_but_not_an_unrelated_result():
    """Provider order is useful only after the request and product are plausibly related."""
    item = PlannedItem(search_term="Thumbs u", quantity=1, unit="item")
    candidates = [
        _product("thums", "Thums Up Soft Drink", 40),
        _product("pepsi", "Pepsi Soft Drink", 40),
        _product("coke", "Coca-Cola Soft Drink", 38),
    ]

    decision = _fallback_match(item, candidates)

    assert decision.product_id == "thums"

    unrelated = _fallback_match(
        PlannedItem(search_term="Bicad"),
        [_product("coffee", "Bevzilla Classic Instant Coffee Powder Sachets", 99)],
    )
    assert unrelated.product_id is None
    assert "No confident" in unrelated.reason


def test_a_short_brand_prefix_is_not_accepted_as_a_complete_query():
    decision = _fallback_match(
        PlannedItem(search_term="Mag"),
        [_product("maggi", "Maggi 2-Minute Noodles", 20)],
    )

    assert decision.product_id is None


def test_brand_and_product_modifier_must_survive_matching():
    branded = PlannedItem(search_term="mixed fruit juice", context="Real")
    brand_decision = _fallback_match(
        branded,
        [
            _product("rasna", "Rasna Jumpin Mixed Fruit Juice", 71),
            _product("real", "Real Mixed Fruit Juice", 110),
        ],
    )
    assert brand_decision.product_id == "real"

    cut = PlannedItem(search_term="chicken breast")
    cut_decision = _fallback_match(
        cut,
        [
            _product("curry", "Chicken Curry Cut", 120),
            _product("breast", "Chicken Breast Boneless", 180),
        ],
    )
    assert cut_decision.product_id == "breast"


def test_dietary_and_cut_modifiers_cannot_be_discarded_by_partial_overlap():
    sugar_free = PlannedItem(search_term="sugar free biscuits")
    assert _fallback_match(
        sugar_free,
        [_product("regular", "Sugar Biscuits", 20)],
    ).product_id is None
    assert _fallback_match(
        sugar_free,
        [_product("free", "Sugar Free Biscuits", 30)],
    ).product_id == "free"

    boneless = PlannedItem(search_term="boneless chicken breast")
    assert _fallback_match(
        boneless,
        [_product("bone-in", "Chicken Breast", 100)],
    ).product_id is None
    assert _fallback_match(
        boneless,
        [_product("boneless", "Boneless Chicken Breast", 150)],
    ).product_id == "boneless"


def test_generic_coke_does_not_become_diet_or_zero_sugar_coke():
    item = PlannedItem(search_term="Coke Can")
    candidates = [
        Product(
            id="multipack",
            name="Coca-Cola Original Taste Soft Drink - Pack of 8",
            pack_size="8 x 250 ml",
            price=160,
            handle="multipack",
        ),
        Product(
            id="regular",
            name="Coca-Cola Soft Drink",
            pack_size="300 ml",
            price=40,
            handle="regular",
        ),
        Product(
            id="diet",
            name="Diet Coke Diets & Lights",
            pack_size="330 ml",
            price=50,
            handle="diet",
        ),
        Product(
            id="zero",
            name="Coca-Cola Zero Sugar Soft Drink",
            pack_size="750 ml",
            price=38,
            handle="zero",
        ),
    ]

    assert _fallback_match(item, candidates).product_id == "regular"


def test_bare_coffee_does_not_become_coffee_flavoured_milk():
    item = PlannedItem(search_term="Coffee")
    candidates = [
        Product(
            id="milk",
            name="Amul Kool Cafe Milk 'n' Coffee Flavoured Milk",
            pack_size="200 ml",
            price=30,
            handle="milk",
        ),
        Product(
            id="coffee",
            name="Nescafe Classic - 100% Pure Instant Coffee Powder - 24 g",
            pack_size="24 g",
            price=124,
            handle="coffee",
        ),
    ]

    assert _fallback_match(item, candidates).product_id == "coffee"


def test_rin_soap_matches_its_detergent_bar_without_weakening_generic_soap():
    rin = PlannedItem(search_term="soap", context="Rin")
    candidates = [
        _product("pears", "Pears Pure & Gentle Glycerin Soap", 50),
        _product("rin", "Rin Detergent Bar", 10),
        _product("surf", "Surf Excel Stain Eraser Detergent Bar", 10),
    ]

    assert rin.provider_query == "Rin soap"
    assert _fallback_match(rin, candidates).product_id == "rin"
    # The equivalence is intentionally brand-scoped: an unbranded bathing-soap
    # request must not silently become laundry detergent.
    assert _fallback_match(PlannedItem(search_term="soap"), candidates).product_id == "pears"


def test_nail_polish_matches_retail_paint_and_enamel_names():
    candidates = [
        _product("paint", "Miss Nails Nail Paint", 99),
        _product("enamel", "Faces Canada Nail Enamel", 149),
    ]

    assert (
        _fallback_match(PlannedItem(search_term="nail polish"), candidates).product_id
        == "paint"
    )


def test_uncertain_transcription_is_never_matched_until_confirmed():
    item = PlannedItem(search_term="Ma", needs_review=True)
    candidates = [_product("maggi", "Maggi Instant Noodles", 20)]

    assert _fallback_match(item, candidates).product_id is None
    item.confirmed = True
    assert _fallback_match(item, candidates).product_id is None


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
