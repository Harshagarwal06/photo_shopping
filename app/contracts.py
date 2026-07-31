"""Build, confirm, and bind user-visible CartProof shopping contracts."""

from __future__ import annotations

import hashlib
import json
import re

from .models import (
    PROVIDER_QUERY_BRANDS,
    CartPlan,
    ContractConfirmRequest,
    ItemContract,
    RequirementLevel,
    ShoppingContract,
    SubstitutionPolicy,
)

_HARD_RULE_RE = re.compile(
    r"\b(must|only|exact|strictly|do not substitute|no substitutions?|same brand)\b",
    re.IGNORECASE,
)


def _brand_for(item) -> str | None:
    haystack = f"{item.context} {item.raw_text}".strip()
    matches = [
        brand
        for brand in PROVIDER_QUERY_BRANDS
        if re.search(
            rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])",
            haystack,
            flags=re.IGNORECASE,
        )
    ]
    return max(matches, key=len) if matches else None


def _item_cap(plan: CartPlan, item) -> float | None:
    haystacks = {item.search_term.casefold(), item.raw_text.casefold()}
    for key, cap in plan.constraints.item_caps.items():
        needle = key.casefold().strip()
        if any(needle in value or value in needle for value in haystacks):
            return cap
    return None


def build_contract(plan: CartPlan) -> ShoppingContract:
    """Create a conservative draft. Only the user can confirm hard requirements."""
    contract_items: list[ItemContract] = []
    for item in plan.items:
        brand = _brand_for(item)
        hard_brand = bool(brand and _HARD_RULE_RE.search(item.raw_text))
        quantity_is_explicit = (
            item.unit.casefold() != "item"
            or item.quantity != 1
        )
        contract_items.append(
            ItemContract(
                planned_item_id=item.id,
                product_name=item.search_term,
                quantity=item.quantity,
                unit=item.unit,
                quantity_level=(
                    RequirementLevel.REQUIRED
                    if quantity_is_explicit
                    else RequirementLevel.FLEXIBLE
                ),
                brand=brand,
                brand_level=(
                    RequirementLevel.REQUIRED
                    if hard_brand
                    else RequirementLevel.PREFERRED
                    if brand
                    else RequirementLevel.FLEXIBLE
                ),
                substitution_policy=(
                    SubstitutionPolicy.SAME_BRAND
                    if hard_brand
                    else SubstitutionPolicy.EQUIVALENT
                    if brand
                    else SubstitutionPolicy.ANY
                ),
                item_price_cap=_item_cap(plan, item),
            )
        )
    return ShoppingContract(
        items=contract_items,
        cart_budget=plan.constraints.cart_budget,
    )


def contract_fingerprint(contract: ShoppingContract) -> str:
    payload = {
        "id": contract.id,
        "version": contract.version,
        "items": [
            item.model_dump(mode="json")
            for item in sorted(contract.items, key=lambda entry: entry.planned_item_id)
        ],
        "cart_budget": contract.cart_budget,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_contract_plan(contract: ShoppingContract, plan: CartPlan) -> None:
    plan_ids = [item.id for item in plan.items]
    contract_ids = [item.planned_item_id for item in contract.items]
    if len(contract_ids) != len(set(contract_ids)):
        raise ValueError("The shopping contract contains a duplicate grocery item.")
    if set(contract_ids) != set(plan_ids):
        raise ValueError("The shopping contract does not match the reviewed grocery list.")


def confirm_contract(
    current: ShoppingContract,
    request: ContractConfirmRequest,
    plan: CartPlan,
) -> ShoppingContract:
    if request.version != current.version:
        raise ValueError("The shopping contract changed. Review the latest version.")
    updated = ShoppingContract(
        id=current.id,
        version=current.version + (1 if current.status == "confirmed" else 0),
        items=request.items,
        cart_budget=request.cart_budget,
        status="confirmed",
    )
    validate_contract_plan(updated, plan)
    updated.fingerprint = contract_fingerprint(updated)
    return updated


def auto_confirm_contract(plan: CartPlan) -> ShoppingContract:
    """Compatibility path for internal callers that do not yet provide a contract."""
    draft = build_contract(plan)
    return confirm_contract(
        draft,
        ContractConfirmRequest(
            version=draft.version,
            items=draft.items,
            cart_budget=draft.cart_budget,
        ),
        plan,
    )


def plan_for_contract(plan: CartPlan, contract: ShoppingContract) -> CartPlan:
    """Apply the confirmed contract values to the retrieval plan."""
    validate_contract_plan(contract, plan)
    requirements = {item.planned_item_id: item for item in contract.items}
    updated_items = []
    for item in plan.items:
        requirement = requirements[item.id]
        context = item.context
        if requirement.brand:
            context = re.sub(
                rf"(?<![a-z0-9]){re.escape(requirement.brand)}(?![a-z0-9])",
                "",
                context,
                flags=re.IGNORECASE,
            )
            if (
                requirement.brand_level == RequirementLevel.REQUIRED
                or requirement.substitution_policy
                in {SubstitutionPolicy.NONE, SubstitutionPolicy.SAME_BRAND}
            ):
                context = f"{requirement.brand} {context}".strip()
        context = re.sub(r"\s+", " ", context).strip()
        updated_items.append(
            item.model_copy(
                update={
                    "search_term": requirement.product_name,
                    "context": context,
                    "quantity": requirement.quantity,
                    "unit": requirement.unit,
                }
            )
        )
    return plan.model_copy(update={"items": updated_items})
