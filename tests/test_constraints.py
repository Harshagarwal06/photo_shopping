from app.constraints import enforce_constraints, parse_measurement, units_for_candidate
from app.models import CartConstraints, DraftItem, PlannedItem, Product


def product(product_id: str, name: str, pack: str, price: float) -> Product:
    return Product(
        id=product_id,
        name=name,
        pack_size=pack,
        price=price,
        handle=product_id,
    )


def test_measurement_normalises_metric_units():
    assert parse_measurement("Family pack 1.5 kg") == (1500, "g")
    assert parse_measurement("Bottle 2 L") == (2000, "ml")
    assert parse_measurement("Tray 12 pcs") == (12, "count")


def test_purchasable_units_do_not_turn_eggs_into_trays():
    item = PlannedItem(search_term="eggs", quantity=12, unit="count")
    tray = product("eggs-12", "Fresh eggs", "12 pcs", 120)
    six_pack = product("eggs-6", "Fresh eggs", "6 pcs", 65)
    assert units_for_candidate(item, tray) == 1
    assert units_for_candidate(item, six_pack) == 2


def test_item_cap_swaps_to_candidate_that_fits():
    item = PlannedItem(search_term="coffee", quantity=1, unit="pack")
    expensive = product("premium", "Premium Coffee", "200 g", 320)
    affordable = product("value", "Value Coffee", "200 g", 180)
    draft_item = DraftItem(
        planned=item,
        candidates=[expensive, affordable],
        selected_product_id=expensive.id,
        units_to_add=1,
        reason="Best flavour match.",
    )

    draft = enforce_constraints(
        [draft_item],
        CartConstraints(item_caps={"coffee": 200}),
        dry_run=True,
    )

    assert draft.items[0].selected_product_id == affordable.id
    assert draft.total == 180
    assert any("item cap" in flag for flag in draft.items[0].flags)


def test_budget_violation_is_flagged_not_silently_removed():
    item = PlannedItem(search_term="milk", quantity=2, unit="l")
    milk = product("milk", "Toned Milk", "1 L", 60)
    draft_item = DraftItem(
        planned=item,
        candidates=[milk],
        selected_product_id=milk.id,
        units_to_add=2,
        reason="Exact amount.",
    )

    draft = enforce_constraints(
        [draft_item],
        CartConstraints(cart_budget=100),
        dry_run=True,
    )

    assert draft.total == 120
    assert draft.items[0].selected_product_id == milk.id
    assert "over budget" in draft.flags[0]


def test_runaway_quantity_is_capped_and_flagged():
    item = PlannedItem(search_term="salt")
    salt = product("salt", "Salt", "1 kg", 30)
    draft_item = DraftItem(
        planned=item,
        candidates=[salt],
        selected_product_id=salt.id,
        units_to_add=999,
        reason="Bad model output.",
    )

    draft = enforce_constraints([draft_item], CartConstraints(), dry_run=True)
    assert draft.items[0].units_to_add == 50
    assert any("capped" in flag for flag in draft.items[0].flags)

