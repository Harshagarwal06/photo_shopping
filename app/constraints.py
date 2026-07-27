from __future__ import annotations

import math
import re

from .models import CartConstraints, DraftCart, DraftItem, PlannedItem, Product


MEASURE_UNIT_PATTERN = (
    r"kg|g|gm|grams?|l|ltr|litres?|liters?|ml|pcs?|pieces?|count|eggs?|units?"
)
MEASURE_RE = re.compile(
    rf"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{MEASURE_UNIT_PATTERN})\b",
    re.IGNORECASE,
)
MULTIPACK_MEASURE_RE = re.compile(
    rf"(?P<count>\d+(?:\.\d+)?)\s*x\s*(?P<amount>\d+(?:\.\d+)?)\s*"
    rf"(?P<unit>{MEASURE_UNIT_PATTERN})\b",
    re.IGNORECASE,
)
REVERSED_MULTIPACK_MEASURE_RE = re.compile(
    rf"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{MEASURE_UNIT_PATTERN})\s*"
    r"x\s*(?P<count>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def _normalise_measurement(amount: float, unit: str) -> tuple[float, str]:
    unit = unit.lower()
    if unit == "kg":
        return amount * 1000, "g"
    if unit in {"g", "gm", "gram", "grams"}:
        return amount, "g"
    if unit in {"l", "ltr", "litre", "litres", "liter", "liters"}:
        return amount * 1000, "ml"
    if unit == "ml":
        return amount, "ml"
    return amount, "count"


def parse_measurement(text: str) -> tuple[float, str] | None:
    normalized = text.replace("×", "x")
    multipack = (
        MULTIPACK_MEASURE_RE.search(normalized)
        or REVERSED_MULTIPACK_MEASURE_RE.search(normalized)
    )
    if multipack:
        return _normalise_measurement(
            float(multipack.group("count")) * float(multipack.group("amount")),
            multipack.group("unit"),
        )

    matches = list(MEASURE_RE.finditer(normalized))
    if not matches:
        return None
    converted = [
        _normalise_measurement(float(match.group("amount")), match.group("unit"))
        for match in matches
    ]
    dimensions = {dimension for _, dimension in converted}
    if len(converted) > 1 and len(dimensions) == 1 and "+" in normalized:
        return sum(amount for amount, _ in converted), converted[0][1]
    return converted[0]


def requested_measurement(item: PlannedItem) -> tuple[float, str] | None:
    unit = item.unit.lower()
    if unit == "kg":
        return item.quantity * 1000, "g"
    if unit in {"g", "gm", "gram", "grams"}:
        return item.quantity, "g"
    if unit in {"l", "ltr", "litre", "litres", "liter", "liters"}:
        return item.quantity * 1000, "ml"
    if unit == "ml":
        return item.quantity, "ml"
    if unit in {"count", "pc", "pcs", "piece", "pieces", "egg", "eggs", "item"}:
        return item.quantity, "count"
    return None


def units_for_candidate(item: PlannedItem, product: Product) -> int:
    requested = requested_measurement(item)
    packed = parse_measurement(product.pack_size or product.name)
    if requested and packed and requested[1] == packed[1] and packed[0] > 0:
        return max(1, math.ceil(requested[0] / packed[0]))
    if item.unit.lower() == "pack":
        return max(1, math.ceil(item.quantity))
    return 1


def _cap_for(item: PlannedItem, constraints: CartConstraints) -> float | None:
    haystacks = {item.search_term.casefold(), item.raw_text.casefold()}
    for key, cap in constraints.item_caps.items():
        needle = key.casefold().strip()
        if any(needle in value or value in needle for value in haystacks):
            return cap
    return None


def _quantity_flags(draft_item: DraftItem) -> list[str]:
    product = draft_item.selected_product
    requested = requested_measurement(draft_item.planned)
    packed = parse_measurement((product.pack_size or product.name) if product else "")
    if not product or not requested or not packed or requested[1] != packed[1]:
        return []
    delivered = packed[0] * draft_item.units_to_add
    ratio = delivered / requested[0]
    if ratio < 0.9:
        return [f"Selected quantity supplies only {ratio:.0%} of the requested amount."]
    if ratio > 1.75:
        return [f"Selected packs supply {ratio:.1f}× the requested amount; review the quantity."]
    return []


def enforce_constraints(
    items: list[DraftItem],
    constraints: CartConstraints,
    *,
    dry_run: bool,
    draft_id: str | None = None,
    provider_id: str = "",
    provider_name: str = "",
) -> DraftCart:
    for item in items:
        if item.selected_product_id is None:
            if "No matching product found." not in item.flags:
                item.flags.append("No matching product found. Edit the query and search again.")
            continue
        if item.units_to_add > 50:
            item.units_to_add = 50
            item.flags.append("Quantity was capped at 50 packs; review the requested amount.")

        cap = _cap_for(item.planned, constraints)
        selected = item.selected_product
        if cap is not None and selected and selected.price * item.units_to_add > cap:
            alternatives = []
            # Imported lazily because matcher imports this module for quantity
            # arithmetic. The same fail-closed relevance gate must also apply to
            # cap-driven replacements.
            from .matcher import match_is_reasonable

            for candidate in item.candidates:
                if not candidate.in_stock:
                    continue
                if not match_is_reasonable(item.planned, candidate)[0]:
                    continue
                units = units_for_candidate(item.planned, candidate)
                if candidate.price * units <= cap:
                    alternatives.append((candidate.price * units, candidate, units))
            if alternatives:
                _, replacement, replacement_units = min(alternatives, key=lambda entry: entry[0])
                item.selected_product_id = replacement.id
                item.units_to_add = replacement_units
                item.reason = f"Swapped to stay under the ₹{cap:.0f} item cap."
                item.flags.append("Original match exceeded the item cap; a cheaper option is selected.")
            else:
                item.flags.append(f"No candidate fits the ₹{cap:.0f} item cap.")
        item.flags.extend(flag for flag in _quantity_flags(item) if flag not in item.flags)

    total = round(
        sum(
            (item.selected_product.price * item.units_to_add)
            for item in items
            if not item.removed and item.selected_product is not None
        ),
        2,
    )
    flags: list[str] = []
    if constraints.cart_budget is not None and total > constraints.cart_budget:
        flags.append(
            f"Draft total is ₹{total:.2f}, which is ₹{total - constraints.cart_budget:.2f} over budget."
        )
    payload = {
        "provider_id": provider_id,
        "provider_name": provider_name,
        "items": items,
        "cart_budget": constraints.cart_budget,
        "total": total,
        "flags": flags,
        "dry_run": dry_run,
    }
    if draft_id:
        payload["id"] = draft_id
    return DraftCart.model_validate(payload)
