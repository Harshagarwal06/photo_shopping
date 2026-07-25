import asyncio

from app.config import Settings
from app.models import AddResult, CartSummary, Product
from app.providers.zepto import ZeptoProvider


class FakeZeptoClient:
    def __init__(self):
        self.connected = True
        self.removed = []

    async def ensure_login(self, *, wait_for_user=False):
        return self.connected

    async def search(self, query):
        return [Product(id="z1", name="Milk", price=50, handle="z1")]

    async def add_to_cart(self, product, quantity):
        return AddResult(
            product_id=product.id,
            product_name=product.name,
            requested_units=quantity,
            success=True,
            message="added",
        )

    async def cart_summary(self):
        return CartSummary(provider="zepto")

    async def remove_from_cart(self, product, quantity):
        self.removed.append((product.id, quantity))

    async def close(self):
        return None


def test_zepto_provider_connects_searches_and_cleans_only_its_operation():
    settings = Settings(
        _env_file=None,
        safety_lock=False,
        dry_run=False,
        zepto_cart_writes=True,
    )
    client = FakeZeptoClient()
    provider = ZeptoProvider(settings, client=client)
    product = Product(id="z1", name="Milk", price=50, handle="z1")

    status = asyncio.run(provider.status(refresh=True))
    results = asyncio.run(provider.search("milk"))
    asyncio.run(provider.add_items([(product, 2)], operation_id="run-1"))
    asyncio.run(provider.clear_cart(operation_id="run-1"))

    assert status.connected is True
    assert results[0].id == "z1"
    assert client.removed == [("z1", 2)]
