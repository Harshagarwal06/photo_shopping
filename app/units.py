"""Per-unit price comparison built on the measurement parsing in constraints.py.

Only the price-per-unit layer lives here; all measurement parsing is reused
from `app.constraints` so the comparison and the single-platform draft agree
on what a pack size means.
"""

from __future__ import annotations

from .constraints import parse_measurement, requested_measurement
from .models import PlannedItem, Product


def _packed(product: Product) -> tuple[float, str] | None:
    return parse_measurement(product.pack_size or product.name)


def per_unit_price(product: Product, units: int) -> tuple[float, str] | None:
    """Price per gram / millilitre / count, or None when not comparable.

    Returns (price_per_unit, dimension). Never guesses: an unparseable pack
    size yields None so the caller can mark the item not price-comparable.
    """
    if units <= 0:
        return None
    packed = _packed(product)
    if not packed:
        return None
    amount, dimension = packed
    total_amount = amount * units
    if total_amount <= 0:
        return None
    return round(product.price * units / total_amount, 6), dimension


def fill_ratio(item: PlannedItem, product: Product, units: int) -> float | None:
    """Delivered quantity divided by requested quantity, or None if incomparable."""
    if units <= 0:
        return None
    requested = requested_measurement(item)
    packed = _packed(product)
    if not requested or not packed:
        return None
    if requested[1] != packed[1] or requested[0] <= 0:
        return None
    return round(packed[0] * units / requested[0], 4)
