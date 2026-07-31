import pytest

from app.cartproof import prove_platform
from app.compare import build_outcome, rank
from app.config import Settings
from app.contracts import build_contract, confirm_contract
from app.models import (
    CartConstraints,
    CartLine,
    CartPlan,
    CartSummary,
    ContractConfirmRequest,
    DraftCart,
    DraftItem,
    ItemContract,
    PlannedItem,
    Product,
    RequirementLevel,
    ShoppingContract,
    SubstitutionPolicy,
)


def _product(
    product_id: str,
    name: str,
    pack_size: str,
    price: float,
) -> Product:
    return Product(
        id=product_id,
        name=name,
        pack_size=pack_size,
        price=price,
        handle=product_id,
    )


def _draft(
    provider: str,
    planned: PlannedItem,
    product: Product | None,
    units: int = 1,
) -> DraftCart:
    return DraftCart(
        provider_id=provider,
        provider_name=provider.title(),
        items=[
            DraftItem(
                planned=planned,
                candidates=[product] if product else [],
                selected_product_id=product.id if product else None,
                units_to_add=units if product else 0,
            )
        ],
    )


def _summary(provider: str, product: Product, units: int = 1) -> CartSummary:
    total = product.price * units
    return CartSummary(
        provider=provider,
        lines=[
            CartLine(
                product_id=product.id,
                name=product.name,
                quantity=units,
                unit_price=product.price,
                line_total=total,
            )
        ],
        subtotal=total,
        total=total,
    )


def _contract(
    planned: PlannedItem,
    *,
    brand: str | None = None,
    brand_level: RequirementLevel = RequirementLevel.FLEXIBLE,
    quantity_level: RequirementLevel = RequirementLevel.REQUIRED,
    budget: float | None = None,
) -> ShoppingContract:
    return ShoppingContract(
        items=[
            ItemContract(
                planned_item_id=planned.id,
                product_name=planned.search_term,
                quantity=planned.quantity,
                unit=planned.unit,
                quantity_level=quantity_level,
                brand=brand,
                brand_level=brand_level,
                substitution_policy=(
                    SubstitutionPolicy.SAME_BRAND
                    if brand_level == RequirementLevel.REQUIRED
                    else SubstitutionPolicy.EQUIVALENT
                    if brand
                    else SubstitutionPolicy.ANY
                ),
            )
        ],
        cart_budget=budget,
        status="confirmed",
        fingerprint="confirmed-contract",
    )


def test_contract_builder_keeps_hard_rules_conservative():
    plan = CartPlan(
        items=[
            PlannedItem(
                search_term="milk",
                context="Amul",
                raw_text="Amul milk 2 litres",
                quantity=2,
                unit="l",
            )
        ]
    )

    contract = build_contract(plan)

    assert contract.items[0].brand == "amul"
    assert contract.items[0].brand_level == RequirementLevel.PREFERRED
    assert contract.items[0].quantity_level == RequirementLevel.REQUIRED
    assert contract.status == "draft"


def test_cart_budget_digits_do_not_turn_default_item_quantity_into_a_hard_rule():
    plan = CartPlan(
        items=[
            PlannedItem(
                search_term="coffee",
                context="cheapest under ₹800",
                raw_text="cheapest coffee under ₹800",
            )
        ],
        constraints=CartConstraints(cart_budget=800),
    )

    contract = build_contract(plan)

    assert contract.cart_budget == 800
    assert contract.items[0].quantity_level == RequirementLevel.FLEXIBLE


def test_explicit_single_container_is_still_a_required_quantity():
    plan = CartPlan(
        items=[
            PlannedItem(
                search_term="bread",
                raw_text="Bread (one loaf)",
                quantity=1,
                unit="pack",
            )
        ]
    )

    contract = build_contract(plan)

    assert contract.items[0].quantity_level == RequirementLevel.REQUIRED


