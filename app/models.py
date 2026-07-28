from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, field_validator


PROVIDER_QUERY_BRANDS = (
    "aashirvaad", "amul", "britannia", "cadbury", "colgate", "daawat", "fortune",
    "havmor", "kelloggs", "kitkat", "knorr", "kurkure", "maggi", "mother dairy",
    "oreo", "pintola", "real", "rin", "tata",
)


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
    confidence: float = Field(default=1.0, ge=0, le=1)
    needs_review: bool = False
    confirmed: bool = False
    alternatives: list[str] = Field(default_factory=list)
    recognition_notes: list[str] = Field(default_factory=list)
    recognition_decision: Literal[
        "accepted", "catalog_corrected", "skipped", "review"
    ] = "accepted"
    crop_box: list[float] = Field(default_factory=list)

    @field_validator("search_term", "context", "unit", "raw_text", "source", mode="before")
    @classmethod
    def clean_strings(cls, value: Any) -> str:
        return str(value or "").strip()

    def _provider_brand_prefix(self) -> str:
        matches = []
        for brand in PROVIDER_QUERY_BRANDS:
            match = re.search(
                rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])",
                self.context,
                flags=re.IGNORECASE,
            )
            if match:
                matches.append(match.group(0))
        return " ".join(matches)

    @computed_field
    @property
    def provider_query(self) -> str:
        """The catalogue query, retaining a separately parsed brand or context."""
        return " ".join(
            part for part in (self._provider_brand_prefix(), self.search_term) if part
        ).strip()

    def apply_manual_query(self, query: str) -> None:
        """Replace the editable provider query without retaining a stale brand."""
        cleaned = query.strip()
        brand_prefix = self._provider_brand_prefix()
        context_prefix = f"{brand_prefix} ".casefold() if brand_prefix else ""
        if context_prefix and cleaned.casefold().startswith(context_prefix):
            self.search_term = cleaned[len(brand_prefix) :].strip()
        else:
            self.search_term = cleaned
            self.context = ""
        self.needs_review = False
        self.confirmed = True


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


class Substitution(BaseModel):
    item: str
    requested: str
    supplied: str
    reason: str
    per_unit_delta: float | None = None


class PlatformOutcome(BaseModel):
    provider: str
    display_name: str
    status: Literal["ok", "not_connected", "unavailable", "failed"] = "ok"
    error: str = ""
    summary: CartSummary | None = None
    matched_items: int = 0
    partial_items: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    unverified_items: list[str] = Field(default_factory=list)
    substitutions: list[Substitution] = Field(default_factory=list)

    @property
    def coverage_tier(self) -> int:
        """0 = full coverage, 1 = short packs, 2 = missing items. Lower wins."""
        if self.missing_items:
            return 2
        if self.partial_items:
            return 1
        return 0


class ComparisonReport(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    platforms: list[PlatformOutcome] = Field(default_factory=list)
    winner: str | None = None
    ranking: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    estimated: bool = False


class ProviderCapabilities(BaseModel):
    search: bool = True
    cart_read: bool = False
    cart_add: bool = False
    operation_cleanup: bool = False
    checkout: bool = False


class PlatformPreflight(BaseModel):
    provider: str
    display_name: str
    connected: bool = False
    eligible: bool = False
    cart_empty: bool | None = None
    cart_mutations_allowed: bool = False
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    message: str = ""


class ComparisonPreflight(BaseModel):
    mode: Literal["estimated", "verified"] = "estimated"
    platforms: list[PlatformPreflight] = Field(default_factory=list)
    can_continue: bool = False
    requires_confirmation: bool = False
    confirmation_token: str | None = None


class ComparisonProposal(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    mode: Literal["estimated", "verified"] = "estimated"
    plan: CartPlan
    report: ComparisonReport
    provider_ids: list[str] = Field(default_factory=list)
    drafts: dict[str, DraftCart] = Field(default_factory=dict)
    frozen: bool = False


class ProposalOverrideRequest(BaseModel):
    provider_id: Literal["blinkit", "instamart", "zepto"]
    planned_item_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    units_to_add: int = Field(ge=1, le=50)


class ComparisonPreflightRequest(BaseModel):
    provider_ids: list[Literal["blinkit", "instamart", "zepto"]] = Field(
        default_factory=lambda: ["blinkit", "instamart", "zepto"]
    )
    mode: Literal["estimated", "verified"] = "estimated"
    proposal_id: str | None = None


class VerifiedComparisonRequest(BaseModel):
    confirmation_token: str = Field(min_length=16)


class ComparisonChoiceRequest(BaseModel):
    action: Literal["keep_winner", "keep_all", "clear_all"] = "keep_winner"
    winner: Literal["blinkit", "instamart", "zepto"] | None = None


class CleanupOutcome(BaseModel):
    provider: str
    success: bool
    message: str


class ComparisonOperation(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    proposal_id: str
    provider_ids: list[str] = Field(default_factory=list)
    report: ComparisonReport
    status: Literal[
        "completed",
        "cleanup_pending",
        "cleaned",
        "cleanup_failed",
    ] = "completed"
    winner: str | None = None
    cleanup: list[CleanupOutcome] = Field(default_factory=list)


class ConfirmResponse(BaseModel):
    results: list[AddResult]
    succeeded: int
    failed: int
    dry_run: bool


class SearchRequest(BaseModel):
    draft_id: str
    planned_item_id: str
    query: str = Field(min_length=1)

    @field_validator("draft_id", "planned_item_id", "query", mode="before")
    @classmethod
    def reject_blank_search_fields(cls, value: Any) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("Search fields cannot be blank.")
        return cleaned


class AddressSelectionRequest(BaseModel):
    address_id: str = Field(min_length=1)


class ProviderSelectionRequest(BaseModel):
    provider_id: Literal["blinkit", "instamart", "zepto"]


class StreamEvent(BaseModel):
    event: Literal["stage", "plan", "item", "draft", "error"]
    stage: str
    message: str
    data: dict[str, Any] | None = None
