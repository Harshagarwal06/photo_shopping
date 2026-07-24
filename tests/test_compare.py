import pytest

from app.compare import rank
from app.config import Settings
from app.models import ComparisonReport, PlatformOutcome
from app.providers.base import CartLine, CartSummary, FeeLine


def _settings() -> Settings:
    return Settings(_env_file=None)


def _summary(provider: str, total: float, eta: int | None = 10) -> CartSummary:
    return CartSummary(
        provider=provider,
        lines=[CartLine(product_id="p", name="Milk", quantity=1,
                        unit_price=total - 25, line_total=total - 25)],
        subtotal=total - 25,
        fees=[FeeLine(label="Delivery fee", amount=25.0)],
        total=total,
        delivery_eta_minutes=eta,
    )


def _outcome(provider: str, total: float, **kwargs) -> PlatformOutcome:
    return PlatformOutcome(
        provider=provider, display_name=provider.title(),
        summary=_summary(provider, total, kwargs.pop("eta", 10)),
        matched_items=kwargs.pop("matched_items", 3), **kwargs,
    )


def test_cheapest_full_coverage_platform_wins():
    report = rank([_outcome("blinkit", 300.0), _outcome("zepto", 265.0)], _settings())
    assert report.winner == "zepto"
    assert report.ranking == ["zepto", "blinkit"]


def test_full_coverage_beats_a_cheaper_cart_with_missing_items():
    """A cheap cart missing an item is not the winner."""
    report = rank(
        [
            _outcome("blinkit", 300.0),
            _outcome("zepto", 200.0, missing_items=["paneer"]),
        ],
        _settings(),
    )
    assert report.winner == "blinkit"
    assert any("paneer" in reason for reason in report.reasons)


def test_short_packs_demote_below_full_coverage():
    """Cheaper because the packs are smaller is not cheaper."""
    report = rank(
        [
            _outcome("blinkit", 300.0),
            _outcome("zepto", 250.0, partial_items=["milk"]),
        ],
        _settings(),
    )
    assert report.winner == "blinkit"


def test_partial_still_beats_missing():
    report = rank(
        [
            _outcome("blinkit", 400.0, missing_items=["atta"]),
            _outcome("zepto", 450.0, partial_items=["milk"]),
        ],
        _settings(),
    )
    assert report.ranking == ["zepto", "blinkit"]


def test_eta_breaks_ties_within_the_price_band():
    """Within ₹20, the faster platform wins."""
    report = rank(
        [_outcome("blinkit", 300.0, eta=25), _outcome("zepto", 295.0, eta=9)],
        _settings(),
    )
    assert report.winner == "zepto"
    report_reversed = rank(
        [_outcome("blinkit", 295.0, eta=25), _outcome("zepto", 300.0, eta=9)],
        _settings(),
    )
    assert report_reversed.winner == "zepto"


def test_price_gap_beyond_the_band_ignores_eta():
    report = rank(
        [_outcome("blinkit", 200.0, eta=30), _outcome("zepto", 300.0, eta=8)],
        _settings(),
    )
    assert report.winner == "blinkit"


def test_failed_platform_is_never_ranked_and_keeps_its_error():
    report = rank(
        [
            _outcome("blinkit", 300.0),
            PlatformOutcome(provider="zepto", display_name="Zepto", status="failed",
                            error="Zepto search timed out."),
        ],
        _settings(),
    )
    assert report.winner == "blinkit"
    assert "zepto" not in report.ranking
    zepto = next(p for p in report.platforms if p.provider == "zepto")
    assert zepto.error == "Zepto search timed out."
    assert any("Zepto search timed out." in reason for reason in report.reasons)


def test_not_connected_platform_is_reported_not_dropped():
    report = rank(
        [
            _outcome("blinkit", 300.0),
            PlatformOutcome(provider="zepto", display_name="Zepto",
                            status="not_connected", error="Zepto is not connected."),
        ],
        _settings(),
    )
    assert {p.provider for p in report.platforms} == {"blinkit", "zepto"}
    assert report.ranking == ["blinkit"]


def test_all_platforms_failed_yields_no_winner():
    report = rank(
        [
            PlatformOutcome(provider="blinkit", display_name="Blinkit",
                            status="failed", error="boom"),
            PlatformOutcome(provider="zepto", display_name="Zepto",
                            status="failed", error="bang"),
        ],
        _settings(),
    )
    assert report.winner is None
    assert report.ranking == []
    assert report.reasons


def test_empty_input_yields_no_winner():
    report = rank([], _settings())
    assert isinstance(report, ComparisonReport)
    assert report.winner is None


def test_report_is_estimated_when_any_summary_is_estimated():
    estimated = _outcome("zepto", 250.0)
    estimated.summary.estimated = True
    report = rank([_outcome("blinkit", 300.0), estimated], _settings())
    assert report.estimated is True


def test_unreconciled_summary_is_disqualified():
    """A cart whose fees do not add up must not win on a wrong number."""
    broken = _outcome("zepto", 250.0)
    broken.summary.total = 999.0  # lines + fees no longer add up
    report = rank([_outcome("blinkit", 300.0), broken], _settings())
    assert report.winner == "blinkit"
    assert "zepto" not in report.ranking


def test_reasons_explain_the_price_gap():
    report = rank([_outcome("blinkit", 300.0), _outcome("zepto", 265.0)], _settings())
    assert any("₹35" in reason for reason in report.reasons)