def test_contract_confirmation_fingerprints_the_exact_version():
    plan = CartPlan(items=[PlannedItem(search_term="milk")])
    draft = build_contract(plan)

    confirmed = confirm_contract(
        draft,
        ContractConfirmRequest(
            version=draft.version,
            items=draft.items,
            cart_budget=500,
        ),
        plan,
    )

    assert confirmed.status == "confirmed"
    assert len(confirmed.fingerprint) == 64
    assert confirmed.cart_budget == 500


def test_contract_rejects_missing_plan_item():
    plan = CartPlan(
        items=[
            PlannedItem(search_term="milk"),
            PlannedItem(search_term="eggs"),
        ]
    )
    draft = build_contract(plan)

    with pytest.raises(ValueError, match="does not match"):
        confirm_contract(
            draft,
            ContractConfirmRequest(
                version=draft.version,
                items=draft.items[:1],
            ),
            plan,
        )


def test_required_brand_failure_disqualifies_platform():
    planned = PlannedItem(search_term="milk", quantity=1, unit="l")
    product = _product("p1", "Mother Dairy Toned Milk", "1 L", 70)
    proof = prove_platform(
        _contract(
            planned,
            brand="Amul",
            brand_level=RequirementLevel.REQUIRED,
        ),
        _draft("blinkit", planned, product),
        _summary("blinkit", product),
        Settings(_env_file=None),
    )

    assert proof.eligible is False
    assert any(
        check.rule == "brand" and check.status == "fail"
        for check in proof.item_proofs[0].checks
    )


def test_preferred_brand_substitution_is_a_rankable_warning():
    planned = PlannedItem(search_term="milk", quantity=1, unit="l")
    product = _product("p1", "Mother Dairy Toned Milk", "1 L", 70)
    proof = prove_platform(
        _contract(
            planned,
            brand="Amul",
            brand_level=RequirementLevel.PREFERRED,
        ),
        _draft("blinkit", planned, product),
        _summary("blinkit", product),
        Settings(_env_file=None),
    )

    assert proof.eligible is True
    assert proof.status == "qualified"
    assert proof.preference_misses == 1


def test_unparseable_pack_cannot_pass_required_quantity():
    planned = PlannedItem(search_term="milk", quantity=2, unit="l")
    product = _product("p1", "Fresh Milk Family Pack", "", 100)
    proof = prove_platform(
        _contract(planned),
        _draft("blinkit", planned, product),
        _summary("blinkit", product),
        Settings(_env_file=None),
    )

    assert proof.eligible is False
    assert any(
        check.rule == "quantity" and check.status == "unverified"
        for check in proof.item_proofs[0].checks
    )


def test_cheaper_noncompliant_cart_never_wins():
    settings = Settings(_env_file=None)
    planned = PlannedItem(search_term="milk", quantity=1, unit="l")
    contract = _contract(
        planned,
        brand="Amul",
        brand_level=RequirementLevel.REQUIRED,
    )
    wrong = _product("wrong", "Mother Dairy Milk", "1 L", 50)
    right = _product("right", "Amul Taaza Milk", "1 L", 70)
    blinkit = build_outcome(
        "blinkit",
        "Blinkit",
        _draft("blinkit", planned, wrong),
        _summary("blinkit", wrong),
        settings,
        contract=contract,
    )
    instamart = build_outcome(
        "instamart",
        "Instamart",
        _draft("instamart", planned, right),
        _summary("instamart", right),
        settings,
        contract=contract,
    )

    report = rank([blinkit, instamart], settings, contract)

    assert report.winner == "instamart"
    assert report.ranking == ["instamart"]
    assert report.contract_id == contract.id


def test_cart_budget_is_a_required_basket_check():
    planned = PlannedItem(search_term="milk", quantity=1, unit="l")
    product = _product("p1", "Fresh Milk", "1 L", 70)
    proof = prove_platform(
        _contract(planned, budget=60),
        _draft("blinkit", planned, product),
        _summary("blinkit", product),
        Settings(_env_file=None),
    )

    assert proof.eligible is False
    assert any(
        check.rule == "cart_budget" and check.status == "fail"
        for check in proof.basket_checks
    )
