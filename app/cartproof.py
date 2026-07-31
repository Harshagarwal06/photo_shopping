"""Deterministic evidence checks for a confirmed shopping contract."""

from __future__ import annotations

import re
from typing import Literal

from .config import Settings
from .matcher import match_is_reasonable
from .models import (
    CartSummary,
    DraftCart,
    ItemContract,
    ItemProof,
    PlatformProof,
    RequirementCheck,
    RequirementLevel,
    ShoppingContract,
    SubstitutionPolicy,
)
from .units import fill_ratio

CheckStatus = Literal["pass", "warning", "fail", "unverified"]


def _phrase_present(text: str, phrase: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(phrase.casefold())}(?![a-z0-9])",
            text.casefold(),
        )
    )


def _level_status(
    level: RequirementLevel,
    *,
    unknown: bool = False,
) -> CheckStatus:
    if level == RequirementLevel.REQUIRED:
        return "unverified" if unknown else "fail"
    if level == RequirementLevel.PREFERRED:
        return "warning"
    return "pass"


def _item_proof(item, requirement: ItemContract) -> ItemProof:
    product = item.selected_product
    checks: list[RequirementCheck] = []
    if product is None or item.units_to_add < 1:
        checks.append(
            RequirementCheck(
                rule="availability",
                requested=requirement.product_name,
                supplied="No product selected",
                status="fail",
                explanation="A required grocery line is missing from this cart.",
            )
        )
        return ItemProof(
            planned_item_id=requirement.planned_item_id,
            requested_item=requirement.product_name,
            checks=checks,
            status="fail",
        )

    checks.append(
        RequirementCheck(
            rule="availability",
            requested=requirement.product_name,
            supplied=product.name,
            status="pass",
            explanation="An in-stock catalogue product was selected.",
        )
    )

    reasonable, relevance_reason = match_is_reasonable(item.planned, product)
    checks.append(
        RequirementCheck(
            rule="relevance",
            requested=requirement.product_name,
            supplied=product.name,
            status="pass" if reasonable else "fail",
            explanation=(
                "The selected product passes the deterministic relevance gate."
                if reasonable
                else relevance_reason or "The selected product does not match the request."
            ),
        )
    )

    if requirement.brand:
        brand_matches = _phrase_present(product.name, requirement.brand)
        brand_is_hard = (
            requirement.brand_level == RequirementLevel.REQUIRED
            or requirement.substitution_policy
            in {SubstitutionPolicy.NONE, SubstitutionPolicy.SAME_BRAND}
        )
        status: CheckStatus = (
            "pass"
            if brand_matches
            else "fail"
            if brand_is_hard
            else _level_status(requirement.brand_level)
        )
        checks.append(
            RequirementCheck(
                rule="brand",
                requested=(
                    f"{requirement.brand} ({requirement.brand_level.value})"
                ),
                supplied=product.name,
                status=status,
                explanation=(
                    f"The selected product contains the requested {requirement.brand} brand."
                    if brand_matches
                    else (
                        f"The selected product does not contain the required "
                        f"{requirement.brand} brand."
                        if brand_is_hard
                        else f"The preferred {requirement.brand} brand was substituted."
                    )
                ),
            )
        )

    ratio = fill_ratio(item.planned, product, item.units_to_add)
    supplied_quantity = f"{item.units_to_add} × {product.pack_size or product.name}"
    requested_quantity = f"{requirement.quantity:g} {requirement.unit}"
    if ratio is None:
        quantity_status: CheckStatus = _level_status(
            requirement.quantity_level,
            unknown=True,
        )
        quantity_explanation = (
            "The catalogue pack size could not be parsed, so the supplied quantity "
            "cannot be proved."
        )
    elif requirement.min_fill_ratio <= ratio <= requirement.max_fill_ratio:
        quantity_status = "pass"
        quantity_explanation = (
            f"The selected packs supply {ratio:.0%} of the requested amount, inside "
            f"the allowed {requirement.min_fill_ratio:.0%}–"
            f"{requirement.max_fill_ratio:.0%} range."
        )
    else:
        quantity_status = _level_status(requirement.quantity_level)
        quantity_explanation = (
            f"The selected packs supply {ratio:.0%} of the requested amount, outside "
            f"the allowed {requirement.min_fill_ratio:.0%}–"
            f"{requirement.max_fill_ratio:.0%} range."
        )
    checks.append(
        RequirementCheck(
            rule="quantity",
            requested=requested_quantity,
            supplied=supplied_quantity,
            status=quantity_status,
            explanation=quantity_explanation,
        )
    )

    if requirement.item_price_cap is not None:
        line_total = round(product.price * item.units_to_add, 2)
        under_cap = line_total <= requirement.item_price_cap
        checks.append(
            RequirementCheck(
                rule="item_price_cap",
                requested=f"At most ₹{requirement.item_price_cap:.2f}",
                supplied=f"₹{line_total:.2f}",
                status="pass" if under_cap else "fail",
                explanation=(
                    "The selected line stays within its price cap."
                    if under_cap
                    else "The selected line exceeds its required price cap."
                ),
            )
        )

    item_status = (
        "fail"
        if any(check.status in {"fail", "unverified"} for check in checks)
        else "warning"
        if any(check.status == "warning" for check in checks)
        else "pass"
    )
    return ItemProof(
        planned_item_id=requirement.planned_item_id,
        requested_item=requirement.product_name,
        product_id=product.id,
        product_name=product.name,
        checks=checks,
        status=item_status,
    )


