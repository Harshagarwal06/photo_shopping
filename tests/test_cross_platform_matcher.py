import pytest

from app.config import Settings
from app.constraints import parse_measurement
from app.llm import ModelBackendError
from app.matcher import match_across_platforms
from app.models import PlannedItem, Product


def _settings() -> Settings:
    # safety_lock keeps the deterministic fallback path active, no network.
    return Settings(_env_file=None, safety_lock=True)


def _llm_settings() -> Settings:
    # Clears every guard in match_across_platforms so the (mocked) model is
    # actually consulted and the sanitization loop runs for real.
    return Settings(_env_file=None, safety_lock=False, demo_mode=False, model_backend="hf")


def _product(pid: str, name: str, pack: str, price: float) -> Product:
    return Product(id=pid, name=name, pack_size=pack, price=price, handle=pid)


def _fake_client_returning(payload: dict) -> type:
    """Build a stand-in for app.matcher.HFModelClient whose complete_json
    returns a fixed, attacker-controlled-looking payload."""

    class FakeClient:
        def __init__(self, _settings):
            pass

        def complete_json(self, **_kwargs):
            return payload

    return FakeClient


def _fake_client_raising(exc: Exception) -> type:
    class FakeClient:
        def __init__(self, _settings):
            pass

        def complete_json(self, **_kwargs):
            raise exc

    return FakeClient


def test_picks_one_product_per_platform():
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    result = match_across_platforms(
        item,
        {
            "blinkit": [_product("b1", "Amul Taaza Toned Milk", "1 L", 75.0)],
            "zepto": [_product("z1", "Amul Taaza Toned Milk", "1 L", 72.0)],
        },
        _settings(),
    )
    assert set(result.picks) == {"blinkit", "zepto"}
    assert result.picks["blinkit"].product_id == "b1"
    assert result.picks["zepto"].product_id == "z1"


def test_platform_with_no_candidates_reports_no_equivalent():
    item = PlannedItem(search_term="paneer", quantity=200, unit="g")
    result = match_across_platforms(
        item,
        {"blinkit": [_product("b1", "Amul Paneer", "200 g", 95.0)], "zepto": []},
        _settings(),
    )
    assert result.picks["zepto"].product_id is None
    assert result.picks["zepto"].units_to_add == 0
    assert "zepto" in result.equivalence_note.lower()


