import asyncio
import time

from app.config import Settings
from app.models import Product
from app.providers.instamart import InstamartProvider
from app.providers.instamart_oauth import SwiggyOAuthClient
from app.providers.token_store import MemoryTokenStore, SwiggyTokenRecord


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.cart = {"spin-existing": 2}

    async def call_tool(self, name, arguments=None):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "get_addresses":
            return {"addresses": [{"addressId": "addr-home", "label": "Home"}]}
        if name == "get_cart":
            return {
                "cart": {
                    "items": [
                        {"spinId": spin_id, "quantity": quantity}
                        for spin_id, quantity in self.cart.items()
                    ]
                }
            }
        if name == "update_cart":
            self.cart = {
                item["spinId"]: item["quantity"]
                for item in arguments["items"]
            }
            return {"updated": True}
        if name == "your_go_to_items":
            return {"products": []}
        if name == "search_products":
            return {"products": []}
        raise AssertionError(f"Unexpected tool: {name}")


def test_instamart_cart_update_reads_merges_replaces_and_verifies_once():
    async def scenario():
        settings = Settings(
            _env_file=None,
            grocery_provider="instamart",
            dry_run=False,
            safety_lock=False,
            demo_mode=False,
            instamart_cart_writes=True,
        )
        store = MemoryTokenStore(
            SwiggyTokenRecord(
                client_id="client",
                access_token="token",
                expires_at=time.time() + 3600,
                selected_address_id="addr-home",
            )
        )
        oauth = SwiggyOAuthClient(settings, store)
        transport = FakeTransport()
        provider = InstamartProvider(
            settings,
            token_store=store,
            oauth=oauth,
            transport=transport,
        )
        product = Product(
            id="spin-milk",
            name="Toned Milk",
            pack_size="1 L",
            price=56,
            handle="spin-milk",
        )

        result = await provider.add_items([(product, 1)], operation_id="draft-1")
        repeated = await provider.add_items([(product, 1)], operation_id="draft-1")

        assert result[0].success is True
        assert transport.cart == {"spin-existing": 2, "spin-milk": 1}
        assert [name for name, _ in transport.calls].count("update_cart") == 1
        assert repeated[0].success is True
        assert "already applied" in repeated[0].message

    asyncio.run(scenario())


def test_instamart_cart_write_guard_produces_dry_run_without_transport():
    async def scenario():
        settings = Settings(
            _env_file=None,
            grocery_provider="instamart",
            dry_run=False,
            safety_lock=False,
            instamart_cart_writes=False,
        )
        store = MemoryTokenStore()
        transport = FakeTransport()
        provider = InstamartProvider(
            settings,
            token_store=store,
            oauth=SwiggyOAuthClient(settings, store),
            transport=transport,
        )
        product = Product(id="spin", name="Milk", price=50, handle="spin")

        result = await provider.add_items([(product, 1)], operation_id="draft")

        assert result[0].dry_run is True
        assert transport.calls == []

    asyncio.run(scenario())
