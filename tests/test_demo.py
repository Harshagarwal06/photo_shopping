import asyncio

from app.config import Settings
from app.demo import demo_search
from app.providers.demo import DemoProvider
from app.providers.factory import create_providers


def test_demo_catalog_returns_realistic_known_products():
    products = demo_search("cheapest coffee")

    assert products
    assert all(product.in_stock for product in products)
    assert any("coffee" in product.name.casefold() for product in products)


def test_demo_catalog_returns_a_deterministic_relevant_synthetic_fallback():
    first = demo_search("toothbrush")
    second = demo_search("toothbrush")

    assert first == second
    assert len(first) == 1
    assert first[0].in_stock is True
    assert "toothbrush" in first[0].name.casefold()
    assert "demo product" in first[0].name.casefold()


def test_demo_mode_isolates_every_provider_from_live_services():
    providers = create_providers(
        Settings(_env_file=None, demo_mode=True, grocery_provider="blinkit")
    )

    assert set(providers) == {"blinkit", "instamart", "zepto"}
    assert all(isinstance(provider, DemoProvider) for provider in providers.values())
    async def load_statuses():
        return await asyncio.gather(
            *(provider.status(refresh=True) for provider in providers.values())
        )

    statuses = asyncio.run(load_statuses())
    assert all(status.connected for status in statuses)


def test_every_demo_provider_can_search_without_an_account():
    async def exercise() -> None:
        providers = create_providers(Settings(_env_file=None, demo_mode=True))
        results = await asyncio.gather(
            *(provider.search("coffee") for provider in providers.values())
        )
        assert all(products for products in results)

    asyncio.run(exercise())