def test_out_of_stock_only_platform_reports_no_equivalent():
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    out_of_stock = _product("z1", "Amul Taaza", "1 L", 72.0)
    out_of_stock.in_stock = False
    result = match_across_platforms(
        item,
        {"blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)], "zepto": [out_of_stock]},
        _settings(),
    )
    assert result.picks["zepto"].product_id is None
    assert result.picks["zepto"].units_to_add == 0


def test_units_scale_to_the_pack_size_each_platform_stocks():
    """1 L requested: one 1 L pack on Blinkit, two 500 ml packs on Zepto."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    result = match_across_platforms(
        item,
        {
            "blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)],
            "zepto": [_product("z1", "Amul Taaza", "500 ml", 40.0)],
        },
        _settings(),
    )
    assert result.picks["blinkit"].units_to_add == 1
    assert result.picks["zepto"].units_to_add == 2


def test_empty_provider_map_returns_empty_picks():
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    assert match_across_platforms(item, {}, _settings()).picks == {}


def test_one_provider_uses_the_normal_single_cart_pick():
    item = PlannedItem(search_term="black pen", quantity=1, unit="item")
    candidates = [
        _product("five", "Reynolds Brite Ball Pen Set (Black)", "5 pcs", 25.0),
        _product("one", "Reynolds Trimax Roller Pen (Black)", "1 pc", 50.0),
    ]

    result = match_across_platforms(
        item,
        {"blinkit": candidates},
        _settings(),
    )

    assert result.picks["blinkit"].product_id == "one"


def test_exact_product_variant_wins_before_cheaper_mixed_brands():
    item = PlannedItem(search_term="toor dal", quantity=1, unit="item")
    result = match_across_platforms(
        item,
        {
            "blinkit": [
                _product("b-cheap", "Whole Farm Grocery Toor Dal", "1 kg", 150.0),
                _product(
                    "b-exact", "Tata Sampann Unpolished Toor Dal/Arhar Dal", "1 kg", 171.0
                ),
            ],
            "instamart": [
                _product("i-cheap", "Basic Toor Dal", "1 kg", 147.0),
                _product(
                    "i-exact", "Tata Sampann Unpolished Toor/Arhar Dal", "1 kg", 186.0
                ),
            ],
            "zepto": [
                _product("z-cheap", "Daily Good Toor Dal Regular", "1 kg", 149.0),
                _product(
                    "z-exact", "Tata Sampann Unpolished Toor Dal/Arhar Dal", "1 kg", 171.0
                ),
            ],
        },
        _settings(),
    )

    assert {pick.product_id for pick in result.picks.values()} == {
        "b-exact",
        "i-exact",
        "z-exact",
    }
    assert "same product/variant" in result.equivalence_note.lower()


def test_common_brand_wins_when_exact_variant_is_not_shared():
    item = PlannedItem(search_term="atta", quantity=1, unit="item")
    result = match_across_platforms(
        item,
        {
            "blinkit": [
                _product("b-cheap", "Organic Tattva Wheat Organic Atta", "1 kg", 63.0),
                _product("b-brand", "Aashirvaad Organic Atta", "1 kg", 65.0),
            ],
            "instamart": [
                _product("i-cheap", "Nature Fresh MP Superior Atta", "1 kg", 60.0),
                _product("i-brand", "Aashirvaad Multigrains Atta", "1 kg", 74.0),
            ],
            "zepto": [
                _product("z-cheap", "Daily Good Sharbati Atta", "1 kg", 49.0),
                _product("z-brand", "Aashirvaad High Fibre Atta", "1 kg", 78.0),
            ],
        },
        _settings(),
    )

    assert {pick.product_id for pick in result.picks.values()} == {
        "b-brand",
        "i-brand",
        "z-brand",
    }
    assert "same aashirvaad brand" in result.equivalence_note.lower()


def test_common_brand_prefers_closest_shared_variant_before_price_or_position():
    item = PlannedItem(search_term="rice", quantity=1, unit="item")
    result = match_across_platforms(
        item,
        {
            "blinkit": [
                _product(
                    "b-super",
                    "Daawat Rozana-Super Basmati Rice (Medium Grain)",
                    "1 kg",
                    93.0,
                )
            ],
            "instamart": [
                _product(
                    "i-super", "Daawat Basmati Rice - Rozana Super", "1 kg", 90.0
                )
            ],
            "zepto": [
                _product(
                    "z-gold",
                    "Daawat Rozana Basmati Rice Gold | Medium Grain",
                    "1 kg",
                    92.0,
                ),
                _product(
                    "z-super",
                    "Daawat Rozana Super Basmati Rice | Medium Grain",
                    "1 kg",
                    93.0,
                ),
            ],
        },
        _settings(),
    )

    assert result.picks["zepto"].product_id == "z-super"


def test_price_breaks_ties_only_after_exact_equivalence_is_established():
    item = PlannedItem(search_term="toor dal", quantity=1, unit="item")
    result = match_across_platforms(
        item,
        {
            "blinkit": [
                _product("b-tata", "Tata Sampann Toor Dal", "1 kg", 171.0),
                _product("b-fortune", "Fortune Toor Dal", "1 kg", 160.0),
            ],
            "instamart": [
                _product("i-tata", "Tata Sampann Toor Dal", "1 kg", 186.0),
                _product("i-fortune", "Fortune Toor Dal", "1 kg", 164.0),
            ],
            "zepto": [
                _product("z-tata", "Tata Sampann Toor Dal", "1 kg", 171.0),
                _product("z-fortune", "Fortune Toor Dal", "1 kg", 162.0),
            ],
        },
        _settings(),
    )

    assert {pick.product_id for pick in result.picks.values()} == {
        "b-fortune",
        "i-fortune",
        "z-fortune",
    }


def test_different_brands_are_used_only_with_explicit_disclosure():
    item = PlannedItem(search_term="toor dal", quantity=1, unit="item")
    result = match_across_platforms(
        item,
        {
            "blinkit": [_product("b1", "Whole Farm Grocery Toor Dal", "1 kg", 150.0)],
            "instamart": [_product("i1", "Basic Toor Dal", "1 kg", 147.0)],
            "zepto": [_product("z1", "Daily Good Toor Dal Regular", "1 kg", 149.0)],
        },
        _settings(),
    )

    assert "no common brand was available" in result.equivalence_note.lower()
    assert all(
        "no common brand was available" in pick.reason.lower()
        for pick in result.picks.values()
    )


# --- Comparability: platforms must deliver the same amount, or none. ----------
# A list written without quantities ("rajma") gives the matcher nothing to
# verify against, so each platform used to pick its own best-value pack and the
# totals were compared at face value. Live, that compared 250 g at Rs48 against
# 500 g at Rs93 and reported the dearer one as cheaper: Rs19.2 against Rs18.6
# per 100 g.


def _delivered(result, provider: str, candidates: list[Product]) -> float | None:
    """Grams or millilitres a platform's pick actually delivers."""
    decision = result.picks[provider]
    if decision.product_id is None:
        return None
    product = next(p for p in candidates if p.id == decision.product_id)
    measurement = parse_measurement(product.pack_size)
    return measurement[0] * decision.units_to_add if measurement else None


def test_platforms_converge_on_the_same_delivered_quantity():
    """With no quantity requested, an equivalent pack exists on both platforms
    and both must choose it -- not each platform's own best value."""
    item = PlannedItem(search_term="rajma", quantity=1, unit="item")
    blinkit = [
        _product("b-250", "Whole Farm Premium Red Rajma", "250 g", 48.0),
        _product("b-500", "Whole Farm Premium Red Rajma", "500 g", 95.0),
    ]
    instamart = [_product("i-500", "Tata Sampann Unpolished Rajma", "500 g", 93.0)]

    result = match_across_platforms(
        item, {"blinkit": blinkit, "instamart": instamart}, _settings()
    )

    assert _delivered(result, "blinkit", blinkit) == _delivered(
        result, "instamart", instamart
    )


def test_the_live_failure_buys_multiple_packs_rather_than_comparing_unequal_ones():
    """The live failure: 250 g at Rs48 against 500 g at Rs93, reported as half
    the price. Two 250 g packs reach the same 500 g, so the honest comparison is
    Rs96 against Rs93 -- and Instamart, the cheaper one per gram, wins."""
    item = PlannedItem(search_term="rajma", quantity=1, unit="item")
    blinkit = [_product("b-250", "Whole Farm Premium Red Rajma", "250 g", 48.0)]
    instamart = [_product("i-500", "Tata Sampann Unpolished Rajma", "500 g", 93.0)]

    result = match_across_platforms(
        item, {"blinkit": blinkit, "instamart": instamart}, _settings()
    )

    assert result.picks["blinkit"].units_to_add == 2
    assert _delivered(result, "blinkit", blinkit) == _delivered(
        result, "instamart", instamart
    )


def test_a_platform_that_cannot_reach_the_stated_amount_is_not_compared():
    """500 g asked for, and one platform stocks only a 2 kg sack. No number of
    packs lands inside the band, so comparing its price would mislead."""
    item = PlannedItem(search_term="rajma", quantity=500, unit="g")
    blinkit = [_product("b-2kg", "Whole Farm Red Rajma", "2 kg", 260.0)]
    instamart = [_product("i-500", "Tata Sampann Rajma", "500 g", 93.0)]

    result = match_across_platforms(
        item, {"blinkit": blinkit, "instamart": instamart}, _settings()
    )

    assert result.picks["blinkit"].product_id is None
    assert result.picks["blinkit"].units_to_add == 0
    assert result.picks["instamart"].product_id == "i-500"
    assert "blinkit" in result.equivalence_note.lower()


def test_a_stated_quantity_is_the_reference_not_the_catalogue():
    """500 g asked for: the 500 g pack wins even though 1 kg is better value."""
    item = PlannedItem(search_term="rajma", quantity=500, unit="g")
    blinkit = [
        _product("b-1kg", "Whole Farm Red Rajma", "1 kg", 150.0),
        _product("b-500", "Whole Farm Red Rajma", "500 g", 95.0),
    ]
    instamart = [_product("i-500", "Tata Sampann Rajma", "500 g", 93.0)]

    result = match_across_platforms(
        item, {"blinkit": blinkit, "instamart": instamart}, _settings()
    )

    assert result.picks["blinkit"].product_id == "b-500"
    assert result.picks["instamart"].product_id == "i-500"


def test_hosted_picks_at_unequal_amounts_are_rejected(monkeypatch):
    """Id validation is not enough. A hosted model can name real, in-stock,
    correctly-branded products that deliver different amounts, which is the same
    misleading comparison arriving through a path that looks trustworthy."""
    monkeypatch.setattr(
        "app.matcher.HFModelClient",
        _fake_client_returning(
            {
                "picks": {
                    "blinkit": {"product_id": "b-250", "units_to_add": 1,
                                "reason": "cheapest"},
                    "instamart": {"product_id": "i-500", "units_to_add": 1,
                                  "reason": "cheapest"},
                },
                "equivalence_note": "Both are rajma.",
            }
        ),
    )
    item = PlannedItem(search_term="rajma", quantity=1, unit="item")
    blinkit = [_product("b-250", "Whole Farm Premium Red Rajma", "250 g", 48.0)]
    instamart = [_product("i-500", "Tata Sampann Unpolished Rajma", "500 g", 93.0)]

    result = match_across_platforms(
        item, {"blinkit": blinkit, "instamart": instamart}, _llm_settings()
    )

    assert _delivered(result, "blinkit", blinkit) == _delivered(
        result, "instamart", instamart
    )


def test_hosted_mixed_brand_picks_cannot_bypass_available_common_brand(monkeypatch):
    candidates = {
        "blinkit": [
            _product("b-cheap", "Whole Farm Grocery Toor Dal", "1 kg", 150.0),
            _product("b-common", "Tata Sampann Toor Dal", "1 kg", 171.0),
        ],
        "instamart": [
            _product("i-cheap", "Basic Toor Dal", "1 kg", 147.0),
            _product("i-common", "Tata Sampann Toor Dal", "1 kg", 186.0),
        ],
        "zepto": [
            _product("z-cheap", "Daily Good Toor Dal", "1 kg", 149.0),
            _product("z-common", "Tata Sampann Toor Dal", "1 kg", 171.0),
        ],
    }
    monkeypatch.setattr(
        "app.matcher.HFModelClient",
        _fake_client_returning(
            {
                "picks": {
                    "blinkit": {
                        "product_id": "b-cheap",
                        "units_to_add": 1,
                        "reason": "cheapest",
                    },
                    "instamart": {
                        "product_id": "i-cheap",
                        "units_to_add": 1,
                        "reason": "cheapest",
                    },
                    "zepto": {
                        "product_id": "z-cheap",
                        "units_to_add": 1,
                        "reason": "cheapest",
                    },
                },
                "equivalence_note": "All are toor dal.",
            }
        ),
    )

    result = match_across_platforms(
        PlannedItem(search_term="toor dal", quantity=1, unit="item"),
        candidates,
        _llm_settings(),
    )

    assert {pick.product_id for pick in result.picks.values()} == {
        "b-common",
        "i-common",
        "z-common",
    }


def test_unmeasurable_packs_still_match_independently():
    """Pieces and loose items have no parseable amount; refusing everything
    would be worse than comparing the best match on each platform."""
    item = PlannedItem(search_term="kitkat", quantity=1, unit="item")
    result = match_across_platforms(
        item,
        {
            "blinkit": [_product("b1", "KitKat Chocolate Bar", "1 pc", 20.0)],
            "zepto": [_product("z1", "KitKat Chocolate Bar", "1 pc", 22.0)],
        },
        _settings(),
    )

    assert result.picks["blinkit"].product_id == "b1"
    assert result.picks["zepto"].product_id == "z1"


# --- LLM path: the sanitization loop is the entire trust boundary. ------------
# Every test below reaches the (mocked) model by using settings that clear the
# demo_mode / safety_lock / model_backend=="local" guards in
# match_across_platforms, then asserts the untrusted model output never
# survives unverified.


def test_hallucinated_product_id_falls_back(monkeypatch):
    """A product_id that appears in no candidate list at all must be rejected."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {"blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)]}
    payload = {
        "picks": {
            "blinkit": {"product_id": "made-up-id", "units_to_add": 1, "reason": "looks right"}
        },
        "equivalence_note": "matched",
    }
    monkeypatch.setattr("app.matcher.HFModelClient", _fake_client_returning(payload))

    result = match_across_platforms(item, candidates, _llm_settings())

    assert result.picks["blinkit"].product_id == "b1"


def test_out_of_stock_product_id_falls_back(monkeypatch):
    """A product_id that is real but out of stock must be rejected."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    in_stock = _product("b1", "Amul Taaza", "1 L", 75.0)
    out_of_stock = _product("b2", "Amul Taaza", "1 L", 74.0)
    out_of_stock.in_stock = False
    candidates = {"blinkit": [in_stock, out_of_stock]}
    payload = {
        "picks": {"blinkit": {"product_id": "b2", "units_to_add": 1, "reason": "cheapest"}},
        "equivalence_note": "matched",
    }
    monkeypatch.setattr("app.matcher.HFModelClient", _fake_client_returning(payload))

    result = match_across_platforms(item, candidates, _llm_settings())

    assert result.picks["blinkit"].product_id == "b1"


def test_cross_platform_id_leak_falls_back(monkeypatch):
    """Blinkit's product id returned under the zepto key must be rejected —
    it is not a member of zepto's own candidate list."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {
        "blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)],
        "zepto": [_product("z1", "Amul Taaza", "1 L", 72.0)],
    }
    payload = {
        "picks": {
            "blinkit": {"product_id": "b1", "units_to_add": 1, "reason": "ok"},
            "zepto": {"product_id": "b1", "units_to_add": 1, "reason": "same brand"},
        },
        "equivalence_note": "matched",
    }
    monkeypatch.setattr("app.matcher.HFModelClient", _fake_client_returning(payload))

    result = match_across_platforms(item, candidates, _llm_settings())

    assert result.picks["zepto"].product_id == "z1"
    assert result.picks["zepto"].product_id != "b1"


def test_invented_provider_key_is_dropped(monkeypatch):
    """A pick returned under a provider key that was never in the input
    (e.g. the model inventing 'instamart') must not appear in the result at
    all — there is no candidate list to verify it against."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {
        "blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)],
        "zepto": [_product("z1", "Amul Taaza", "1 L", 72.0)],
    }
    payload = {
        "picks": {
            "blinkit": {"product_id": "b1", "units_to_add": 1, "reason": "ok"},
            "zepto": {"product_id": "z1", "units_to_add": 1, "reason": "ok"},
            "instamart": {"product_id": "unverified-id", "units_to_add": 1, "reason": "invented"},
        },
        "equivalence_note": "matched",
    }
    monkeypatch.setattr("app.matcher.HFModelClient", _fake_client_returning(payload))

    result = match_across_platforms(item, candidates, _llm_settings())

    assert set(result.picks) == {"blinkit", "zepto"}
    assert "instamart" not in result.picks


