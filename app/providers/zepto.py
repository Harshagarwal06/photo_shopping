from __future__ import annotations

from ..config import Settings
from ..models import AddResult, CartSummary, Product, ProviderCapabilities
from ..zepto import ZeptoClient, ZeptoError
from .base import (
    ConnectResult,
    GroceryProvider,
    ProviderError,
    ProviderSafetyError,
    ProviderStatus,
)


class ZeptoProvider(GroceryProvider):
    provider_id = "zepto"
    display_name = "Zepto"

    def __init__(self, settings: Settings, *, client: ZeptoClient | None = None):
        self.settings = settings
        self.client = client or ZeptoClient(settings)
        self._connected = False
        self._operations: dict[str, list[tuple[Product, int]]] = {}

    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        if refresh:
            try:
                self._connected = await self.client.ensure_login(wait_for_user=False)
            except ZeptoError:
                self._connected = False
        return ProviderStatus(
            provider=self.provider_id,
            display_name=self.display_name,
            connected=self._connected,
            message=(
                "Connected through a local Zepto browser session."
                if self._connected
                else "Connect Zepto to use its saved browser session."
            ),
        )

    async def connect(self) -> ConnectResult:
        try:
            self._connected = await self.client.ensure_login(wait_for_user=True)
        except ZeptoError as exc:
            raise ProviderError(str(exc)) from exc
        return ConnectResult(
            connected=self._connected,
            message="Zepto connected." if self._connected else "Zepto login is incomplete.",
        )

    async def search(self, query: str) -> list[Product]:
        try:
            return await self.client.search(query)
        except ZeptoError as exc:
            raise ProviderError(str(exc)) from exc

    async def add_items(
        self,
        selections: list[tuple[Product, int]],
        *,
        operation_id: str,
    ) -> list[AddResult]:
        results = [
            await self.client.add_to_cart(product, quantity)
            for product, quantity in selections
        ]
        successful = {
            result.product_id
            for result in results
            if result.success and not result.dry_run
        }
        self._operations[operation_id] = [
            (product, quantity)
            for product, quantity in selections
            if product.id in successful
        ]
        return results

    async def cart_summary(self) -> CartSummary:
        try:
            return await self.client.cart_summary()
        except ZeptoError as exc:
            raise ProviderError(str(exc)) from exc

    async def clear_cart(self, *, operation_id: str) -> None:
        if not self.settings.cart_mutations_allowed_for(self.provider_id):
            raise ProviderSafetyError(
                "Zepto cart writes are disabled, so cleanup was not attempted."
            )
        selections = self._operations.get(operation_id)
        if selections is None:
            raise ProviderSafetyError(
                "Zepto has no comparison-cart ledger for this operation."
            )
        try:
            for product, quantity in selections:
                await self.client.remove_from_cart(product, quantity)
        except ZeptoError as exc:
            raise ProviderError(str(exc)) from exc
        self._operations.pop(operation_id, None)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            search=True,
            cart_read=True,
            cart_add=True,
            operation_cleanup=True,
        )

    async def close(self) -> None:
        await self.client.close()
