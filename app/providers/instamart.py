from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any, Iterable

from ..config import Settings
from ..models import AddResult, Product, ProviderCapabilities
from .base import (
    CartLine,
    CartSummary,
    ConnectResult,
    FeeLine,
    GroceryProvider,
    ProviderAddress,
    ProviderAuthError,
    ProviderError,
    ProviderSafetyError,
    ProviderStatus,
)
from .instamart_oauth import SwiggyOAuthClient
from .instamart_transport import InstamartMCPTransport
from .token_store import KeyringTokenStore, TokenStore


SPIN_KEYS = ("spinId", "spin_id", "spinID")
NAME_KEYS = ("name", "title", "productName", "product_name", "displayName")
PACK_KEYS = ("packSize", "pack_size", "quantityDescription", "variantName", "displayName")
PRICE_KEYS = ("offerPrice", "sellingPrice", "finalPrice", "price")
MRP_KEYS = ("mrp", "maxRetailPrice", "listPrice")
IMAGE_KEYS = ("imageUrl", "image_url", "image", "thumbnailUrl", "thumbnail")


def _first(node: dict, keys: Iterable[str], default=None):
    for key in keys:
        value = node.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _number(value: Any, *, key: str = "") -> float | None:
    if isinstance(value, dict):
        for nested_key in (
            "offerPrice",
            "sellingPrice",
            "finalPrice",
            "value",
            "amount",
            "price",
            "units",
            "mrp",
        ):
            if nested_key in value:
                return _number(value[nested_key], key=nested_key)
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    elif isinstance(value, str):
        match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", value)
        if not match:
            return None
        number = float(match.group(0).replace(",", ""))
    else:
        return None
    return number / 100 if "paise" in key.casefold() else number


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, str):
        abbreviated = re.search(
            r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*([km])\b",
            value.casefold(),
        )
        if abbreviated:
            number = float(abbreviated.group(1).replace(",", ""))
            multiplier = 1_000 if abbreviated.group(2) == "k" else 1_000_000
            return max(0, int(number * multiplier))
    number = _number(value)
    return max(0, int(number)) if number is not None else default


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _walk_product_nodes(
    value: Any,
    inherited: dict[str, Any] | None = None,
):
    if isinstance(value, list):
        for child in value:
            yield from _walk_product_nodes(child, inherited)
        return
    if not isinstance(value, dict):
        return

    context = dict(inherited or {})
    for target, keys in (
        ("name", NAME_KEYS),
        ("pack", PACK_KEYS),
        ("image", IMAGE_KEYS),
    ):
        found = _first(value, keys)
        if found not in (None, "") and not isinstance(found, (dict, list)):
            context[target] = str(found)
    promoted = _first(value, ("isPromoted", "sponsored"))
    if promoted is not None:
        context["sponsored"] = bool(promoted)

    spin_id = _first(value, SPIN_KEYS)
    if spin_id:
        yield value, context
    for child in value.values():
        if isinstance(child, (dict, list)):
            yield from _walk_product_nodes(child, context)


