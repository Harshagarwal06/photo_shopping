
from app.providers.base import CartLine, CartSummary, FeeLine


def _line(total: float = 100.0) -> CartLine:
    return CartLine(
        product_id="p1", name="Amul Taaza 1 L", quantity=1,
        unit_price=total, line_total=total,
    )


def test_summary_reconciles_when_fees_add_up():
    summary = CartSummary(
        provider="blinkit", lines=[_line(100.0)], subtotal=100.0,
        fees=[FeeLine(label="Delivery fee", amount=25.0)], total=125.0,
    )
    assert summary.reconciles is True
    assert summary.reconciliation_error is None


def test_summary_tolerates_sub_paisa_float_drift():
    summary = CartSummary(
        provider="blinkit", lines=[_line(0.1)], subtotal=0.1,
        fees=[FeeLine(label="Handling", amount=0.2)], total=0.30000000000000004,
    )
    assert summary.reconciles is True


def test_missing_fee_line_fails_reconciliation():
    """A scraped fee we failed to read must surface as an error, never as a cheap cart."""
    summary = CartSummary(
        provider="zepto", lines=[_line(100.0)], subtotal=100.0,
        fees=[], total=132.0,
    )
    assert summary.reconciles is False
    assert "132" in summary.reconciliation_error


def test_discounts_are_negative_fee_lines():
    summary = CartSummary(
        provider="instamart", lines=[_line(500.0)], subtotal=500.0,
        fees=[FeeLine(label="Delivery fee", amount=30.0),
              FeeLine(label="Coupon SAVE50", amount=-50.0)],
        total=480.0,
    )
    assert summary.reconciles is True
    assert summary.total < summary.subtotal


def test_estimated_summary_skips_reconciliation():
    """Estimated totals are computed by us, so they reconcile by construction."""
    summary = CartSummary(
        provider="zepto", lines=[_line(100.0)], subtotal=100.0,
        fees=[FeeLine(label="Estimated delivery", amount=25.0)],
        total=125.0, estimated=True,
    )
    assert summary.estimated is True
    assert summary.reconciles is True
