import asyncio

import pytest

from app.comparison_service import ComparisonService, _Confirmation
from app.config import Settings
from app.models import (
    AddResult,
    CartLine,
    CartPlan,
    CartSummary,
    ComparisonChoiceRequest,
    PlannedItem,
    Product,
    ProposalOverrideRequest,
    ProviderCapabilities,
)
from app.providers.base import ConnectResult, GroceryProvider, ProviderStatus


class FakeComparisonProvider(GroceryProvider):
    def __init__(self, provider_id: str, *, connected: bool = True, cart_empty: bool = True):
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self.connected = connected
        self.cart_empty = cart_empty
        self.add_calls = 0
        self.cleanup_calls = 0
        self.status_refreshes: list[bool] = []
        self._cart_total = 0.0

    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        self.status_refreshes.append(refresh)
        return ProviderStatus(
            provider=self.provider_id,
            display_name=self.display_name,
            connected=self.connected,
        )

    async def connect(self) -> ConnectResult:
        return ConnectResult(connected=self.connected)

    async def search(self, query: str) -> list[Product]:
        return [
            Product(
                id=f"{self.provider_id}-{query}",
                name=query.title(),
                pack_size="1 item",
                price=50,
                handle=f"{self.provider_id}-{query}",
            ),
            Product(
                id=f"{self.provider_id}-{query}-alt",
                name=f"{query.title()} Alternative",
                pack_size="1 item",
                price=40,
                handle=f"{self.provider_id}-{query}-alt",
            ),
        ]

    async def add_items(self, selections, *, operation_id):
        self.add_calls += 1
        self._cart_total = sum(product.price * quantity for product, quantity in selections)
        return [
            AddResult(
                product_id=product.id,
                product_name=product.name,
                requested_units=quantity,
                success=True,
            )
            for product, quantity in selections
        ]

    async def cart_summary(self) -> CartSummary:
        if self.cart_empty and not self.add_calls:
            return CartSummary(provider=self.provider_id)
        total = self._cart_total or 50
        return CartSummary(
            provider=self.provider_id,
            lines=[
                CartLine(
                    product_id="p",
                    name="Milk",
                    quantity=1,
                    unit_price=total,
                    line_total=total,
                )
            ],
            subtotal=total,
            total=total,
        )

    async def clear_cart(self, *, operation_id: str) -> None:
        self.cleanup_calls += 1

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            search=True,
            cart_read=True,
            cart_add=True,
            operation_cleanup=True,
        )

    async def close(self) -> None:
        return None


def _plan() -> CartPlan:
    return CartPlan(items=[PlannedItem(search_term="milk")])


def _write_settings() -> Settings:
    return Settings(
        _env_file=None,
        model_backend="local",
        safety_lock=False,
        dry_run=False,
        blinkit_cart_writes=True,
        instamart_cart_writes=True,
    )


def test_estimate_never_writes_and_keeps_disconnected_platform_visible():
    blinkit = FakeComparisonProvider("blinkit")
    instamart = FakeComparisonProvider("instamart", connected=False)
    service = ComparisonService(
        {"blinkit": blinkit, "instamart": instamart},
        _write_settings(),
    )

    proposal = asyncio.run(
        service.estimate(_plan(), ["blinkit", "instamart", "zepto"])
    )

    assert proposal.report.estimated is True
    assert blinkit.add_calls == 0
    by_provider = {outcome.provider: outcome for outcome in proposal.report.platforms}
    assert by_provider["instamart"].status == "not_connected"
    assert by_provider["zepto"].status == "unavailable"
    assert blinkit.status_refreshes == [True]
    assert instamart.status_refreshes == [True]


def test_verified_preflight_requires_empty_carts():
    provider = FakeComparisonProvider("blinkit", cart_empty=False)
    service = ComparisonService({"blinkit": provider}, _write_settings())

    preflight = asyncio.run(service.preflight(["blinkit"], mode="verified"))

    assert preflight.can_continue is False
    assert preflight.platforms[0].cart_empty is False


def test_confirmation_is_single_use_and_cleanup_is_idempotent():
    provider = FakeComparisonProvider("blinkit")
    service = ComparisonService({"blinkit": provider}, _write_settings())
    proposal = asyncio.run(service.estimate(_plan(), ["blinkit"]))
    preflight = asyncio.run(
        service.preflight(
            ["blinkit"],
            mode="verified",
            proposal_id=proposal.id,
        )
    )

    operation = asyncio.run(
        service.verify(proposal.id, preflight.confirmation_token)
    )
    assert provider.add_calls == 1

    with pytest.raises(ValueError):
        asyncio.run(service.verify(proposal.id, preflight.confirmation_token))

    request = ComparisonChoiceRequest(action="clear_all")
    cleaned = asyncio.run(service.choose(operation.id, request))
    repeated = asyncio.run(service.choose(operation.id, request))
    assert cleaned.status == "cleaned"
    assert repeated.status == "cleaned"
    assert provider.cleanup_calls == 1


def test_override_recalculates_proposal_and_invalidates_confirmation():
    provider = FakeComparisonProvider("blinkit")
    service = ComparisonService({"blinkit": provider}, _write_settings())
    proposal = asyncio.run(service.estimate(_plan(), ["blinkit"]))
    preflight = asyncio.run(
        service.preflight(["blinkit"], mode="verified", proposal_id=proposal.id)
    )
    draft_item = proposal.drafts["blinkit"].items[0]
    replacement = next(
        product
        for product in draft_item.candidates
        if product.id != draft_item.selected_product_id
    )

    updated = service.override(
        proposal.id,
        ProposalOverrideRequest(
            provider_id="blinkit",
            planned_item_id=draft_item.planned.id,
            product_id=replacement.id,
            units_to_add=2,
        ),
    )

    assert updated.drafts["blinkit"].items[0].selected_product_id == replacement.id
    assert updated.drafts["blinkit"].items[0].units_to_add == 2
    assert updated.frozen is False
    with pytest.raises(ValueError):
        asyncio.run(service.verify(proposal.id, preflight.confirmation_token))


def test_in_memory_state_is_bounded_and_expired_confirmations_are_purged():
    service = ComparisonService({}, Settings(max_state_records=10))
    for index in range(12):
        service._remember(service.proposals, str(index), object())

    assert list(service.proposals) == [str(index) for index in range(2, 12)]

    service._confirmations["expired"] = _Confirmation(
        proposal_id="old",
        provider_ids=(),
        expires_at=0,
    )
    service._confirmations["current"] = _Confirmation(
        proposal_id="new",
        provider_ids=(),
        expires_at=float("inf"),
    )
    service._purge_expired_confirmations()

    assert list(service._confirmations) == ["current"]
