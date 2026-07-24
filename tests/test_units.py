from app.models import PlannedItem, Product
from app.units import fill_ratio, per_unit_price


def _product(pack_size: str, price: float, name: str = "Test Product") -> Product:
    return Product(id="p1", name=name, pack_size=pack_size, price=price, handle="h1")


def _item(quantity: float, unit: str) -> PlannedItem:
    return PlannedItem(search_term="milk", quantity=quantity, unit=unit)


def test_per_unit_price_for_one_litre():
    assert per_unit_price(_product("1 L", 75.0), 1) == (0.075, "ml")


def test_per_unit_price_accounts_for_multiple_units():
    """Two 500 ml packs at ₹40 each is ₹80 for 1000 ml."""
    assert per_unit_price(_product("500 ml", 40.0), 2) == (0.08, "ml")


def test_per_unit_price_falls_back_to_name_when_pack_size_blank():
    assert per_unit_price(_product("", 50.0, name="Aashirvaad Atta 1 kg"), 1) == (0.05, "g")


def test_per_unit_price_unparseable_returns_none():
    """Never guess. An unparseable pack is not price-comparable."""
    assert per_unit_price(_product("1 combo", 99.0, name="Party Combo"), 1) is None


def test_per_unit_price_zero_units_returns_none():
    assert per_unit_price(_product("1 L", 75.0), 0) is None


def test_fill_ratio_exact_match():
    assert fill_ratio(_item(1, "l"), _product("1 L", 75.0), 1) == 1.0


def test_fill_ratio_detects_shortfall():
    """500 ml supplied against 1 L requested is half the basket."""
    assert fill_ratio(_item(1, "l"), _product("500 ml", 40.0), 1) == 0.5


def test_fill_ratio_dimension_mismatch_returns_none():
    """Grams cannot answer a request in litres."""
    assert fill_ratio(_item(1, "l"), _product("500 g", 40.0), 1) is None


def test_fill_ratio_unknown_request_returns_none():
    assert fill_ratio(_item(1, "item"), _product("1 L", 75.0), 1) is None
