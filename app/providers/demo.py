from __future__ import annotations

from ..demo import demo_search
from ..models import AddResult, CartSummary, Product, ProviderCapabilities
from .base import ConnectResult, GroceryProvider, ProviderStatus


class DemoProvider(GroceryProvider):
    """A fully local provider used to keep demo mode isolated from live services."""

    def __init__(self, provider_id: str, display_name: str):
        self.provider_id = provider_id
        self.display_name = display_name

    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        return ProviderStatus(
            provider=self.provider_id,
            display_name=self.display_name,
            connected=True,
            message="Demo catalogue connected; no shopping account is used.",
        )

    async def connect(self) -> ConnectResult:
        return ConnectResult(
            connected=True,
            message="Demo catalogue connected.",
        )

    async def search(self, query: str) -> list[Product]:
        return demo_search(query)

    async def add_items(
        self,
        selections: list[tuple[Product, int]],
        *,
        operation_id: str,
    ) -> list[AddResult]:
        return [
            AddResult(
                product_id=product.id,
                product_name=product.name,
                requested_units=quantity,
                success=True,
                dry_run=True,
                message=(
                    f"Demo only: would add {quantity} × {product.name}; "
                    "no cart was changed."
                ),
            )
            for product, quantity in selections
        ]

    async def cart_summary(self) -> CartSummary:
        return CartSummary(
            provider=self.provider_id,
            estimated=True,
            raw_note="Demo mode has no live cart.",
        )

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(search=True)

    async def close(self) -> None:
        return None