def products_from_instamart(payload: dict, query: str, limit: int = 10) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()
    for node, context in _walk_product_nodes(payload):
        spin_id = str(_first(node, SPIN_KEYS) or "").strip()
        if not spin_id or spin_id in seen:
            continue
        name = str(_first(node, NAME_KEYS, context.get("name", "")) or "").strip()
        if not name:
            continue

        price_key = next((key for key in PRICE_KEYS if node.get(key) not in (None, "")), "")
        price = _number(node.get(price_key), key=price_key) if price_key else None
        if price is None:
            paise_key = next(
                (
                    key
                    for key in ("priceInPaise", "offerPriceInPaise", "sellingPriceInPaise")
                    if node.get(key) not in (None, "")
                ),
                "",
            )
            price = _number(node.get(paise_key), key=paise_key) if paise_key else None
        if price is None or price < 0:
            continue

        mrp_key = next((key for key in MRP_KEYS if node.get(key) not in (None, "")), "")
        mrp = _number(node.get(mrp_key), key=mrp_key) if mrp_key else None
        price_details = node.get("price")
        if mrp is None and isinstance(price_details, dict):
            nested_mrp_key = next(
                (key for key in MRP_KEYS if price_details.get(key) not in (None, "")),
                "",
            )
            mrp = (
                _number(price_details.get(nested_mrp_key), key=nested_mrp_key)
                if nested_mrp_key
                else None
            )
        pack = str(_first(node, PACK_KEYS, context.get("pack", "")) or "").strip()
        image = _first(node, IMAGE_KEYS, context.get("image"))
        if isinstance(image, dict):
            image = _first(image, ("url", "src", "value"))
        rating_value = _first(node, ("rating", "averageRating", "avgRating"))
        rating = _number(rating_value)
        review_count = _integer(_first(node, ("reviewCount", "ratingCount", "ratingsCount")))
        if not review_count and isinstance(rating_value, dict):
            review_count = _integer(_first(rating_value, ("count", "ratingCount")))
        available_value = _first(
            node,
            ("inStock", "available", "isAvailable", "isInStockAndAvailable", "isAvail"),
            True,
        )
        in_stock = bool(available_value) and not bool(node.get("outOfStock"))
        discount = _number(_first(node, ("discountPercent", "discount_percentage")))
        if discount is None and mrp and mrp > price:
            discount = round((mrp - price) / mrp * 100, 1)
        eta = _integer(_first(node, ("deliveryMinutes", "etaMinutes", "eta"))) or None
        if eta is None and isinstance(node.get("sla"), dict):
            eta = _integer(_first(node["sla"], ("value", "minutes"))) or None
        sponsored = bool(
            _first(node, ("isPromoted", "sponsored"), context.get("sponsored", False))
        )

        seen.add(spin_id)
        products.append(
            Product(
                id=spin_id,
                name=name,
                pack_size=pack,
                price=price,
                mrp=mrp,
                discount_percent=max(0, discount or 0),
                delivery_minutes=eta,
                rating=rating if rating is not None and 0 <= rating <= 5 else None,
                review_count=review_count,
                image_url=str(image) if image else None,
                in_stock=in_stock,
                sponsored=sponsored,
                handle=spin_id,
                search_query=query,
            )
        )
        if len(products) >= limit:
            break
    return products


def cart_items_from_instamart(payload: dict) -> dict[str, int]:
    items: dict[str, int] = {}
    for node, _ in _walk_product_nodes(payload):
        spin_id = str(_first(node, SPIN_KEYS) or "").strip()
        quantity = _integer(_first(node, ("quantity", "qty", "count")))
        if spin_id and quantity > 0:
            items[spin_id] = max(quantity, items.get(spin_id, 0))
    return items


FEE_LABELS = {
    "deliveryFee": "Delivery fee",
    "handlingFee": "Handling fee",
    "surgeFee": "Surge fee",
    "rainFee": "Rain fee",
    "smallCartFee": "Small cart fee",
    "packagingFee": "Packaging fee",
    "discount": "Discount",
    "couponDiscount": "Coupon discount",
}


def _fees_from_bill(bill: dict) -> list[FeeLine]:
    """Read fee lines out of a get_cart bill/billBreakdown dict.

    The live spike (tests/fixtures/instamart_cart.json) showed a real
    ``billBreakdown: {"lineItems": [...], "toPay": {...}}`` shape rather than a
    flat dict of named fee keys, but the recorded cart was empty so the shape of
    a populated ``lineItems`` entry is unverified. This reads a list of
    {label, value} entries under "lineItems" first; only when that produces no
    fee lines does it fall back to flat named keys (as the brief's assumed
    shape used). The two are never combined, since a payload that happens to
    carry both shapes for the same fees would otherwise be counted twice.
    """
    fees: list[FeeLine] = []
    line_items = bill.get("lineItems")
    if isinstance(line_items, list):
        for entry in line_items:
            if not isinstance(entry, dict):
                continue
            label = str(_first(entry, ("label", "name", "title"), "") or "").strip()
            amount = _number(_first(entry, ("value", "amount")))
            if label and amount not in (None, 0):
                fees.append(FeeLine(label=label, amount=amount))
    if fees:
        return fees
    for key, label in FEE_LABELS.items():
        value = _number(bill.get(key))
        if value not in (None, 0):
            fees.append(FeeLine(label=label, amount=value))
    return fees


