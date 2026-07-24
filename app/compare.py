"""Deterministic comparison of the same basket across platforms.

No LLM. Arithmetic is the one thing that must be exactly right, and every
number and reason here is traceable to a rule.
"""

from __future__ import annotations

from .config import Settings
from .models import (
    CartLine,
    CartSummary,
    ComparisonReport,
    DraftCart,
    FeeLine,
    PlatformOutcome,
    Substitution,
)
from .units import fill_ratio, per_unit_price


# Fees used only when cart writes are disabled and we cannot read a real cart.
FEE_ESTIMATES: dict[str, list[FeeLine]] = {
    "blinkit": [FeeLine(label="Estimated delivery fee", amount=25.0),
                FeeLine(label="Estimated handling fee", amount=5.0)],
    "zepto": [FeeLine(label="Estimated delivery fee", amount=29.0),
              FeeLine(label="Estimated handling fee", amount=5.0)],
    "instamart": [FeeLine(label="Estimated delivery fee", amount=30.0),
                  FeeLine(label="Estimated handling fee", amount=5.0)],
}


def estimated_summary(provider_id: str, draft: DraftCart) -> CartSummary:
    """Build a clearly-labelled estimated summary when carts cannot be written."""
    lines = [
        CartLine(
            product_id=product.id,
            name=product.name,
            quantity=item.units_to_add,
            unit_price=product.price,
            line_total=round(product.price * item.units_to_add, 2),
        )
        for item in draft.items
        if not item.removed and (product := item.selected_product) is not None
    ]
    subtotal = round(sum(line.line_total for line in lines), 2)
    fees = FEE_ESTIMATES.get(provider_id, [])
    return CartSummary(
        provider=provider_id,
        lines=lines,
        subtotal=subtotal,
        fees=list(fees),
        total=round(subtotal + sum(fee.amount for fee in fees), 2),
        estimated=True,
        raw_note="Fees are estimates because cart writes are disabled.",
    )


def build_outcome(
    provider_id: str,
    display_name: str,
    draft: DraftCart,
    summary: CartSummary | None,
    settings: Settings,
    *,
    status: str = "ok",
    error: str = "",
) -> PlatformOutcome:
    """Fold a platform's draft and cart summary into a comparable outcome."""
    if status != "ok":
        return PlatformOutcome(
            provider=provider_id, display_name=display_name,
            status=status, error=error,
        )

    matched = 0
    partial: list[str] = []
    missing: list[str] = []
    unverified: list[str] = []
    substitutions: list[Substitution] = []

    for item in draft.items:
        if item.removed:
            continue
        label = item.planned.raw_text or item.planned.search_term
        product = item.selected_product
        if product is None or item.units_to_add < 1:
            missing.append(label)
            continue
        ratio = fill_ratio(item.planned, product, item.units_to_add)
        if ratio is None:
            # Pack size unparseable or units incomparable: never guess. The
            # item is not demoted (that would punish a parsing gap, not a
            # real shortfall) but it is disclosed so the user knows the
            # quantity was never actually checked.
            unverified.append(label)
            matched += 1
        elif ratio < settings.min_fill_ratio:
            partial.append(label)
            unit_price = per_unit_price(product, item.units_to_add)
            substitutions.append(
                Substitution(
                    item=label,
                    requested=f"{item.planned.quantity:g} {item.planned.unit}",
                    supplied=f"{item.units_to_add} × {product.pack_size or product.name}",
                    reason=f"Supplies only {ratio:.0%} of the requested amount.",
                    per_unit_delta=unit_price[0] if unit_price else None,
                )
            )
        else:
            matched += 1

    return PlatformOutcome(
        provider=provider_id,
        display_name=display_name,
        status="ok",
        summary=summary,
        matched_items=matched,
        partial_items=partial,
        missing_items=missing,
        unverified_items=unverified,
        substitutions=substitutions,
    )


def _rankable(outcome: PlatformOutcome) -> bool:
    """Only platforms with a trustworthy, reconciled cart may be ranked."""
    return (
        outcome.status == "ok"
        and outcome.summary is not None
        and outcome.summary.reconciles
    )


def rank(outcomes: list[PlatformOutcome], settings: Settings) -> ComparisonReport:
    """Rank platforms lexicographically: coverage tier, then real total, then ETA."""
    reasons: list[str] = []

    for outcome in outcomes:
        if outcome.status != "ok" and outcome.error:
            reasons.append(f"{outcome.display_name}: {outcome.error}")
        elif outcome.summary is not None and not outcome.summary.reconciles:
            reasons.append(
                f"{outcome.display_name} was excluded — "
                f"{outcome.summary.reconciliation_error}"
            )

    is_estimated = any(
        outcome.summary.estimated for outcome in outcomes if outcome.summary
    )

    rankable = [outcome for outcome in outcomes if _rankable(outcome)]
    if not rankable:
        if not reasons:
            reasons.append("No platform produced a comparable cart.")
        return ComparisonReport(platforms=outcomes, winner=None, ranking=[],
                                reasons=reasons, estimated=is_estimated)

    ordered = sorted(
        rankable,
        key=lambda outcome: (outcome.coverage_tier, round(outcome.summary.total, 2)),
    )

    # ETA tiebreak: within the price band, prefer the faster platform.
    best = ordered[0]
    band = [
        outcome
        for outcome in ordered
        if outcome.coverage_tier == best.coverage_tier
        and round(outcome.summary.total, 2) - round(best.summary.total, 2)
        <= settings.eta_tiebreak_rupees
    ]
    if len(band) > 1:
        fastest = min(
            band,
            key=lambda outcome: (
                outcome.summary.delivery_eta_minutes
                if outcome.summary.delivery_eta_minutes is not None
                else 10**6
            ),
        )
        if fastest is not best:
            ordered.remove(fastest)
            ordered.insert(0, fastest)
            reasons.append(
                f"{fastest.display_name} wins the tiebreak: within "
                f"₹{settings.eta_tiebreak_rupees:.0f} of the cheapest and arrives sooner."
            )
        best = ordered[0]

    if len(ordered) > 1:
        runner_up = ordered[1]
        gap = round(runner_up.summary.total - best.summary.total, 2)
        if gap > 0:
            reasons.append(
                f"{best.display_name} is ₹{gap:g} cheaper than {runner_up.display_name}."
            )

    for outcome in ordered:
        if outcome.missing_items:
            reasons.append(
                f"{outcome.display_name} is missing: {', '.join(outcome.missing_items)}."
            )
        if outcome.partial_items:
            reasons.append(
                f"{outcome.display_name} supplies short packs for: "
                f"{', '.join(outcome.partial_items)}."
            )
        if outcome.unverified_items:
            reasons.append(
                f"{outcome.display_name} could not verify the quantity for: "
                f"{', '.join(outcome.unverified_items)}."
            )

    return ComparisonReport(
        platforms=outcomes,
        winner=best.provider,
        ranking=[outcome.provider for outcome in ordered],
        reasons=reasons,
        estimated=is_estimated,
    )
