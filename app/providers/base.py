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


class ProviderAddress(BaseModel):
    id: str
    label: str
    detail: str = ""


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
    async def close(self) -> None:
        raise NotImplementedError

    async def diagnostics(self) -> dict[str, Any]:
        return {}