def test_omitted_input_provider_falls_back_instead_of_going_missing(monkeypatch):
    """A provider that WAS in the input but the model left out of its picks
    must still appear in the result, resolved via the fallback."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {
        "blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)],
        "zepto": [_product("z1", "Amul Taaza", "1 L", 72.0)],
    }
    payload = {
        "picks": {"blinkit": {"product_id": "b1", "units_to_add": 1, "reason": "ok"}},
        "equivalence_note": "matched",
    }
    monkeypatch.setattr("app.matcher.HFModelClient", _fake_client_returning(payload))

    result = match_across_platforms(item, candidates, _llm_settings())

    assert "zepto" in result.picks
    assert result.picks["zepto"].product_id == "z1"


def test_malformed_payload_falls_back_via_validation_error(monkeypatch):
    """A structurally invalid payload (picks is not an object) must be
    recovered via the deterministic fallback, not raise out of the function."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {"blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)]}
    payload = {"picks": ["not", "a", "mapping"], "equivalence_note": "matched"}
    monkeypatch.setattr("app.matcher.HFModelClient", _fake_client_returning(payload))

    result = match_across_platforms(item, candidates, _llm_settings())

    assert result.picks["blinkit"].product_id == "b1"


def test_model_backend_error_reraises_when_fallback_disabled(monkeypatch):
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {"blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)]}
    monkeypatch.setattr(
        "app.matcher.HFModelClient", _fake_client_raising(ModelBackendError("boom"))
    )
    settings = Settings(
        _env_file=None,
        safety_lock=False,
        demo_mode=False,
        model_backend="hf",
        local_vision_fallback=False,
    )

    with pytest.raises(ModelBackendError):
        match_across_platforms(item, candidates, settings)


def test_model_backend_error_falls_back_when_fallback_enabled(monkeypatch):
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    candidates = {"blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)]}
    monkeypatch.setattr(
        "app.matcher.HFModelClient", _fake_client_raising(ModelBackendError("boom"))
    )
    settings = Settings(
        _env_file=None,
        safety_lock=False,
        demo_mode=False,
        model_backend="hf",
        local_vision_fallback=True,
    )

    result = match_across_platforms(item, candidates, settings)

    assert result.picks["blinkit"].product_id == "b1"
