from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ItemSource(StrEnum):
    DIRECT = "direct"
    PHOTO = "photo"
    TEXT = "text"
    BOTH = "both"


class PlannedItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    search_term: str = Field(min_length=1)
    context: str = ""
    quantity: float = Field(default=1, gt=0)
    unit: str = "item"
    raw_text: str = ""
    source: str = "direct"

    @field_validator("search_term", "context", "unit", "raw_text", "source", mode="before")
    @classmethod
    def clean_strings(cls, value: Any) -> str:
        return str(value or "").strip()


class CartConstraints(BaseModel):
    cart_budget: float | None = Field(default=None, gt=0)
    item_caps: dict[str, float] = Field(default_factory=dict)
    preferences: list[str] = Field(default_factory=list)


class CartPlan(BaseModel):
    items: list[PlannedItem] = Field(min_length=1)
    constraints: CartConstraints = Field(default_factory=CartConstraints)
    processing_note: str = ""


class Product(BaseModel):
    id: str
    name: str
    pack_size: str = ""
    price: float = Field(ge=0)
    mrp: float | None = Field(default=None, ge=0)
    discount_percent: float = Field(default=0, ge=0)
    delivery_minutes: int | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int = Field(default=0, ge=0)
    past_order_count: int = Field(default=0, ge=0)
    sponsored: bool = False
    image_url: str | None = None
    in_stock: bool = True
    handle: str
    product_url: str | None = None
    search_query: str = ""


class MatchDecision(BaseModel):
    product_id: str | None = None
    units_to_add: int = Field(default=1, ge=0)
    reason: str


class CrossPlatformMatch(BaseModel):
    """One planned item resolved against every platform at once."""

    picks: dict[str, MatchDecision] = Field(default_factory=dict)
    equivalence_note: str = ""


class DraftItem(BaseModel):
    planned: PlannedItem
    candidates: list[Product] = Field(default_factory=list)
    selected_product_id: str | None = None
    units_to_add: int = Field(default=1, ge=0)
    reason: str = ""
    flags: list[str] = Field(default_factory=list)
    removed: bool = False

    @property
    def selected_product(self) -> Product | None:
        return next((p for p in self.candidates if p.id == self.selected_product_id), None)


class DraftCart(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    provider_id: str = ""
    provider_name: str = ""
    items: list[DraftItem]
    cart_budget: float | None = None
    total: float = 0
    notices: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    auto_add_messages: list[str] = Field(default_factory=list)
    auto_add_errors: list[str] = Field(default_factory=list)
    dry_run: bool = True


class DraftSelection(BaseModel):
    planned_item_id: str
    product_id: str
    units_to_add: int = Field(ge=1, le=50)


class ConfirmRequest(BaseModel):
    draft_id: str
    selections: list[DraftSelection]


class AddResult(BaseModel):
    product_id: str
    product_name: str
    requested_units: int
    success: bool
    dry_run: bool = False
    message: str


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


class ConfirmResponse(BaseModel):
    results: list[AddResult]
    succeeded: int
    failed: int
    dry_run: bool


class SearchRequest(BaseModel):
    draft_id: str
    planned_item_id: str
    query: str = Field(min_length=1)


class AddressSelectionRequest(BaseModel):
    address_id: str = Field(min_length=1)


class ProviderSelectionRequest(BaseModel):
    provider_id: Literal["blinkit", "instamart", "zepto"]


class StreamEvent(BaseModel):
    event: Literal["stage", "plan", "item", "draft", "error"]
    stage: str
    message: str
    data: dict[str, Any] | None = None