def cart_summary_from_instamart(payload: dict) -> CartSummary:
    """Convert a get_cart payload into a CartSummary. Pure; unit-tested.

    The real spike recording (tests/fixtures/instamart_cart.json) showed a flat
    top-level shape (no "cart" wrapper key), with "items" at the top level and a
    "billBreakdown" dict rather than "bill". The `cart = payload.get("cart",
    payload)` fallback below handles both. The recorded cart was empty, so item
    field names (spinId/name/quantity/price) and populated fee-line shapes are
    unverified; item extraction reuses the same SPIN_KEYS/NAME_KEYS already
    proven against real Instamart payloads elsewhere in this module.

    ``estimated`` reflects what Instamart actually failed to report, not
    whether the cart happens to have zero fees: it is True when no bill
    breakdown was present at all, and/or when no total key was present so the
    total had to be derived locally as subtotal + fees. A derived total always
    reconciles against itself (CartSummary.reconciles compares the reported
    total to computed_total), so treating it as verified would silently defeat
    the reconciliation safety check documented on CartSummary.
    """
    cart = payload.get("cart", payload) or {}
    raw_items = cart.get("items", []) or []
    bill = _first(cart, ("bill", "billBreakdown"), {}) or {}
    breakdown_absent = not bill

    lines: list[CartLine] = []
    for raw in raw_items:
        quantity = _integer(_first(raw, ("quantity", "qty"), 0))
        unit_price = _number(_first(raw, ("price", "unitPrice"))) or 0.0
        line_total = _number(_first(raw, ("total", "lineTotal")))
        lines.append(
            CartLine(
                product_id=str(
                    _first(raw, (*SPIN_KEYS, "productId", "product_id", "id"), "") or ""
                ),
                name=str(_first(raw, NAME_KEYS, "") or "").strip(),
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total if line_total is not None else unit_price * quantity,
            )
        )

    subtotal = _number(_first(bill, ("itemTotal", "subTotal", "subtotal")))
    if subtotal is None:
        subtotal = round(sum(line.line_total for line in lines), 2)

    fees = _fees_from_bill(bill)

    reported_total = _number(_first(bill, ("grandTotal", "total", "payableAmount", "toPay")))
    total_was_reported = reported_total is not None
    total = (
        reported_total
        if total_was_reported
        else round(subtotal + sum(fee.amount for fee in fees), 2)
    )

    notes: list[str] = []
    if breakdown_absent:
        notes.append("Instamart did not report a fee breakdown.")
    if not total_was_reported:
        notes.append(
            "Instamart did not report a total; the total shown was calculated "
            "locally from the subtotal and fees."
        )

    return CartSummary(
        provider="instamart",
        lines=lines,
        subtotal=round(subtotal, 2),
        fees=fees,
        total=round(total, 2),
        delivery_eta_minutes=_integer(_first(cart, ("etaMinutes", "eta"), 0)) or None,
        estimated=bool(notes),
        raw_note=" ".join(notes),
    )


def zeroed_cart_update(payload: dict) -> list[dict]:
    """Build the update_cart items body that empties the cart.

    Reuses cart_items_from_instamart so spin-id extraction lives in exactly one
    place and handles the same nested payload shapes add_items already handles.
    """
    return [
        {"spinId": spin_id, "quantity": 0}
        for spin_id in cart_items_from_instamart(payload)
    ]


def merge_cart_items(
    existing: dict[str, int],
    additions: dict[str, int],
) -> dict[str, int]:
    merged = {spin_id: quantity for spin_id, quantity in existing.items() if quantity > 0}
    for spin_id, quantity in additions.items():
        if quantity > 0:
            merged[spin_id] = merged.get(spin_id, 0) + quantity
    return merged


def addresses_from_instamart(payload: dict) -> list[ProviderAddress]:
    addresses: list[ProviderAddress] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return
        address_id = _first(value, ("addressId", "address_id", "id"))
        if address_id and str(address_id) not in seen:
            label = str(
                _first(
                    value,
                    (
                        "label",
                        "name",
                        "type",
                        "addressType",
                        "addressTag",
                        "addressCategory",
                    ),
                    "Saved address",
                )
            )
            detail_value = _first(
                value,
                (
                    "displayAddress",
                    "formattedAddress",
                    "address",
                    "subtitle",
                    "addressLine",
                ),
                "",
            )
            detail = detail_value if isinstance(detail_value, str) else ""
            seen.add(str(address_id))
            addresses.append(
                ProviderAddress(id=str(address_id), label=label, detail=detail)
            )
        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child)

    visit(payload)
    return addresses


