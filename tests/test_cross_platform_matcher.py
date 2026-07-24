import pytest

from app.config import Settings
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