def prove_platform(
    contract: ShoppingContract,
    draft: DraftCart,
    summary: CartSummary | None,
    settings: Settings,
) -> PlatformProof:
    """Prove one platform against the exact confirmed contract."""
    del settings  # The contract freezes its own quantity tolerances.
    draft_items = {item.planned.id: item for item in draft.items if not item.removed}
    item_proofs = [
        _item_proof(draft_items.get(requirement.planned_item_id), requirement)
        if requirement.planned_item_id in draft_items
        else ItemProof(
            planned_item_id=requirement.planned_item_id,
            requested_item=requirement.product_name,
            checks=[
                RequirementCheck(
                    rule="availability",
                    requested=requirement.product_name,
                    supplied="No product selected",
                    status="fail",
                    explanation="The contract item is absent from this platform draft.",
                )
            ],
            status="fail",
        )
        for requirement in contract.items
    ]

    basket_checks: list[RequirementCheck] = []
    if summary is None:
        basket_checks.append(
            RequirementCheck(
                rule="cart_total",
                requested="A complete, internally consistent cart total",
                supplied="Cart total unavailable",
                status="fail",
                explanation="The platform did not return a cart total.",
            )
        )
    elif not summary.reconciles:
        basket_checks.append(
            RequirementCheck(
                rule="cart_total",
                requested="A reconciled cart total",
                supplied=f"₹{summary.total:.2f}",
                status="fail",
                explanation=summary.reconciliation_error or "The total did not reconcile.",
            )
        )
    else:
        basket_checks.append(
            RequirementCheck(
                rule="cart_total",
                requested="Subtotal plus every disclosed fee",
                supplied=f"₹{summary.total:.2f}",
                status="pass",
                explanation="The displayed subtotal and fee lines reconcile to the total.",
            )
        )

    if contract.cart_budget is not None and summary is not None:
        within_budget = summary.total <= contract.cart_budget
        basket_checks.append(
            RequirementCheck(
                rule="cart_budget",
                requested=f"At most ₹{contract.cart_budget:.2f}",
                supplied=f"₹{summary.total:.2f}",
                status="pass" if within_budget else "fail",
                explanation=(
                    "The cart is within the confirmed basket budget."
                    if within_budget
                    else "The cart exceeds the confirmed basket budget."
                ),
            )
        )

    required_failures = sum(
        check.status in {"fail", "unverified"}
        for proof in item_proofs
        for check in proof.checks
    ) + sum(
        check.status in {"fail", "unverified"} for check in basket_checks
    )
    preference_misses = sum(
        check.status == "warning"
        for proof in item_proofs
        for check in proof.checks
    )
    eligible = required_failures == 0
    status = (
        "non_compliant"
        if not eligible
        else "qualified"
        if preference_misses
        else "compliant"
    )
    return PlatformProof(
        provider=draft.provider_id,
        status=status,
        eligible=eligible,
        required_failures=required_failures,
        preference_misses=preference_misses,
        item_proofs=item_proofs,
        basket_checks=basket_checks,
    )