class InstamartProvider(GroceryProvider):
    provider_id = "instamart"
    display_name = "Swiggy Instamart"

    def __init__(
        self,
        settings: Settings,
        *,
        token_store: TokenStore | None = None,
        oauth: SwiggyOAuthClient | None = None,
        transport: InstamartMCPTransport | None = None,
    ):
        self.settings = settings
        self.token_store = token_store or KeyringTokenStore(
            settings.swiggy_keyring_service,
            settings.swiggy_keyring_account,
        )
        self.oauth = oauth or SwiggyOAuthClient(settings, self.token_store)
        self.transport = transport or InstamartMCPTransport(
            settings.instamart_mcp_url,
            self.oauth,
        )
        self._addresses: list[ProviderAddress] = []
        self._history_ids: Counter[str] = Counter()
        self._history_names: Counter[str] = Counter()
        self._history_address_id: str | None = None
        self._cart_lock = asyncio.Lock()
        self._applied_operations: set[str] = set()
        self._operation_additions: dict[str, dict[str, int]] = {}

    async def connect(self) -> ConnectResult:
        return ConnectResult(
            connected=False,
            authorization_url=await self.oauth.authorization_url(),
            message="Continue in Swiggy to connect Instamart.",
        )

    async def complete_oauth(self, code: str, state: str) -> None:
        await self.oauth.exchange_code(code, state)
        self._addresses = []
        await self.status(refresh=True)

    async def disconnect(self) -> None:
        await self.oauth.logout()
        self._addresses = []
        self._history_ids.clear()
        self._history_names.clear()
        self._history_address_id = None

    async def status(self, *, refresh: bool = False) -> ProviderStatus:
        if not self.oauth.is_connected():
            return ProviderStatus(
                provider=self.provider_id,
                display_name=self.display_name,
                connected=False,
                message="Connect your Swiggy account to search Instamart.",
            )
        if refresh or not self._addresses:
            try:
                self._addresses = addresses_from_instamart(
                    await self.transport.call_tool("get_addresses")
                )
            except ProviderAuthError:
                return ProviderStatus(
                    provider=self.provider_id,
                    display_name=self.display_name,
                    connected=False,
                    message="The Instamart session expired. Reconnect to continue.",
                )
        selected = self.oauth.selected_address_id()
        valid_ids = {address.id for address in self._addresses}
        if selected not in valid_ids:
            selected = None
        if selected is None and len(self._addresses) == 1:
            selected = self._addresses[0].id
            self.oauth.save_selected_address(selected)
        return ProviderStatus(
            provider=self.provider_id,
            display_name=self.display_name,
            connected=True,
            requires_address=bool(self._addresses and selected is None),
            selected_address_id=selected,
            addresses=self._addresses,
            message=(
                "Choose a delivery address."
                if self._addresses and selected is None
                else "Instamart connected."
                if selected
                else (
                    "No saved address found. Open Swiggy to add one, "
                    "then refresh addresses here."
                )
            ),
        )

    async def select_address(self, address_id: str) -> ProviderStatus:
        status = await self.status(refresh=not self._addresses)
        if not status.connected:
            raise ProviderAuthError(status.message)
        if address_id not in {address.id for address in status.addresses}:
            raise ProviderError("That Instamart delivery address is unavailable.")
        self.oauth.save_selected_address(address_id)
        self._history_address_id = None
        return await self.status()

    async def _ready_address_id(self) -> str:
        status = await self.status()
        if not status.connected:
            raise ProviderAuthError(status.message)
        if not status.selected_address_id:
            raise ProviderError(status.message)
        return status.selected_address_id

    async def _load_history(self, address_id: str) -> None:
        if self._history_address_id == address_id:
            return
        payload = await self.transport.call_tool(
            "your_go_to_items",
            {"addressId": address_id, "offset": 0},
        )
        go_to_products = products_from_instamart(payload, "", limit=100)
        self._history_ids = Counter(product.handle for product in go_to_products)
        self._history_names = Counter(_normalise_name(product.name) for product in go_to_products)
        self._history_address_id = address_id

    async def search(self, query: str) -> list[Product]:
        address_id = await self._ready_address_id()
        try:
            await self._load_history(address_id)
        except ProviderError:
            # Product search still works if the preference endpoint is unavailable.
            self._history_address_id = address_id
        payload = await self.transport.call_tool(
            "search_products",
            {"addressId": address_id, "query": query, "offset": 0},
        )
        products = products_from_instamart(
            payload,
            query,
            limit=self.settings.search_result_limit,
        )
        for product in products:
            count = self._history_ids.get(product.handle, 0)
            if not count:
                count = self._history_names.get(_normalise_name(product.name), 0)
            product.past_order_count = count
        return products

    async def add_items(
        self,
        selections: list[tuple[Product, int]],
        *,
        operation_id: str,
    ) -> list[AddResult]:
        if not selections:
            return []
        if not self.settings.cart_mutations_allowed_for(self.provider_id):
            return [
                AddResult(
                    product_id=product.id,
                    product_name=product.name,
                    requested_units=quantity,
                    success=True,
                    dry_run=True,
                    message=(
                        f"Instamart safe test: would add {quantity} × {product.name}; "
                        "the cart was not changed."
                    ),
                )
                for product, quantity in selections
            ]
        if operation_id in self._applied_operations:
            return [
                AddResult(
                    product_id=product.id,
                    product_name=product.name,
                    requested_units=quantity,
                    success=True,
                    message=f"{product.name} was already applied for this draft.",
                )
                for product, quantity in selections
            ]

        address_id = await self._ready_address_id()
        async with self._cart_lock:
            if operation_id in self._applied_operations:
                return [
                    AddResult(
                        product_id=product.id,
                        product_name=product.name,
                        requested_units=quantity,
                        success=True,
                        message=f"{product.name} was already applied for this draft.",
                    )
                    for product, quantity in selections
                ]
            try:
                current_payload = await self.transport.call_tool("get_cart")
                existing = cart_items_from_instamart(current_payload)
                additions: dict[str, int] = {}
                for product, quantity in selections:
                    spin_id = product.handle or product.id
                    additions[spin_id] = additions.get(spin_id, 0) + quantity
                merged = merge_cart_items(existing, additions)
                await self.transport.call_tool(
                    "update_cart",
                    {
                        "selectedAddressId": address_id,
                        "items": [
                            {"spinId": spin_id, "quantity": quantity}
                            for spin_id, quantity in merged.items()
                        ],
                    },
                )
                verified = cart_items_from_instamart(
                    await self.transport.call_tool("get_cart")
                )
                missing = {
                    spin_id: quantity
                    for spin_id, quantity in merged.items()
                    if verified.get(spin_id, 0) < quantity
                }
                if missing:
                    raise ProviderError(
                        "Instamart returned an unexpected cart after the update; "
                        "review the cart in Swiggy."
                    )
            except ProviderError as exc:
                return [
                    AddResult(
                        product_id=product.id,
                        product_name=product.name,
                        requested_units=quantity,
                        success=False,
                        message=f"Instamart could not add this item: {exc}",
                    )
                    for product, quantity in selections
                ]
            self._applied_operations.add(operation_id)
            self._operation_additions[operation_id] = additions

        return [
            AddResult(
                product_id=product.id,
                product_name=product.name,
                requested_units=quantity,
                success=True,
                message=f"Added {quantity} × {product.name} to the Instamart cart.",
            )
            for product, quantity in selections
        ]

    async def cart_summary(self) -> CartSummary:
        async with self._cart_lock:
            payload = await self.transport.call_tool("get_cart")
        return cart_summary_from_instamart(payload)

    async def clear_cart(self, *, operation_id: str) -> None:
        if not self.settings.cart_mutations_allowed_for(self.provider_id):
            raise ProviderSafetyError(
                "Instamart cart writes are disabled, so the cart was not cleared."
            )
        additions = self._operation_additions.get(operation_id)
        if additions is None:
            raise ProviderSafetyError(
                "Instamart has no comparison-cart ledger for this operation."
            )
        address_id = await self._ready_address_id()
        async with self._cart_lock:
            payload = await self.transport.call_tool("get_cart")
            current = cart_items_from_instamart(payload)
            if not current:
                self._operation_additions.pop(operation_id, None)
                self._applied_operations.discard(operation_id)
                return
            expected = dict(current)
            for spin_id, quantity in additions.items():
                expected[spin_id] = max(0, expected.get(spin_id, 0) - quantity)
            updates = [
                {"spinId": spin_id, "quantity": quantity}
                for spin_id, quantity in expected.items()
            ]
            await self.transport.call_tool(
                "update_cart",
                {"selectedAddressId": address_id, "items": updates},
            )
            remaining = cart_items_from_instamart(
                await self.transport.call_tool("get_cart")
            )
            mismatched = {
                spin_id: quantity
                for spin_id, quantity in expected.items()
                if remaining.get(spin_id, 0) != quantity
            }
            if mismatched:
                raise ProviderError(
                    "Instamart cleanup could not restore the comparison quantities; "
                    "review the cart in Swiggy."
                )
            self._operation_additions.pop(operation_id, None)
            self._applied_operations.discard(operation_id)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            search=True,
            cart_read=True,
            cart_add=True,
            operation_cleanup=True,
        )

    async def close(self) -> None:
        return None

    async def diagnostics(self) -> dict[str, Any]:
        return {
            "allowed_tools": sorted(
                {
                    "get_addresses",
                    "search_products",
                    "your_go_to_items",
                    "get_cart",
                    "update_cart",
                    "get_orders",
                    "get_order_details",
                }
            ),
            "cart_writes_enabled": self.settings.cart_mutations_allowed_for(
                self.provider_id
            ),
            "checkout_available": False,
        }
