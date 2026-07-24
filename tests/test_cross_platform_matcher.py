from app.config import Settings
from app.matcher import match_across_platforms
from app.models import PlannedItem, Product


def _settings() -> Settings:
    # safety_lock keeps the deterministic fallback path active, no network.
    return Settings(_env_file=None, safety_lock=True)


def _product(pid: str, name: str, pack: str, price: float) -> Product:
    return Product(id=pid, name=name, pack_size=pack, price=price, handle=pid)


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
