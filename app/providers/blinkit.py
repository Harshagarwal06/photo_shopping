from __future__ import annotations

from ..blinkit import BlinkitClient, BlinkitError
from ..config import Settings
from ..models import AddResult, Product
from .base import CartSummary, ConnectResult, GroceryProvider, ProviderError, ProviderStatus


class BlinkitProvider(GroceryProvider):
    provider_id = "blinkit"
    display_name = "Blinkit"

    def __init__(self, settings: Settings):
        self.client = BlinkitClient(settings)
        self._connected = False

    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        if refresh:
            try:
                self._connected = await self.client.ensure_login(wait_for_user=False)
            except BlinkitError:
                self._connected = False
        return ProviderStatus(
            provider=self.provider_id,
            display_name=self.display_name,
            connected=self._connected,
            message="Connected through a local Playwright browser session."
            if self._connected
            else "Connect Blinkit to use the saved browser session.",
        )

    async def connect(self) -> ConnectResult:
        try:
            self._connected = await self.client.ensure_login(wait_for_user=True)
        except BlinkitError as exc:
            raise ProviderError(str(exc)) from exc
        return ConnectResult(
            connected=self._connected,
            message="Blinkit connected." if self._connected else "Blinkit login is incomplete.",
        )

    async def search(self, query: str) -> list[Product]:
        try:
            return await self.client.search(query)
        except BlinkitError as exc:
            raise ProviderError(str(exc)) from exc

    async def add_items(
        self,
        selections: list[tuple[Product, int]],
        *,
        operation_id: str,
    ) -> list[AddResult]:
        del operation_id
        results: list[AddResult] = []
        for product, quantity in selections:
            results.append(await self.client.add_to_cart(product, quantity))
        return results

    async def cart_summary(self) -> CartSummary:
        raise ProviderError(f"{self.display_name} cart reading is not implemented yet.")

    async def close(self) -> None:
        await self.client.close()
