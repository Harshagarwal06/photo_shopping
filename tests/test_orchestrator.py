import asyncio

from app.config import Settings
from app.models import CartConstraints, CartPlan, PlannedItem, Product
from app.orchestrator import run_comparison
from app.providers.base import CartLine, CartSummary, FeeLine, ProviderError


class FakeProvider:
    def __init__(self, provider_id, price, *, fail_on=None, eta=10):
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self.price = price
        self.fail_on = fail_on
        self.eta = eta
        self.cleared = False
        self.added = []

    async def search(self, query):
        if self.fail_on == "search":
            raise ProviderError(f"{self.provider_id} search failed")
        return [Product(id=f"{self.provider_id}-1", name="Amul Taaza",
                        pack_size="1 L", price=self.price, handle="h")]

    async def add_items(self, selections, *, operation_id):
        self.added = list(selections)
        return []

    async def cart_summary(self):
        if self.fail_on == "cart":
            raise ProviderError(f"{self.provider_id} cart failed")
        subtotal = sum(p.price * q for p, q in self.added) or self.price
        return CartSummary(
            provider=self.provider_id,
            lines=[CartLine(product_id="x", name="Amul Taaza", quantity=1,
                            unit_price=subtotal, line_total=subtotal)],
            subtotal=subtotal,
            fees=[FeeLine(label="Delivery fee", amount=25.0)],
            total=subtotal + 25.0,
            delivery_eta_minutes=self.eta,
        )

    async def clear_cart(self, *, operation_id):
        self.cleared = True

    async def close(self):
        return None


def _plan():
    return CartPlan(items=[PlannedItem(search_term="milk", quantity=1, unit="l")],
                    constraints=CartConstraints())


def _settings():
    return Settings(_env_file=None, safety_lock=True)


def test_cheaper_platform_wins_across_providers():
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0)}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner == "zepto"


def test_one_provider_failing_does_not_sink_the_run():
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0, fail_on="search")}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner == "blinkit"
    zepto = next(p for p in report.platforms if p.provider == "zepto")
    assert zepto.status == "failed"
    assert "search failed" in zepto.error


def test_cart_read_failure_is_reported_as_failed():
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0, fail_on="cart")}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner == "blinkit"


def test_all_providers_failing_yields_no_winner():
    providers = {"blinkit": FakeProvider("blinkit", 75.0, fail_on="search"),
                 "zepto": FakeProvider("zepto", 68.0, fail_on="search")}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner is None


def test_events_are_emitted_per_platform():
    events = []
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0)}
    asyncio.run(run_comparison(_plan(), providers, _settings(),
                               on_event=events.append))
    assert {e.data.get("provider") for e in events if e.data} >= {"blinkit", "zepto"}


def test_dry_run_produces_estimated_totals_without_writing():
    """With writes disabled, nothing is added and totals are labelled estimated."""
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0)}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.estimated is True
    assert providers["blinkit"].added == []
