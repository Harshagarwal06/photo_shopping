import asyncio

from app.config import Settings
from app.models import AddResult, CartPlan, CartSummary, PlannedItem, Product
from app.providers.base import ConnectResult, GroceryProvider, ProviderStatus
from app.recognition import resolve_plan_autonomously


class CatalogProvider(GroceryProvider):
    def __init__(self, provider_id: str, products: list[Product]):
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self.products = products
        self.queries: list[str] = []

    async def search(self, query: str) -> list[Product]:
        self.queries.append(query)
        return self.products

    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        return ProviderStatus(
            provider=self.provider_id,
            display_name=self.display_name,
            connected=True,
        )

    async def connect(self) -> ConnectResult:
        return ConnectResult(connected=True)

    async def add_items(
        self,
        selections: list[tuple[Product, int]],
        *,
        operation_id: str,
    ) -> list[AddResult]:
        raise AssertionError("Recognition must never mutate a cart.")

    async def cart_summary(self) -> CartSummary:
        return CartSummary(provider=self.provider_id)

    async def close(self) -> None:
        return None


def _product(provider: str, name: str) -> Product:
    return Product(
        id=f"{provider}-{name}",
        name=name,
        pack_size="1 item",
        price=50,
        handle=f"{provider}-{name}",
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        recognition_policy="autonomous_safe",
        autonomous_catalog_min_providers=2,
    )


def test_engine_agreement_and_two_catalogues_accept_line():
    local = PlannedItem(
        id="line",
        search_term="Maggi",
        raw_text="Maggi",
        confidence=0.75,
        needs_review=True,
        quantity=2,
        unit="pack",
    )
    cloud = local.model_copy(deep=True, update={"confidence": 0.65})
    providers = {
        name: CatalogProvider(name, [_product(name, "Maggi 2-Minute Noodles")])
        for name in ("blinkit", "zepto")
    }

    result = asyncio.run(
        resolve_plan_autonomously(
            CartPlan(items=[local]),
            CartPlan(items=[cloud]),
            providers,
            _settings(),
        )
    )

    item = result.items[0]
    assert item.needs_review is False
    assert item.confirmed is True
    assert item.recognition_decision == "accepted"
    assert item.quantity == 2
    assert item.unit == "pack"


def test_unresolved_line_is_skipped_without_forcing_product():
    local = PlannedItem(
        id="line",
        search_term="Ma",
        raw_text="5. Ma",
        confidence=0.2,
        needs_review=True,
    )
    cloud = local.model_copy(deep=True, update={"confidence": 0.65})
    providers = {
        name: CatalogProvider(name, [])
        for name in ("blinkit", "zepto")
    }

    result = asyncio.run(
        resolve_plan_autonomously(
            CartPlan(items=[local]),
            CartPlan(items=[cloud]),
            providers,
            _settings(),
        )
    )

    item = result.items[0]
    assert item.needs_review is True
    assert item.confirmed is False
    assert item.recognition_decision == "skipped"
    assert "safely skipped 1 uncertain line" in result.processing_note


def test_two_provider_brand_consensus_corrects_close_ocr_typo():
    local = PlannedItem(
        id="line",
        search_term="Citbat",
        raw_text="4. Citbat",
        confidence=0.2,
        needs_review=True,
    )
    cloud = local.model_copy(deep=True, update={"confidence": 0.65})
    providers = {
        name: CatalogProvider(name, [_product(name, "KitKat Chocolate Bar")])
        for name in ("blinkit", "zepto")
    }

    result = asyncio.run(
        resolve_plan_autonomously(
            CartPlan(items=[local]),
            CartPlan(items=[cloud]),
            providers,
            _settings(),
        )
    )

    item = result.items[0]
    assert item.search_term == "KitKat"
    assert item.needs_review is False
    assert item.recognition_decision == "catalog_corrected"
