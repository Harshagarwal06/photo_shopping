from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from ..models import AddResult, Product


class ProviderError(RuntimeError):
    """A grocery-provider error that is safe to show in the local UI."""


class ProviderAuthError(ProviderError):
    """The provider needs the user to reconnect."""


class ProviderSafetyError(ProviderError):
    """A provider operation was rejected by the application's safety boundary."""


class CartReconciliationError(ProviderError):
    """A cart's reported total disagreed with its own line items and fees."""


class ProviderAddress(BaseModel):
    id: str
    label: str
    detail: str = ""


class FeeLine(BaseModel):
    """One line on the cart bill. Discounts and coupons are negative."""

    label: str
    amount: float


class CartLine(BaseModel):
    product_id: str
    name: str
    quantity: int = Field(ge=0)
    unit_price: float = Field(ge=0)
    line_total: float = Field(ge=0)


class CartSummary(BaseModel):
    provider: str
    lines: list[CartLine] = Field(default_factory=list)
    subtotal: float = 0
    fees: list[FeeLine] = Field(default_factory=list)
    total: float = 0
    delivery_eta_minutes: int | None = Field(default=None, ge=0)
    estimated: bool = False
    raw_note: str = ""

    @property
    def computed_total(self) -> float:
        return round(self.subtotal + sum(fee.amount for fee in self.fees), 2)

    @property
    def reconciles(self) -> bool:
        return abs(self.computed_total - round(self.total, 2)) < 0.01

    @property
    def reconciliation_error(self) -> str | None:
        if self.reconciles:
            return None
        return (
            f"{self.provider} reported a total of ₹{self.total:.2f} but its lines "
            f"and fees add up to ₹{self.computed_total:.2f}. A fee line was probably "
            "missed, so this platform is not safe to compare."
        )


class ProviderStatus(BaseModel):
    provider: str
    display_name: str
    connected: bool
    requires_address: bool = False
    selected_address_id: str | None = None
    addresses: list[ProviderAddress] = Field(default_factory=list)
    message: str = ""


class ConnectResult(BaseModel):
    connected: bool = False
    authorization_url: str | None = None
    message: str = ""


class GroceryProvider(ABC):
    provider_id: str
    display_name: str

    @abstractmethod
    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    async def connect(self) -> ConnectResult:
        raise NotImplementedError

    async def complete_oauth(self, code: str, state: str) -> None:
        raise ProviderError(f"{self.display_name} does not use an OAuth callback.")

    async def disconnect(self) -> None:
        return None

    async def select_address(self, address_id: str) -> ProviderStatus:
        raise ProviderError(f"{self.display_name} does not require address selection.")

    @abstractmethod
    async def search(self, query: str) -> list[Product]:
        raise NotImplementedError

    @abstractmethod
    async def add_items(
        self,
        selections: list[tuple[Product, int]],
        *,
        operation_id: str,
    ) -> list[AddResult]:
        raise NotImplementedError

    @abstractmethod
    async def cart_summary(self) -> CartSummary:
        """Read the provider's current cart, including real fees."""
        raise NotImplementedError

    async def clear_cart(self, *, operation_id: str) -> None:
        raise ProviderError(f"{self.display_name} cannot clear its cart yet.")

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    async def diagnostics(self) -> dict[str, Any]:
        return {}
