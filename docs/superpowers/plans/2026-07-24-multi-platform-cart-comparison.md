# Multi-Platform Cart Comparison Implementation Plan

> **Superseded for execution:** Use
> `docs/superpowers/plans/2026-07-24-multi-platform-cart-comparison-revised.md`.
> This original plan remains as design history.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the same grocery basket on Blinkit, Zepto, and Swiggy Instamart, read each platform's real cart total including fees, and deterministically recommend which one to order from.

**Architecture:** The existing `GroceryProvider` registry (`app/providers/factory.py`) gains two methods — `cart_summary()` and `clear_cart()` — plus a third Playwright provider for Zepto. A new orchestrator fans the existing plan → search → match → constraints pipeline across all connected providers in parallel; a new pure-function `compare` module ranks the resulting real cart totals. All LLM use stays in planning and matching; ranking is deterministic.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, pydantic-settings, Playwright (Chromium, persistent contexts), Swiggy MCP over HTTP, pytest, vanilla JS frontend.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-multi-platform-cart-comparison-design.md`. Read it before starting.
- Run tests with `.venv/bin/python -m pytest`. Baseline at plan time: **38 passing**. Never finish a task with fewer passing than you started.
- **Fail closed.** All cart writes go through `settings.cart_mutations_allowed_for(provider_id)` (`app/config.py:62`). Never bypass it.
- **Checkout stays disabled on every platform.** No task may add ordering, payment, or checkout.
- No new runtime dependencies. Everything needed is already in `requirements.txt`.
- Money is `float` rupees, rounded to 2 decimals at boundaries — matching `app/constraints.py:119`.
- Reuse `parse_measurement`, `requested_measurement`, `units_for_candidate` from `app/constraints.py`. Do not reimplement measurement parsing.
- Follow the existing pure-core pattern: Playwright/MCP glue calls a **pure module-level function** that converts raw dicts into models (see `_products_from_raw` in `app/blinkit.py:79` and its test `tests/test_blinkit_products.py`). The pure function is what gets unit-tested.
- Type hints on every new function. `from __future__ import annotations` at the top of every new module.

---

### Task 1: Cart summary models and provider interface

**Files:**
- Modify: `app/providers/base.py`
- Test: `tests/test_cart_summary.py` (create)

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `FeeLine`, `CartLine`, `CartSummary`, `CartReconciliationError`; `GroceryProvider.cart_summary()`, `GroceryProvider.clear_cart(*, operation_id)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cart_summary.py`:

```python
import pytest

from app.providers.base import CartLine, CartSummary, FeeLine


def _line(total: float = 100.0) -> CartLine:
    return CartLine(
        product_id="p1", name="Amul Taaza 1 L", quantity=1,
        unit_price=total, line_total=total,
    )


def test_summary_reconciles_when_fees_add_up():
    summary = CartSummary(
        provider="blinkit", lines=[_line(100.0)], subtotal=100.0,
        fees=[FeeLine(label="Delivery fee", amount=25.0)], total=125.0,
    )
    assert summary.reconciles is True
    assert summary.reconciliation_error is None


def test_summary_tolerates_sub_paisa_float_drift():
    summary = CartSummary(
        provider="blinkit", lines=[_line(0.1)], subtotal=0.1,
        fees=[FeeLine(label="Handling", amount=0.2)], total=0.30000000000000004,
    )
    assert summary.reconciles is True


def test_missing_fee_line_fails_reconciliation():
    """A scraped fee we failed to read must surface as an error, never as a cheap cart."""
    summary = CartSummary(
        provider="zepto", lines=[_line(100.0)], subtotal=100.0,
        fees=[], total=132.0,
    )
    assert summary.reconciles is False
    assert "132" in summary.reconciliation_error


def test_discounts_are_negative_fee_lines():
    summary = CartSummary(
        provider="instamart", lines=[_line(500.0)], subtotal=500.0,
        fees=[FeeLine(label="Delivery fee", amount=30.0),
              FeeLine(label="Coupon SAVE50", amount=-50.0)],
        total=480.0,
    )
    assert summary.reconciles is True
    assert summary.total < summary.subtotal


def test_estimated_summary_skips_reconciliation():
    """Estimated totals are computed by us, so they reconcile by construction."""
    summary = CartSummary(
        provider="zepto", lines=[_line(100.0)], subtotal=100.0,
        fees=[FeeLine(label="Estimated delivery", amount=25.0)],
        total=125.0, estimated=True,
    )
    assert summary.estimated is True
    assert summary.reconciles is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cart_summary.py -v`
Expected: FAIL with `ImportError: cannot import name 'CartLine' from 'app.providers.base'`

- [ ] **Step 3: Write the implementation**

In `app/providers/base.py`, add after the existing `ProviderSafetyError` class:

```python
class CartReconciliationError(ProviderError):
    """A cart's reported total disagreed with its own line items and fees."""
```

Add these models after `ProviderAddress`:

```python
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
```

Add these methods to the `GroceryProvider` ABC, after `add_items`:

```python
    @abstractmethod
    async def cart_summary(self) -> CartSummary:
        """Read the provider's current cart, including real fees."""
        raise NotImplementedError

    async def clear_cart(self, *, operation_id: str) -> None:
        raise ProviderError(f"{self.display_name} cannot clear its cart yet.")
```

- [ ] **Step 4: Add stub implementations so existing providers still import**

`cart_summary` is abstract, so `BlinkitProvider` and `InstamartProvider` will fail to instantiate until they implement it. Add a temporary stub to **both** `app/providers/blinkit.py` and `app/providers/instamart.py` (real versions land in Tasks 4 and 5):

```python
    async def cart_summary(self) -> CartSummary:
        raise ProviderError(f"{self.display_name} cart reading is not implemented yet.")
```

Import `CartSummary` and `ProviderError` from `.base` in both files.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 43 passing (38 baseline + 5 new)

- [ ] **Step 6: Commit**

```bash
git add app/providers/base.py app/providers/blinkit.py app/providers/instamart.py tests/test_cart_summary.py
git commit -m "feat: add CartSummary models and cart read/clear provider interface"
```

---

### Task 2: Per-unit price and fill ratio (`app/units.py`)

**Files:**
- Create: `app/units.py`
- Test: `tests/test_units.py` (create)

**Interfaces:**
- Consumes: `parse_measurement`, `requested_measurement` from `app/constraints.py`
- Produces: `per_unit_price(product, units) -> tuple[float, str] | None`, `fill_ratio(item, product, units) -> float | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_units.py`:

```python
from app.models import PlannedItem, Product
from app.units import fill_ratio, per_unit_price


def _product(pack_size: str, price: float, name: str = "Test Product") -> Product:
    return Product(id="p1", name=name, pack_size=pack_size, price=price, handle="h1")


def _item(quantity: float, unit: str) -> PlannedItem:
    return PlannedItem(search_term="milk", quantity=quantity, unit=unit)


def test_per_unit_price_for_one_litre():
    assert per_unit_price(_product("1 L", 75.0), 1) == (0.075, "ml")


def test_per_unit_price_accounts_for_multiple_units():
    """Two 500 ml packs at ₹40 each is ₹80 for 1000 ml."""
    assert per_unit_price(_product("500 ml", 40.0), 2) == (0.08, "ml")


def test_per_unit_price_falls_back_to_name_when_pack_size_blank():
    assert per_unit_price(_product("", 50.0, name="Aashirvaad Atta 1 kg"), 1) == (0.05, "g")


def test_per_unit_price_unparseable_returns_none():
    """Never guess. An unparseable pack is not price-comparable."""
    assert per_unit_price(_product("1 combo", 99.0, name="Party Combo"), 1) is None


def test_per_unit_price_zero_units_returns_none():
    assert per_unit_price(_product("1 L", 75.0), 0) is None


def test_fill_ratio_exact_match():
    assert fill_ratio(_item(1, "l"), _product("1 L", 75.0), 1) == 1.0


def test_fill_ratio_detects_shortfall():
    """500 ml supplied against 1 L requested is half the basket."""
    assert fill_ratio(_item(1, "l"), _product("500 ml", 40.0), 1) == 0.5


def test_fill_ratio_dimension_mismatch_returns_none():
    """Grams cannot answer a request in litres."""
    assert fill_ratio(_item(1, "l"), _product("500 g", 40.0), 1) is None


def test_fill_ratio_unknown_request_returns_none():
    assert fill_ratio(_item(1, "item"), _product("1 L", 75.0), 1) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_units.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.units'`

- [ ] **Step 3: Write the implementation**

Create `app/units.py`:

```python
"""Per-unit price comparison built on the measurement parsing in constraints.py.

Only the price-per-unit layer lives here; all measurement parsing is reused
from `app.constraints` so the comparison and the single-platform draft agree
on what a pack size means.
"""

from __future__ import annotations

from .constraints import parse_measurement, requested_measurement
from .models import PlannedItem, Product


def _packed(product: Product) -> tuple[float, str] | None:
    return parse_measurement(product.pack_size or product.name)


def per_unit_price(product: Product, units: int) -> tuple[float, str] | None:
    """Price per gram / millilitre / count, or None when not comparable.

    Returns (price_per_unit, dimension). Never guesses: an unparseable pack
    size yields None so the caller can mark the item not price-comparable.
    """
    if units <= 0:
        return None
    packed = _packed(product)
    if not packed:
        return None
    amount, dimension = packed
    total_amount = amount * units
    if total_amount <= 0:
        return None
    return round(product.price * units / total_amount, 6), dimension


def fill_ratio(item: PlannedItem, product: Product, units: int) -> float | None:
    """Delivered quantity divided by requested quantity, or None if incomparable."""
    if units <= 0:
        return None
    requested = requested_measurement(item)
    packed = _packed(product)
    if not requested or not packed:
        return None
    if requested[1] != packed[1] or requested[0] <= 0:
        return None
    return round(packed[0] * units / requested[0], 4)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_units.py -v`
Expected: PASS, 9 passing

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 52 passing

- [ ] **Step 6: Commit**

```bash
git add app/units.py tests/test_units.py
git commit -m "feat: add per-unit price and fill ratio helpers"
```

---

### Task 3: Zepto and comparison configuration, fail-closed

**Files:**
- Modify: `app/config.py`
- Modify: `app/models.py:135` (`ProviderSelectionRequest.provider_id` literal)
- Test: `tests/test_zepto_safety.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: settings `zepto_base_url`, `zepto_profile_dir`, `zepto_cart_writes`, `min_fill_ratio`, `eta_tiebreak_rupees`; `cart_mutations_allowed_for("zepto")`

- [ ] **Step 1: Write the failing test**

Create `tests/test_zepto_safety.py` (mirrors `tests/test_instamart_safety.py`):

```python
from app.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, safety_lock=False, dry_run=False, demo_mode=False)
    return Settings(**{**base, **overrides})


def test_zepto_writes_blocked_by_default():
    """zepto_cart_writes defaults off, so writes fail closed."""
    assert _settings().cart_mutations_allowed_for("zepto") is False


def test_zepto_writes_allowed_when_explicitly_enabled():
    assert _settings(zepto_cart_writes=True).cart_mutations_allowed_for("zepto") is True


def test_safety_lock_overrides_zepto_opt_in():
    settings = _settings(zepto_cart_writes=True, safety_lock=True)
    assert settings.cart_mutations_allowed_for("zepto") is False


def test_dry_run_overrides_zepto_opt_in():
    settings = _settings(zepto_cart_writes=True, dry_run=True)
    assert settings.cart_mutations_allowed_for("zepto") is False


def test_demo_mode_overrides_zepto_opt_in():
    settings = _settings(zepto_cart_writes=True, demo_mode=True)
    assert settings.cart_mutations_allowed_for("zepto") is False


def test_zepto_opt_in_does_not_leak_to_blinkit():
    """Enabling Zepto writes must not enable any other provider."""
    settings = _settings(zepto_cart_writes=True)
    assert settings.cart_mutations_allowed_for("instamart") is False


def test_zepto_has_its_own_browser_profile_dir():
    """Two Playwright providers cannot share one Chromium SingletonLock."""
    settings = _settings()
    assert settings.zepto_profile_dir != settings.browser_profile_dir


def test_comparison_defaults_match_constraints_threshold():
    settings = _settings()
    assert settings.min_fill_ratio == 0.9
    assert settings.eta_tiebreak_rupees == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_zepto_safety.py -v`
Expected: FAIL — `cart_mutations_allowed_for("zepto")` returns `True` (no gate yet) and `zepto_profile_dir` does not exist.

- [ ] **Step 3: Write the implementation**

In `app/config.py`, change the provider literal on line 29:

```python
    grocery_provider: Literal["blinkit", "instamart", "zepto"] = "blinkit"
```

Add after the `blinkit_base_url` / profile settings block:

```python
    zepto_base_url: str = "https://www.zeptonow.com"
    zepto_profile_dir: Path = ROOT / "browser_profile_zepto"
    zepto_cart_writes: bool = False

    min_fill_ratio: float = Field(default=0.9, gt=0, le=1)
    eta_tiebreak_rupees: float = Field(default=20.0, ge=0)
```

Replace `cart_mutations_allowed_for` with a table-driven version:

```python
    def cart_mutations_allowed_for(self, provider_id: str) -> bool:
        """Fail closed unless global and provider-specific guards permit writes."""
        base_allowed = not self.safety_lock and not self.dry_run and not self.demo_mode
        provider_gates = {
            "instamart": self.instamart_cart_writes,
            "zepto": self.zepto_cart_writes,
        }
        return base_allowed and provider_gates.get(provider_id, True)
```

In `app/models.py:135`:

```python
class ProviderSelectionRequest(BaseModel):
    provider_id: Literal["blinkit", "instamart", "zepto"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_zepto_safety.py -v`
Expected: PASS, 8 passing

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 60 passing

- [ ] **Step 6: Update `.env.example`**

Append:

```
# Zepto (Playwright). Its own Chromium profile — cannot share Blinkit's.
ZEPTO_BASE_URL=https://www.zeptonow.com
ZEPTO_PROFILE_DIR=browser_profile_zepto
# Cart writes fail closed. Requires SAFETY_LOCK=false and DRY_RUN=false too.
ZEPTO_CART_WRITES=false

# Comparison tuning
MIN_FILL_RATIO=0.9
ETA_TIEBREAK_RUPEES=20
```

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/models.py tests/test_zepto_safety.py .env.example
git commit -m "feat: add Zepto and comparison settings with fail-closed cart writes"
```

---

### Task 4: Instamart cart summary and clear

**Files:**
- Modify: `app/providers/instamart.py`
- Test: `tests/test_instamart_cart.py` (create)

**Interfaces:**
- Consumes: `CartSummary`, `CartLine`, `FeeLine` (Task 1)
- Produces: `cart_summary_from_instamart(payload) -> CartSummary`; `InstamartProvider.cart_summary()`, `InstamartProvider.clear_cart(operation_id=...)`

**Note:** `get_cart` and `update_cart` are already on the allowlist in `app/providers/instamart_transport.py:16`. **This task must not add any MCP tool to that allowlist.** Clearing is `update_cart` with quantities zeroed.

- [ ] **Step 1: Spike — record a real `get_cart` payload**

Before writing the parser, capture the real shape. With Instamart connected, run:

```bash
.venv/bin/python -c "
import asyncio, json
from app.config import get_settings
from app.providers.instamart import InstamartProvider
async def main():
    p = InstamartProvider(get_settings())
    print(json.dumps(await p.transport.call_tool('get_cart'), indent=2)[:4000])
asyncio.run(main())
"
```

Save the output to `tests/fixtures/instamart_cart.json`. **If the payload contains no fee or bill breakdown**, that is the outcome the spec anticipates (Risk 6): implement `cart_summary` to return `estimated=True` with `fees=[]` and `raw_note="Instamart did not report a fee breakdown."`, and let Task 9's estimator supply fees. Record which case you hit in the commit message.

- [ ] **Step 2: Write the failing test**

Create `tests/test_instamart_cart.py`. Use the recorded fixture shape; the test below assumes the common `{"cart": {"items": [...], "bill": {...}}}` shape — **adjust the input dicts to match your recorded fixture, but keep the assertions**:

```python
from app.providers.instamart import cart_summary_from_instamart, zeroed_cart_update


def test_cart_summary_reads_lines_and_fees():
    payload = {
        "cart": {
            "items": [
                {"productId": "abc", "name": "Amul Taaza 1 L", "quantity": 2,
                 "price": 35.0, "total": 70.0},
            ],
            "bill": {
                "itemTotal": 70.0,
                "deliveryFee": 25.0,
                "handlingFee": 5.0,
                "discount": -10.0,
                "grandTotal": 90.0,
            },
            "etaMinutes": 12,
        }
    }

    summary = cart_summary_from_instamart(payload)

    assert summary.provider == "instamart"
    assert [line.name for line in summary.lines] == ["Amul Taaza 1 L"]
    assert summary.lines[0].quantity == 2
    assert summary.subtotal == 70.0
    assert summary.total == 90.0
    assert summary.delivery_eta_minutes == 12
    assert summary.reconciles is True


def test_cart_summary_of_empty_cart_is_zero():
    summary = cart_summary_from_instamart({"cart": {"items": [], "bill": {}}})
    assert summary.lines == []
    assert summary.total == 0
    assert summary.reconciles is True


def test_zeroed_cart_update_sets_every_quantity_to_zero():
    payload = {"cart": {"items": [
        {"productId": "abc", "quantity": 2},
        {"productId": "def", "quantity": 1},
    ]}}
    assert zeroed_cart_update(payload) == [
        {"product_id": "abc", "quantity": 0},
        {"product_id": "def", "quantity": 0},
    ]


def test_zeroed_cart_update_of_empty_cart_is_empty():
    assert zeroed_cart_update({"cart": {"items": []}}) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_instamart_cart.py -v`
Expected: FAIL with `ImportError: cannot import name 'cart_summary_from_instamart'`

- [ ] **Step 4: Write the implementation**

In `app/providers/instamart.py`, add module-level pure functions next to the existing `cart_items_from_instamart`:

```python
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


def cart_summary_from_instamart(payload: dict) -> CartSummary:
    """Convert a get_cart payload into a CartSummary. Pure; unit-tested."""
    cart = payload.get("cart", payload) or {}
    raw_items = cart.get("items", []) or []
    bill = cart.get("bill", {}) or {}

    lines: list[CartLine] = []
    for raw in raw_items:
        quantity = _integer(_first(raw, ("quantity", "qty"), 0))
        unit_price = _number(_first(raw, ("price", "unitPrice"))) or 0.0
        line_total = _number(_first(raw, ("total", "lineTotal")))
        lines.append(
            CartLine(
                product_id=str(_first(raw, ("productId", "product_id", "id"), "") or ""),
                name=str(_first(raw, NAME_KEYS, "") or "").strip(),
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total if line_total is not None else unit_price * quantity,
            )
        )

    subtotal = _number(_first(bill, ("itemTotal", "subTotal", "subtotal")))
    if subtotal is None:
        subtotal = round(sum(line.line_total for line in lines), 2)

    fees = [
        FeeLine(label=label, amount=value)
        for key, label in FEE_LABELS.items()
        if (value := _number(bill.get(key))) not in (None, 0)
    ]

    total = _number(_first(bill, ("grandTotal", "total", "payableAmount")))
    if total is None:
        total = round(subtotal + sum(fee.amount for fee in fees), 2)

    return CartSummary(
        provider="instamart",
        lines=lines,
        subtotal=round(subtotal, 2),
        fees=fees,
        total=round(total, 2),
        delivery_eta_minutes=_integer(_first(cart, ("etaMinutes", "eta"), 0)) or None,
        estimated=not bill,
        raw_note="" if bill else "Instamart did not report a fee breakdown.",
    )


def zeroed_cart_update(payload: dict) -> list[dict]:
    """Build the update_cart body that empties the cart."""
    cart = payload.get("cart", payload) or {}
    return [
        {"product_id": str(_first(raw, ("productId", "product_id", "id"), "") or ""), "quantity": 0}
        for raw in (cart.get("items", []) or [])
    ]
```

Import `CartLine`, `CartSummary`, `FeeLine` from `.base`.

Replace the Task 1 stub `cart_summary` on `InstamartProvider`:

```python
    async def cart_summary(self) -> CartSummary:
        async with self._cart_lock:
            payload = await self.transport.call_tool("get_cart")
        return cart_summary_from_instamart(payload)

    async def clear_cart(self, *, operation_id: str) -> None:
        if not self.settings.cart_mutations_allowed_for(self.provider_id):
            raise ProviderSafetyError(
                "Instamart cart writes are disabled, so the cart was not cleared."
            )
        async with self._cart_lock:
            payload = await self.transport.call_tool("get_cart")
            updates = zeroed_cart_update(payload)
            if not updates:
                return
            await self.transport.call_tool("update_cart", {"items": updates})
            remaining = cart_items_from_instamart(await self.transport.call_tool("get_cart"))
            if remaining:
                raise ProviderError(
                    "Instamart still reports items after clearing; review the cart in Swiggy."
                )
```

Match the exact `update_cart` argument key to the existing call at `app/providers/instamart.py:414` — reuse whatever key that call already uses.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_instamart_cart.py -v`
Expected: PASS, 4 passing

- [ ] **Step 6: Verify the allowlist did not grow**

Run: `.venv/bin/python -m pytest tests/test_instamart_safety.py -v`
Expected: PASS — no new MCP tool was introduced.

- [ ] **Step 7: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/providers/instamart.py tests/test_instamart_cart.py tests/fixtures/instamart_cart.json
git commit -m "feat: read and clear the Instamart cart via existing MCP tools"
```

---

### Task 5: Blinkit cart summary and clear

**Files:**
- Modify: `app/blinkit.py`, `app/providers/blinkit.py`
- Test: `tests/test_blinkit_cart.py` (create)

**Interfaces:**
- Consumes: `CartSummary`, `CartLine`, `FeeLine` (Task 1)
- Produces: `cart_summary_from_raw(raw, provider) -> CartSummary`; `BlinkitClient.cart_summary()`, `BlinkitClient.clear_cart()`

**Pattern:** exactly like `_products_from_raw` (`app/blinkit.py:79`) — `page.evaluate()` returns plain dicts, a pure function converts them. The pure function is what gets tested.

- [ ] **Step 1: Write the failing test**

Create `tests/test_blinkit_cart.py`:

```python
from app.blinkit import cart_summary_from_raw


def test_parses_lines_and_bill_from_cart_text():
    raw = {
        "lines": [
            {"text": "Amul Taaza Toned Milk\n1 L\n2 x ₹35\n₹70", "handle": "/prn/amul/1"},
            {"text": "Aashirvaad Atta\n1 kg\n₹60", "handle": "/prn/atta/2"},
        ],
        "billText": (
            "Item total\n₹130\nDelivery charge\n₹25\nHandling charge\n₹5\n"
            "Grand total\n₹160"
        ),
        "etaText": "Delivery in 11 minutes",
    }

    summary = cart_summary_from_raw(raw, provider="blinkit")

    assert [line.name for line in summary.lines] == [
        "Amul Taaza Toned Milk", "Aashirvaad Atta",
    ]
    assert summary.lines[0].quantity == 2
    assert summary.lines[0].line_total == 70.0
    assert summary.lines[1].quantity == 1
    assert summary.subtotal == 130.0
    assert {fee.label for fee in summary.fees} == {"Delivery charge", "Handling charge"}
    assert summary.total == 160.0
    assert summary.delivery_eta_minutes == 11
    assert summary.reconciles is True


def test_discount_line_is_negative():
    raw = {
        "lines": [{"text": "Milk\n1 L\n₹100", "handle": "h"}],
        "billText": "Item total\n₹100\nDelivery charge\n₹25\nDiscount\n-₹15\nGrand total\n₹110",
        "etaText": "",
    }
    summary = cart_summary_from_raw(raw, provider="blinkit")
    discount = next(fee for fee in summary.fees if fee.label == "Discount")
    assert discount.amount == -15.0
    assert summary.reconciles is True


def test_unreadable_fee_line_breaks_reconciliation_rather_than_lying():
    """The whole point: a missed fee must not look like a cheap cart."""
    raw = {
        "lines": [{"text": "Milk\n1 L\n₹100", "handle": "h"}],
        "billText": "Item total\n₹100\nGrand total\n₹132",
        "etaText": "",
    }
    summary = cart_summary_from_raw(raw, provider="blinkit")
    assert summary.reconciles is False
    assert "132" in summary.reconciliation_error


def test_empty_cart():
    summary = cart_summary_from_raw({"lines": [], "billText": "", "etaText": ""}, provider="blinkit")
    assert summary.lines == []
    assert summary.total == 0
    assert summary.reconciles is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_blinkit_cart.py -v`
Expected: FAIL with `ImportError: cannot import name 'cart_summary_from_raw'`

- [ ] **Step 3: Write the pure parser**

In `app/blinkit.py`, add near `_products_from_raw`:

```python
BILL_TOTAL_LABELS = {"item total", "items total", "subtotal", "sub total", "mrp total"}
GRAND_TOTAL_LABELS = {"grand total", "to pay", "total amount", "bill total"}
ETA_MINUTES_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)
SIGNED_PRICE_RE = re.compile(r"(-?)\s*₹\s*(-?[\d,]+(?:\.\d+)?)")
QUANTITY_RE = re.compile(r"(\d+)\s*[x×]\s*₹", re.IGNORECASE)


def _signed_price(text: str) -> float | None:
    match = SIGNED_PRICE_RE.search(text)
    if not match:
        return None
    value = float(match.group(2).replace(",", ""))
    return -abs(value) if match.group(1) == "-" or value < 0 else value


def _bill_pairs(bill_text: str) -> list[tuple[str, float]]:
    """Pair each bill label with the price on the following line."""
    lines = [line.strip() for line in bill_text.splitlines() if line.strip()]
    pairs: list[tuple[str, float]] = []
    for index, line in enumerate(lines):
        if "₹" in line:
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        amount = _signed_price(following)
        if amount is not None:
            pairs.append((line, amount))
    return pairs


def cart_summary_from_raw(raw: dict, *, provider: str) -> CartSummary:
    """Convert scraped cart text into a CartSummary. Pure; unit-tested."""
    lines: list[CartLine] = []
    for entry in raw.get("lines", []) or []:
        text = entry.get("text", "")
        line_total = _signed_price(text.splitlines()[-1] if text.splitlines() else "")
        if line_total is None:
            continue
        quantity_match = QUANTITY_RE.search(text)
        quantity = int(quantity_match.group(1)) if quantity_match else 1
        text_lines = [item.strip() for item in text.splitlines() if item.strip()]
        name = next(
            (item for item in text_lines if "₹" not in item and not PACK_RE.fullmatch(item)),
            "",
        )
        handle = entry.get("handle") or name
        lines.append(
            CartLine(
                product_id=hashlib.sha1(handle.encode("utf-8")).hexdigest()[:12],
                name=name,
                quantity=quantity,
                unit_price=round(line_total / quantity, 2) if quantity else line_total,
                line_total=line_total,
            )
        )

    pairs = _bill_pairs(raw.get("billText", "") or "")
    subtotal = next(
        (amount for label, amount in pairs if label.casefold() in BILL_TOTAL_LABELS),
        round(sum(line.line_total for line in lines), 2),
    )
    total = next(
        (amount for label, amount in pairs if label.casefold() in GRAND_TOTAL_LABELS),
        None,
    )
    fees = [
        FeeLine(label=label, amount=amount)
        for label, amount in pairs
        if label.casefold() not in BILL_TOTAL_LABELS
        and label.casefold() not in GRAND_TOTAL_LABELS
    ]
    if total is None:
        total = round(subtotal + sum(fee.amount for fee in fees), 2)

    eta_match = ETA_MINUTES_RE.search(raw.get("etaText", "") or "")
    return CartSummary(
        provider=provider,
        lines=lines,
        subtotal=round(subtotal, 2),
        fees=fees,
        total=round(total, 2),
        delivery_eta_minutes=int(eta_match.group(1)) if eta_match else None,
    )
```

Import `CartLine`, `CartSummary`, `FeeLine` from `.providers.base` at the top of `app/blinkit.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_blinkit_cart.py -v`
Expected: PASS, 4 passing

- [ ] **Step 5: Commit the pure layer before touching Playwright**

```bash
git add app/blinkit.py tests/test_blinkit_cart.py
git commit -m "feat: parse Blinkit cart bill into a reconciled CartSummary"
```

- [ ] **Step 6: Add the Playwright glue**

Selectors cannot be known without the live DOM. Open the cart with `BROWSER_HEADLESS=false`, inspect it, and fill the selectors below. Add to `BlinkitClient`:

```python
    async def cart_summary(self) -> CartSummary:
        await self.ensure_login()
        async with self._lock:
            page = await self._get_page()
            await page.goto(f"{self.settings.blinkit_base_url}/cart", wait_until="domcontentloaded")
            raw = await page.evaluate(
                """() => {
                  const lineNodes = [...document.querySelectorAll('SELECTOR_FOR_CART_ROW')];
                  const bill = document.querySelector('SELECTOR_FOR_BILL_PANEL');
                  const eta = document.querySelector('SELECTOR_FOR_ETA');
                  return {
                    lines: lineNodes.map(node => ({
                      text: (node.innerText || '').trim(),
                      handle: node.querySelector('a[href]')?.getAttribute('href') || '',
                    })),
                    billText: (bill?.innerText || '').trim(),
                    etaText: (eta?.innerText || '').trim(),
                  };
                }"""
            )
        return cart_summary_from_raw(raw, provider="blinkit")
```

Verify against the live cart:

```bash
BROWSER_HEADLESS=false .venv/bin/python -c "
import asyncio
from app.blinkit import BlinkitClient
from app.config import get_settings
async def main():
    c = BlinkitClient(get_settings())
    s = await c.cart_summary()
    print(s.model_dump_json(indent=2))
    print('reconciles:', s.reconciles, s.reconciliation_error or '')
    await c.close()
asyncio.run(main())
"
```

Expected: line items and bill match what the browser shows, and `reconciles: True`. **If it prints `False`, a fee line is being missed — fix the selector, do not adjust the tolerance.**

- [ ] **Step 7: Add `clear_cart` and wire the provider**

```python
    async def clear_cart(self, *, operation_id: str) -> None:
        if not self.settings.cart_mutations_allowed_for("blinkit"):
            raise ProviderSafetyError(
                "Blinkit cart writes are disabled, so the cart was not cleared."
            )
        await self.ensure_login()
        async with self._lock:
            page = await self._get_page()
            await page.goto(f"{self.settings.blinkit_base_url}/cart", wait_until="domcontentloaded")
            for _ in range(100):  # bounded: never loop forever on a stuck UI
                remove = page.locator("SELECTOR_FOR_DECREMENT_OR_REMOVE").first
                if not await remove.count():
                    break
                await remove.click()
                await page.wait_for_timeout(400)
        summary = await self.cart_summary()
        if summary.lines:
            raise ProviderError("The Blinkit cart still has items after clearing.")
```

In `app/providers/blinkit.py`, replace the Task 1 stub with delegations to `self.client.cart_summary()` and `self.client.clear_cart(...)`, following how `search` and `add_items` already delegate.

- [ ] **Step 8: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/blinkit.py app/providers/blinkit.py
git commit -m "feat: read and clear the Blinkit cart via Playwright"
```

---

### Task 6: Zepto provider — connection and search

**Files:**
- Create: `app/zepto.py`, `app/providers/zepto.py`
- Modify: `app/providers/factory.py`
- Test: `tests/test_zepto_products.py` (create)

**Interfaces:**
- Consumes: `Product`, `GroceryProvider`, settings from Task 3
- Produces: `zepto_products_from_raw(query, raw, limit, base_url) -> list[Product]`; `ZeptoClient`; `ZeptoProvider` registered as `"zepto"`

**This is the critical-path task.** Nothing downstream is trustworthy until it works. Build `app/zepto.py` as a close sibling of `app/blinkit.py` — same `_lock`, same `SingletonLock` handling, same `ensure_login` shape — but pointed at `settings.zepto_profile_dir`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_zepto_products.py`:

```python
from app.zepto import zepto_products_from_raw


def test_parses_product_cards():
    raw = [
        {
            "text": "Amul Taaza Toned Milk\n1 L\n₹75\n₹80\n6% OFF\nAdd",
            "href": "/pn/amul-taaza/pvid/abc123",
            "image": "https://cdn.zeptonow.com/milk.png",
            "addText": "Add",
        },
        {
            "text": "Mother Dairy Milk\n500 ml\n₹42\nOut of stock",
            "href": "/pn/mother-dairy/pvid/def456",
            "image": "https://cdn.zeptonow.com/md.png",
            "addText": "Out of stock",
        },
    ]

    products = zepto_products_from_raw("milk", raw, limit=5,
                                       base_url="https://www.zeptonow.com")

    assert [p.name for p in products] == ["Amul Taaza Toned Milk", "Mother Dairy Milk"]
    assert products[0].pack_size == "1 L"
    assert products[0].price == 75.0
    assert products[0].mrp == 80.0
    assert products[0].discount_percent == 6.0
    assert products[0].in_stock is True
    assert products[0].product_url == "https://www.zeptonow.com/pn/amul-taaza/pvid/abc123"
    assert products[1].in_stock is False


def test_ids_are_stable_across_calls():
    raw = [{"text": "Milk\n1 L\n₹75\nAdd", "href": "/pn/x/pvid/1",
            "image": "", "addText": "Add"}]
    first = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    second = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    assert first[0].id == second[0].id


def test_cards_without_price_are_skipped():
    raw = [{"text": "Category banner", "href": "", "image": "", "addText": ""}]
    assert zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com") == []


def test_limit_is_respected():
    raw = [
        {"text": f"Milk {i}\n1 L\n₹{70 + i}\nAdd", "href": f"/pn/x/pvid/{i}",
         "image": "", "addText": "Add"}
        for i in range(10)
    ]
    assert len(zepto_products_from_raw("milk", raw, limit=3,
                                       base_url="https://www.zeptonow.com")) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_zepto_products.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.zepto'`

- [ ] **Step 3: Write the pure parser**

Create `app/zepto.py` with the same imports and regex constants as `app/blinkit.py` (`PRICE_RE`, `PACK_RE`), and:

```python
def zepto_products_from_raw(
    query: str,
    raw_products: list[dict],
    limit: int,
    *,
    base_url: str = "https://www.zeptonow.com",
) -> list[Product]:
    products: list[Product] = []
    for raw in raw_products:
        text = raw.get("text", "")
        price_matches = list(PRICE_RE.finditer(text))
        if not price_matches:
            continue
        prices = [float(m.group(1).replace(",", "")) for m in price_matches]
        price = prices[0]
        mrp = next((value for value in prices[1:] if value > price), None)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        name = next(
            (line for line in lines if "₹" not in line and not PACK_RE.fullmatch(line)),
            query,
        )
        pack_match = PACK_RE.search(text)
        href = raw.get("href") or ""
        handle = href or raw.get("image") or f"{query}|{name}|{price}"
        discount_match = re.search(r"(\d+(?:\.\d+)?)%\s*OFF", text, re.I)
        eta_match = re.search(r"(\d+)\s*MINS?", text, re.I)
        add_text = raw.get("addText", "")
        products.append(
            Product(
                id=hashlib.sha1(f"zepto|{handle}".encode("utf-8")).hexdigest()[:12],
                name=name,
                pack_size=pack_match.group(0) if pack_match else "",
                price=price,
                mrp=mrp,
                discount_percent=float(discount_match.group(1)) if discount_match else 0,
                delivery_minutes=int(eta_match.group(1)) if eta_match else None,
                image_url=raw.get("image") or None,
                in_stock=not re.search(r"out of stock|notify|sold out", add_text, re.I),
                handle=handle,
                product_url=urljoin(base_url, href) if href else None,
                search_query=query,
            )
        )
    return products[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_zepto_products.py -v`
Expected: PASS, 4 passing

- [ ] **Step 5: Commit the pure layer**

```bash
git add app/zepto.py tests/test_zepto_products.py
git commit -m "feat: add Zepto product parser"
```

- [ ] **Step 6: Add `ZeptoClient` with login and search**

Copy the structural skeleton of `BlinkitClient` (`app/blinkit.py:142-300`) into `app/zepto.py`: `__init__` storing settings and `asyncio.Lock`, `_open_persistent_context`, `_singleton_lock_pid`, `_remove_stale_lock`, `_get_page`, `ensure_login`, `close`. Substitute `self.settings.zepto_profile_dir` for `browser_profile_dir` and `self.settings.zepto_base_url` for `blinkit_base_url`. Then add `search(query)` following `app/blinkit.py:381`, filling the `page.evaluate` selectors from the live DOM and calling `zepto_products_from_raw`.

- [ ] **Step 7: Create the provider wrapper and register it**

Create `app/providers/zepto.py` mirroring `app/providers/blinkit.py` exactly: `provider_id = "zepto"`, `display_name = "Zepto"`, delegating `status`, `connect`, `search`, `add_items`, `cart_summary`, `clear_cart`, `close` to `ZeptoClient`.

In `app/providers/factory.py`:

```python
def create_providers(settings: Settings) -> dict[str, GroceryProvider]:
    """Create every supported provider; the setting only chooses the default."""
    return {
        "blinkit": BlinkitProvider(settings),
        "instamart": InstamartProvider(settings),
        "zepto": ZeptoProvider(settings),
    }
```

- [ ] **Step 8: Verify search against the live site**

```bash
BROWSER_HEADLESS=false .venv/bin/python -c "
import asyncio
from app.config import get_settings
from app.providers.zepto import ZeptoProvider
async def main():
    p = ZeptoProvider(get_settings())
    for prod in await p.search('milk'):
        print(prod.name, prod.pack_size, prod.price, prod.in_stock)
    await p.close()
asyncio.run(main())
"
```

Expected: real Zepto products with sane names, pack sizes, and prices. Log in through the headful window on first run.

- [ ] **Step 9: Confirm both browsers can run at once**

Run the Blinkit and Zepto search snippets concurrently. Expected: no `SingletonLock` conflict — they use different profile dirs. If it conflicts, `zepto_profile_dir` is misconfigured.

- [ ] **Step 10: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/zepto.py app/providers/zepto.py app/providers/factory.py
git commit -m "feat: add Zepto provider with login and search"
```

---

### Task 7: Zepto cart add, read, and clear

**Files:**
- Modify: `app/zepto.py`, `app/providers/zepto.py`
- Test: `tests/test_zepto_cart.py` (create)

**Interfaces:**
- Consumes: `cart_summary_from_raw` (Task 5), `ZeptoClient` (Task 6)
- Produces: `ZeptoClient.add_to_cart`, `ZeptoClient.cart_summary`, `ZeptoClient.clear_cart`

- [ ] **Step 1: Write the failing test**

`cart_summary_from_raw` from Task 5 is provider-agnostic and is reused here. Create `tests/test_zepto_cart.py`:

```python
import asyncio

import pytest

from app.blinkit import cart_summary_from_raw
from app.config import Settings
from app.models import Product
from app.providers.base import ProviderSafetyError
from app.zepto import ZeptoClient


def test_zepto_cart_text_parses_with_shared_parser():
    raw = {
        "lines": [{"text": "Amul Taaza\n1 L\n2 x ₹38\n₹76", "handle": "/pn/amul/pvid/1"}],
        "billText": "Item total\n₹76\nDelivery charge\n₹29\nGrand total\n₹105",
        "etaText": "10 mins",
    }
    summary = cart_summary_from_raw(raw, provider="zepto")
    assert summary.provider == "zepto"
    assert summary.total == 105.0
    assert summary.delivery_eta_minutes == 10
    assert summary.reconciles is True


def test_clear_cart_refuses_when_writes_disabled(tmp_path):
    """Fail closed: no browser is even opened."""
    client = ZeptoClient(Settings(_env_file=None, zepto_profile_dir=tmp_path))
    with pytest.raises(ProviderSafetyError):
        asyncio.run(client.clear_cart(operation_id="op1"))


def test_add_to_cart_refuses_when_writes_disabled(tmp_path):
    client = ZeptoClient(Settings(_env_file=None, zepto_profile_dir=tmp_path))
    product = Product(id="p1", name="Milk", price=75.0, handle="/pn/x/pvid/1")
    with pytest.raises(ProviderSafetyError):
        asyncio.run(client.add_to_cart(product, 1))
```

**Note:** this repo has no `pytest-asyncio`. Async code is tested with
`asyncio.run(...)` inside sync test functions — see `tests/test_safety.py:31`
and `tests/test_instamart_provider.py:86`. Follow that convention everywhere.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_zepto_cart.py -v`
Expected: FAIL — `clear_cart` / `add_to_cart` are not defined on `ZeptoClient`.

- [ ] **Step 3: Implement the three cart methods**

Add to `ZeptoClient`, following `BlinkitClient.add_to_cart` (`app/blinkit.py:459`) and the Task 5 cart methods. Every one begins with the safety gate **before** opening a browser:

```python
        if not self.settings.cart_mutations_allowed_for("zepto"):
            raise ProviderSafetyError(
                "Zepto cart writes are disabled, so the cart was not changed."
            )
```

`cart_summary()` needs no gate — it is read-only. It navigates to the Zepto cart URL, runs the same `page.evaluate` shape as Task 5 (`{lines, billText, etaText}`) with Zepto's selectors, and returns `cart_summary_from_raw(raw, provider="zepto")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_zepto_cart.py -v`
Expected: PASS, 3 passing

- [ ] **Step 5: Verify live in dry run, then with writes**

First confirm the safety gate holds with defaults, then enable writes deliberately and verify add → read → clear against the real site, checking `reconciles: True` at each step.

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/zepto.py app/providers/zepto.py tests/test_zepto_cart.py
git commit -m "feat: add Zepto cart add, read, and clear"
```

---

### Task 8: Cross-platform joint matching

**Files:**
- Modify: `app/matcher.py`, `app/models.py`
- Test: `tests/test_cross_platform_matcher.py` (create)

**Interfaces:**
- Consumes: `MatchDecision`, `Product`, `PlannedItem`
- Produces: `CrossPlatformMatch`; `match_across_platforms(item, candidates_by_provider, settings) -> CrossPlatformMatch`

Keep the existing `match_product` — `/api/search` still uses it for single-item re-search.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cross_platform_matcher.py`:

```python
from app.config import Settings
from app.matcher import match_across_platforms
from app.models import PlannedItem, Product


def _settings() -> Settings:
    # safety_lock keeps the deterministic fallback path active, no network.
    return Settings(_env_file=None, safety_lock=True)


def _product(pid: str, name: str, pack: str, price: float) -> Product:
    return Product(id=pid, name=name, pack_size=pack, price=price, handle=pid)


def test_picks_one_product_per_platform():
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    result = match_across_platforms(
        item,
        {
            "blinkit": [_product("b1", "Amul Taaza Toned Milk", "1 L", 75.0)],
            "zepto": [_product("z1", "Amul Taaza Toned Milk", "1 L", 72.0)],
        },
        _settings(),
    )
    assert set(result.picks) == {"blinkit", "zepto"}
    assert result.picks["blinkit"].product_id == "b1"
    assert result.picks["zepto"].product_id == "z1"


def test_platform_with_no_candidates_reports_no_equivalent():
    item = PlannedItem(search_term="paneer", quantity=200, unit="g")
    result = match_across_platforms(
        item,
        {"blinkit": [_product("b1", "Amul Paneer", "200 g", 95.0)], "zepto": []},
        _settings(),
    )
    assert result.picks["zepto"].product_id is None
    assert result.picks["zepto"].units_to_add == 0
    assert "zepto" in result.equivalence_note.lower()


def test_out_of_stock_only_platform_reports_no_equivalent():
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    out_of_stock = _product("z1", "Amul Taaza", "1 L", 72.0)
    out_of_stock.in_stock = False
    result = match_across_platforms(
        item,
        {"blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)], "zepto": [out_of_stock]},
        _settings(),
    )
    assert result.picks["zepto"].product_id is None


def test_units_scale_to_the_pack_size_each_platform_stocks():
    """1 L requested: one 1 L pack on Blinkit, two 500 ml packs on Zepto."""
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    result = match_across_platforms(
        item,
        {
            "blinkit": [_product("b1", "Amul Taaza", "1 L", 75.0)],
            "zepto": [_product("z1", "Amul Taaza", "500 ml", 40.0)],
        },
        _settings(),
    )
    assert result.picks["blinkit"].units_to_add == 1
    assert result.picks["zepto"].units_to_add == 2


def test_empty_provider_map_returns_empty_picks():
    item = PlannedItem(search_term="milk", quantity=1, unit="l")
    assert match_across_platforms(item, {}, _settings()).picks == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cross_platform_matcher.py -v`
Expected: FAIL with `ImportError: cannot import name 'match_across_platforms'`

- [ ] **Step 3: Add the model**

In `app/models.py`, after `MatchDecision`:

```python
class CrossPlatformMatch(BaseModel):
    """One planned item resolved against every platform at once."""

    picks: dict[str, MatchDecision] = Field(default_factory=dict)
    equivalence_note: str = ""
```

- [ ] **Step 4: Implement `match_across_platforms`**

In `app/matcher.py`:

```python
CROSS_MATCHER_SYSTEM = """You match one planned grocery item against candidates from
several Indian instant-delivery platforms at once. Prefer the SAME brand and pack size
on every platform so their prices are comparable. Only choose in-stock candidate ids
from that platform's own list. If a platform has no reasonable equivalent, set its
product_id to null and units_to_add to 0 — do not force a poor match.
Compute purchasable units, not loose quantity: 12 eggs is one 12-count tray.

Schema: {"picks": {"<provider>": {"product_id": "id or null", "units_to_add": 1,
"reason": "short reason"}}, "equivalence_note": "one short sentence"}
"""


def match_across_platforms(
    item: PlannedItem,
    candidates_by_provider: dict[str, list[Product]],
    settings: Settings,
) -> CrossPlatformMatch:
    """Pick a comparable product on each platform, or report no equivalent."""
    if not candidates_by_provider:
        return CrossPlatformMatch()

    if settings.demo_mode or settings.safety_lock or settings.model_backend == "local":
        return _fallback_cross_match(item, candidates_by_provider)

    payload = {
        provider: [
            {
                "id": product.id, "name": product.name, "pack_size": product.pack_size,
                "price": product.price, "in_stock": product.in_stock,
            }
            for product in candidates
        ]
        for provider, candidates in candidates_by_provider.items()
    }
    prompt = (
        f"Planned item: {item.model_dump_json()}\n"
        f"Candidates by platform: {payload}\n"
        "Pick the most comparable product on each platform."
    )
    try:
        raw = HFModelClient(settings).complete_json(
            model=settings.matcher_model,
            system=CROSS_MATCHER_SYSTEM,
            prompt=prompt,
            max_tokens=800,
        )
    except ModelBackendError:
        if not settings.local_vision_fallback:
            raise
        return _fallback_cross_match(item, candidates_by_provider)

    try:
        result = CrossPlatformMatch.model_validate(raw)
    except ValidationError:
        # A malformed shape is recoverable: fall back rather than fail the run.
        return _fallback_cross_match(item, candidates_by_provider)

    # Never trust the model's ids: drop any pick that is not a real in-stock candidate.
    for provider, candidates in candidates_by_provider.items():
        valid = {p.id for p in candidates if p.in_stock}
        decision = result.picks.get(provider)
        if decision is None or decision.product_id not in valid:
            result.picks[provider] = _fallback_match(item, candidates)
    return result


def _fallback_cross_match(
    item: PlannedItem,
    candidates_by_provider: dict[str, list[Product]],
) -> CrossPlatformMatch:
    picks = {
        provider: _fallback_match(item, candidates)
        for provider, candidates in candidates_by_provider.items()
    }
    missing = sorted(p for p, d in picks.items() if d.product_id is None)
    note = (
        f"No equivalent found on {', '.join(missing)}."
        if missing
        else "Matched independently on each platform."
    )
    return CrossPlatformMatch(picks=picks, equivalence_note=note)
```

Import `CrossPlatformMatch` from `.models` and `ValidationError` from `pydantic`. Note `_fallback_match` already returns `product_id=None, units_to_add=0` for an empty or fully out-of-stock candidate list (`app/matcher.py:112`), which satisfies the "no equivalent" tests.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cross_platform_matcher.py -v`
Expected: PASS, 5 passing

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/matcher.py app/models.py tests/test_cross_platform_matcher.py
git commit -m "feat: match one item across all platforms in a single decision"
```

---

### Task 9: Comparison engine (`app/compare.py`)

**Files:**
- Create: `app/compare.py`
- Modify: `app/models.py`
- Test: `tests/test_compare.py` (create)

**Interfaces:**
- Consumes: `CartSummary`, `CartLine`, `FeeLine` (Task 1); `per_unit_price`, `fill_ratio` (Task 2); `DraftCart`, `DraftItem` (existing)
- Produces: `Substitution`, `PlatformOutcome`, `ComparisonReport`; `build_outcome(...)`, `estimated_summary(...)`, `rank(outcomes, settings) -> ComparisonReport`

This is the heart of the feature and it is entirely pure functions. Test it hard.

- [ ] **Step 1: Add the models**

In `app/models.py`:

```python
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
```

Import `CartSummary` from `.providers.base` in `app/models.py`. **If this creates a circular import** (`providers.base` already imports from `.models`), move `FeeLine`/`CartLine`/`CartSummary` from `app/providers/base.py` into `app/models.py` in Task 1's place and re-export them from `base` — update Task 1's imports accordingly and re-run its tests.

- [ ] **Step 2: Write the failing test**

Create `tests/test_compare.py`:

```python
import pytest

from app.compare import rank
from app.config import Settings
from app.models import ComparisonReport, PlatformOutcome
from app.providers.base import CartLine, CartSummary, FeeLine


def _settings() -> Settings:
    return Settings(_env_file=None)


def _summary(provider: str, total: float, eta: int | None = 10) -> CartSummary:
    return CartSummary(
        provider=provider,
        lines=[CartLine(product_id="p", name="Milk", quantity=1,
                        unit_price=total - 25, line_total=total - 25)],
        subtotal=total - 25,
        fees=[FeeLine(label="Delivery fee", amount=25.0)],
        total=total,
        delivery_eta_minutes=eta,
    )


def _outcome(provider: str, total: float, **kwargs) -> PlatformOutcome:
    return PlatformOutcome(
        provider=provider, display_name=provider.title(),
        summary=_summary(provider, total, kwargs.pop("eta", 10)),
        matched_items=kwargs.pop("matched_items", 3), **kwargs,
    )


def test_cheapest_full_coverage_platform_wins():
    report = rank([_outcome("blinkit", 300.0), _outcome("zepto", 265.0)], _settings())
    assert report.winner == "zepto"
    assert report.ranking == ["zepto", "blinkit"]


def test_full_coverage_beats_a_cheaper_cart_with_missing_items():
    """A cheap cart missing an item is not the winner."""
    report = rank(
        [
            _outcome("blinkit", 300.0),
            _outcome("zepto", 200.0, missing_items=["paneer"]),
        ],
        _settings(),
    )
    assert report.winner == "blinkit"
    assert any("paneer" in reason for reason in report.reasons)


def test_short_packs_demote_below_full_coverage():
    """Cheaper because the packs are smaller is not cheaper."""
    report = rank(
        [
            _outcome("blinkit", 300.0),
            _outcome("zepto", 250.0, partial_items=["milk"]),
        ],
        _settings(),
    )
    assert report.winner == "blinkit"


def test_partial_still_beats_missing():
    report = rank(
        [
            _outcome("blinkit", 400.0, missing_items=["atta"]),
            _outcome("zepto", 450.0, partial_items=["milk"]),
        ],
        _settings(),
    )
    assert report.ranking == ["zepto", "blinkit"]


def test_eta_breaks_ties_within_the_price_band():
    """Within ₹20, the faster platform wins."""
    report = rank(
        [_outcome("blinkit", 300.0, eta=25), _outcome("zepto", 295.0, eta=9)],
        _settings(),
    )
    assert report.winner == "zepto"
    report_reversed = rank(
        [_outcome("blinkit", 295.0, eta=25), _outcome("zepto", 300.0, eta=9)],
        _settings(),
    )
    assert report_reversed.winner == "zepto"


def test_price_gap_beyond_the_band_ignores_eta():
    report = rank(
        [_outcome("blinkit", 200.0, eta=30), _outcome("zepto", 300.0, eta=8)],
        _settings(),
    )
    assert report.winner == "blinkit"


def test_failed_platform_is_never_ranked_and_keeps_its_error():
    report = rank(
        [
            _outcome("blinkit", 300.0),
            PlatformOutcome(provider="zepto", display_name="Zepto", status="failed",
                            error="Zepto search timed out."),
        ],
        _settings(),
    )
    assert report.winner == "blinkit"
    assert "zepto" not in report.ranking
    zepto = next(p for p in report.platforms if p.provider == "zepto")
    assert zepto.error == "Zepto search timed out."
    assert any("Zepto search timed out." in reason for reason in report.reasons)


def test_not_connected_platform_is_reported_not_dropped():
    report = rank(
        [
            _outcome("blinkit", 300.0),
            PlatformOutcome(provider="zepto", display_name="Zepto",
                            status="not_connected", error="Zepto is not connected."),
        ],
        _settings(),
    )
    assert {p.provider for p in report.platforms} == {"blinkit", "zepto"}
    assert report.ranking == ["blinkit"]


def test_all_platforms_failed_yields_no_winner():
    report = rank(
        [
            PlatformOutcome(provider="blinkit", display_name="Blinkit",
                            status="failed", error="boom"),
            PlatformOutcome(provider="zepto", display_name="Zepto",
                            status="failed", error="bang"),
        ],
        _settings(),
    )
    assert report.winner is None
    assert report.ranking == []
    assert report.reasons


def test_empty_input_yields_no_winner():
    report = rank([], _settings())
    assert isinstance(report, ComparisonReport)
    assert report.winner is None


def test_report_is_estimated_when_any_summary_is_estimated():
    estimated = _outcome("zepto", 250.0)
    estimated.summary.estimated = True
    report = rank([_outcome("blinkit", 300.0), estimated], _settings())
    assert report.estimated is True


def test_unreconciled_summary_is_disqualified():
    """A cart whose fees do not add up must not win on a wrong number."""
    broken = _outcome("zepto", 250.0)
    broken.summary.total = 999.0  # lines + fees no longer add up
    report = rank([_outcome("blinkit", 300.0), broken], _settings())
    assert report.winner == "blinkit"
    assert "zepto" not in report.ranking


def test_reasons_explain_the_price_gap():
    report = rank([_outcome("blinkit", 300.0), _outcome("zepto", 265.0)], _settings())
    assert any("₹35" in reason for reason in report.reasons)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_compare.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.compare'`

- [ ] **Step 4: Write the implementation**

Create `app/compare.py`:

```python
"""Deterministic comparison of the same basket across platforms.

No LLM. Arithmetic is the one thing that must be exactly right, and every
number and reason here is traceable to a rule.
"""

from __future__ import annotations

from .config import Settings
from .models import (
    ComparisonReport,
    DraftCart,
    PlatformOutcome,
    Substitution,
)
from .providers.base import CartLine, CartSummary, FeeLine
from .units import fill_ratio, per_unit_price


# Fees used only when cart writes are disabled and we cannot read a real cart.
FEE_ESTIMATES: dict[str, list[FeeLine]] = {
    "blinkit": [FeeLine(label="Estimated delivery fee", amount=25.0),
                FeeLine(label="Estimated handling fee", amount=5.0)],
    "zepto": [FeeLine(label="Estimated delivery fee", amount=29.0),
              FeeLine(label="Estimated handling fee", amount=5.0)],
    "instamart": [FeeLine(label="Estimated delivery fee", amount=30.0),
                  FeeLine(label="Estimated handling fee", amount=5.0)],
}


def estimated_summary(provider_id: str, draft: DraftCart) -> CartSummary:
    """Build a clearly-labelled estimated summary when carts cannot be written."""
    lines = [
        CartLine(
            product_id=product.id,
            name=product.name,
            quantity=item.units_to_add,
            unit_price=product.price,
            line_total=round(product.price * item.units_to_add, 2),
        )
        for item in draft.items
        if not item.removed and (product := item.selected_product) is not None
    ]
    subtotal = round(sum(line.line_total for line in lines), 2)
    fees = FEE_ESTIMATES.get(provider_id, [])
    return CartSummary(
        provider=provider_id,
        lines=lines,
        subtotal=subtotal,
        fees=list(fees),
        total=round(subtotal + sum(fee.amount for fee in fees), 2),
        estimated=True,
        raw_note="Fees are estimates because cart writes are disabled.",
    )


def build_outcome(
    provider_id: str,
    display_name: str,
    draft: DraftCart,
    summary: CartSummary | None,
    settings: Settings,
    *,
    status: str = "ok",
    error: str = "",
) -> PlatformOutcome:
    """Fold a platform's draft and cart summary into a comparable outcome."""
    if status != "ok":
        return PlatformOutcome(
            provider=provider_id, display_name=display_name,
            status=status, error=error,
        )

    matched = 0
    partial: list[str] = []
    missing: list[str] = []
    substitutions: list[Substitution] = []

    for item in draft.items:
        if item.removed:
            continue
        label = item.planned.raw_text or item.planned.search_term
        product = item.selected_product
        if product is None or item.units_to_add < 1:
            missing.append(label)
            continue
        ratio = fill_ratio(item.planned, product, item.units_to_add)
        if ratio is not None and ratio < settings.min_fill_ratio:
            partial.append(label)
            unit_price = per_unit_price(product, item.units_to_add)
            substitutions.append(
                Substitution(
                    item=label,
                    requested=f"{item.planned.quantity:g} {item.planned.unit}",
                    supplied=f"{item.units_to_add} × {product.pack_size or product.name}",
                    reason=f"Supplies only {ratio:.0%} of the requested amount.",
                    per_unit_delta=unit_price[0] if unit_price else None,
                )
            )
        else:
            matched += 1

    return PlatformOutcome(
        provider=provider_id,
        display_name=display_name,
        status="ok",
        summary=summary,
        matched_items=matched,
        partial_items=partial,
        missing_items=missing,
        substitutions=substitutions,
    )


def _rankable(outcome: PlatformOutcome) -> bool:
    """Only platforms with a trustworthy, reconciled cart may be ranked."""
    return (
        outcome.status == "ok"
        and outcome.summary is not None
        and outcome.summary.reconciles
    )


def rank(outcomes: list[PlatformOutcome], settings: Settings) -> ComparisonReport:
    """Rank platforms lexicographically: coverage tier, then real total, then ETA."""
    reasons: list[str] = []

    for outcome in outcomes:
        if outcome.status != "ok" and outcome.error:
            reasons.append(f"{outcome.display_name}: {outcome.error}")
        elif outcome.summary is not None and not outcome.summary.reconciles:
            reasons.append(
                f"{outcome.display_name} was excluded — "
                f"{outcome.summary.reconciliation_error}"
            )

    rankable = [outcome for outcome in outcomes if _rankable(outcome)]
    if not rankable:
        if not reasons:
            reasons.append("No platform produced a comparable cart.")
        return ComparisonReport(platforms=outcomes, winner=None, ranking=[],
                                reasons=reasons, estimated=False)

    ordered = sorted(
        rankable,
        key=lambda outcome: (outcome.coverage_tier, outcome.summary.total),
    )

    # ETA tiebreak: within the price band, prefer the faster platform.
    best = ordered[0]
    band = [
        outcome
        for outcome in ordered
        if outcome.coverage_tier == best.coverage_tier
        and outcome.summary.total - best.summary.total <= settings.eta_tiebreak_rupees
    ]
    if len(band) > 1:
        fastest = min(
            band,
            key=lambda outcome: (
                outcome.summary.delivery_eta_minutes
                if outcome.summary.delivery_eta_minutes is not None
                else 10**6
            ),
        )
        if fastest is not best:
            ordered.remove(fastest)
            ordered.insert(0, fastest)
            reasons.append(
                f"{fastest.display_name} wins the tiebreak: within "
                f"₹{settings.eta_tiebreak_rupees:.0f} of the cheapest and arrives sooner."
            )
        best = ordered[0]

    if len(ordered) > 1:
        runner_up = ordered[1]
        gap = round(runner_up.summary.total - best.summary.total, 2)
        if gap > 0:
            reasons.append(
                f"{best.display_name} is ₹{gap:g} cheaper than {runner_up.display_name}."
            )

    for outcome in ordered:
        if outcome.missing_items:
            reasons.append(
                f"{outcome.display_name} is missing: {', '.join(outcome.missing_items)}."
            )
        if outcome.partial_items:
            reasons.append(
                f"{outcome.display_name} supplies short packs for: "
                f"{', '.join(outcome.partial_items)}."
            )

    return ComparisonReport(
        platforms=outcomes,
        winner=best.provider,
        ranking=[outcome.provider for outcome in ordered],
        reasons=reasons,
        estimated=any(
            outcome.summary.estimated for outcome in outcomes if outcome.summary
        ),
    )
```

Remove the vestigial no-op loop near the end if it survives your edit — it does nothing and should not ship.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_compare.py -v`
Expected: PASS, 13 passing

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/compare.py app/models.py tests/test_compare.py
git commit -m "feat: add deterministic multi-platform comparison engine"
```

---

### Task 10: Parallel orchestrator

**Files:**
- Create: `app/orchestrator.py`
- Test: `tests/test_orchestrator.py` (create)

**Interfaces:**
- Consumes: `match_across_platforms` (Task 8), `build_outcome` / `estimated_summary` (Task 9), `enforce_constraints` (existing), `GroceryProvider`
- Produces: `run_comparison(plan, providers, settings, on_event) -> ComparisonReport`, `preflight(providers, settings) -> list[PreflightPlatform]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator.py` with a fake provider so no browser or network is touched:

```python
import asyncio

from app.config import Settings
from app.models import CartConstraints, CartPlan, PlannedItem, Product
from app.orchestrator import run_comparison
from app.providers.base import CartLine, CartSummary, FeeLine, ProviderError


class FakeProvider:
    def __init__(self, provider_id, price, *, fail_on=None, eta=10):
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self.price = price
        self.fail_on = fail_on
        self.eta = eta
        self.cleared = False
        self.added = []

    async def search(self, query):
        if self.fail_on == "search":
            raise ProviderError(f"{self.provider_id} search failed")
        return [Product(id=f"{self.provider_id}-1", name="Amul Taaza",
                        pack_size="1 L", price=self.price, handle="h")]

    async def add_items(self, selections, *, operation_id):
        self.added = list(selections)
        return []

    async def cart_summary(self):
        if self.fail_on == "cart":
            raise ProviderError(f"{self.provider_id} cart failed")
        subtotal = sum(p.price * q for p, q in self.added) or self.price
        return CartSummary(
            provider=self.provider_id,
            lines=[CartLine(product_id="x", name="Amul Taaza", quantity=1,
                            unit_price=subtotal, line_total=subtotal)],
            subtotal=subtotal,
            fees=[FeeLine(label="Delivery fee", amount=25.0)],
            total=subtotal + 25.0,
            delivery_eta_minutes=self.eta,
        )

    async def clear_cart(self, *, operation_id):
        self.cleared = True

    async def close(self):
        return None


def _plan():
    return CartPlan(items=[PlannedItem(search_term="milk", quantity=1, unit="l")],
                    constraints=CartConstraints())


def _settings():
    return Settings(_env_file=None, safety_lock=True)


def test_cheaper_platform_wins_across_providers():
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0)}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner == "zepto"


def test_one_provider_failing_does_not_sink_the_run():
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0, fail_on="search")}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner == "blinkit"
    zepto = next(p for p in report.platforms if p.provider == "zepto")
    assert zepto.status == "failed"
    assert "search failed" in zepto.error


def test_cart_read_failure_is_reported_as_failed():
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0, fail_on="cart")}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner == "blinkit"


def test_all_providers_failing_yields_no_winner():
    providers = {"blinkit": FakeProvider("blinkit", 75.0, fail_on="search"),
                 "zepto": FakeProvider("zepto", 68.0, fail_on="search")}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.winner is None


def test_events_are_emitted_per_platform():
    events = []
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0)}
    asyncio.run(run_comparison(_plan(), providers, _settings(),
                               on_event=events.append))
    assert {e.data.get("provider") for e in events if e.data} >= {"blinkit", "zepto"}


def test_dry_run_produces_estimated_totals_without_writing():
    """With writes disabled, nothing is added and totals are labelled estimated."""
    providers = {"blinkit": FakeProvider("blinkit", 75.0),
                 "zepto": FakeProvider("zepto", 68.0)}
    report = asyncio.run(run_comparison(_plan(), providers, _settings()))
    assert report.estimated is True
    assert providers["blinkit"].added == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.orchestrator'`

- [ ] **Step 3: Write the implementation**

Create `app/orchestrator.py`:

```python
"""Fan the single-platform pipeline out across every connected provider."""

from __future__ import annotations

import asyncio
from typing import Callable

from .compare import build_outcome, estimated_summary, rank
from .config import Settings
from .constraints import enforce_constraints
from .matcher import match_across_platforms
from .models import (
    CartPlan,
    ComparisonReport,
    DraftCart,
    DraftItem,
    PlatformOutcome,
    StreamEvent,
)
from .providers.base import CartSummary, GroceryProvider


EventSink = Callable[[StreamEvent], None] | None


def _emit(on_event: EventSink, stage: str, message: str, provider: str = "") -> None:
    if on_event is not None:
        on_event(StreamEvent(event="stage", stage=stage, message=message,
                             data={"provider": provider}))


async def _search_platform(
    provider: GroceryProvider,
    plan: CartPlan,
    on_event: EventSink,
) -> dict[str, list]:
    """Search every planned item on one platform. Items stay sequential."""
    results = {}
    for index, item in enumerate(plan.items, start=1):
        _emit(on_event, "retrieval",
              f"Searching {provider.display_name} for {item.search_term} "
              f"({index}/{len(plan.items)})…", provider.provider_id)
        results[item.id] = await provider.search(item.search_term)
    return results


async def run_comparison(
    plan: CartPlan,
    providers: dict[str, GroceryProvider],
    settings: Settings,
    on_event: EventSink = None,
) -> ComparisonReport:
    """Build the same basket everywhere and rank the resulting real totals."""
    # 1. Search every platform concurrently.
    searches = await asyncio.gather(
        *(_search_platform(provider, plan, on_event) for provider in providers.values()),
        return_exceptions=True,
    )

    candidates: dict[str, dict[str, list]] = {}
    failures: dict[str, str] = {}
    for provider_id, result in zip(providers, searches):
        if isinstance(result, BaseException):
            failures[provider_id] = str(result) or result.__class__.__name__
        else:
            candidates[provider_id] = result

    # 2. Joint match: one decision per item across all healthy platforms.
    _emit(on_event, "matcher", "Comparing products across platforms…")
    drafts: dict[str, DraftCart] = {}
    items_by_provider: dict[str, list[DraftItem]] = {pid: [] for pid in candidates}
    for planned in plan.items:
        match = await asyncio.to_thread(
            match_across_platforms,
            planned,
            {pid: candidates[pid].get(planned.id, []) for pid in candidates},
            settings,
        )
        for provider_id in candidates:
            decision = match.picks.get(provider_id)
            items_by_provider[provider_id].append(
                DraftItem(
                    planned=planned,
                    candidates=candidates[provider_id].get(planned.id, []),
                    selected_product_id=decision.product_id if decision else None,
                    units_to_add=decision.units_to_add if decision else 0,
                    reason=decision.reason if decision else match.equivalence_note,
                )
            )

    for provider_id, items in items_by_provider.items():
        drafts[provider_id] = enforce_constraints(
            items, plan.constraints,
            dry_run=not settings.cart_mutations_allowed_for(provider_id),
            provider_id=provider_id,
            provider_name=providers[provider_id].display_name,
        )

    # 3. Build each cart and read its real total, concurrently.
    async def settle(provider_id: str) -> tuple[str, CartSummary | None, str]:
        provider = providers[provider_id]
        draft = drafts[provider_id]
        if not settings.cart_mutations_allowed_for(provider_id):
            return provider_id, estimated_summary(provider_id, draft), ""
        selections = [
            (product, item.units_to_add)
            for item in draft.items
            if not item.removed
            and (product := item.selected_product) is not None
            and item.units_to_add > 0
        ]
        try:
            _emit(on_event, "cart", f"Building the {provider.display_name} cart…",
                  provider_id)
            await provider.add_items(selections, operation_id=draft.id)
            return provider_id, await provider.cart_summary(), ""
        except Exception as exc:
            return provider_id, None, str(exc) or exc.__class__.__name__

    settled = await asyncio.gather(*(settle(pid) for pid in drafts))

    # 4. Fold into outcomes and rank.
    outcomes: list[PlatformOutcome] = []
    for provider_id, summary, error in settled:
        outcomes.append(
            build_outcome(
                provider_id, providers[provider_id].display_name,
                drafts[provider_id], summary, settings,
                status="ok" if summary is not None else "failed",
                error=error,
            )
        )
    for provider_id, error in failures.items():
        outcomes.append(
            PlatformOutcome(
                provider=provider_id,
                display_name=providers[provider_id].display_name,
                status="failed", error=error,
            )
        )

    _emit(on_event, "compare", "Ranking platforms…")
    return rank(outcomes, settings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS, 6 passing

- [ ] **Step 5: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: run the pipeline across all platforms in parallel"
```

---

### Task 11: Comparison API endpoints

**Files:**
- Modify: `app/main.py`, `app/models.py`
- Test: `tests/test_compare_api.py` (create)

**Interfaces:**
- Consumes: `run_comparison` (Task 10), `plan_cart` (existing), the `providers` registry (existing)
- Produces: `GET /api/compare/preflight`, `POST /api/compare/stream`, `POST /api/compare/choose`

- [ ] **Step 1: Add the request models**

In `app/models.py`:

```python
class ComparePreflightPlatform(BaseModel):
    provider: str
    display_name: str
    connected: bool
    cart_line_count: int = 0
    cart_total: float = 0
    needs_clearing: bool = False
    error: str = ""


class CompareChoiceRequest(BaseModel):
    report_id: str
    provider_id: str = Field(min_length=1)
    clear_losers: bool = True
```

Extend the `StreamEvent` literal to include the new stages:

```python
    event: Literal["stage", "plan", "item", "draft", "compare", "error"]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_compare_api.py`. This is the first test in the repo to use
`TestClient`; it is verified working, but emits a
`StarletteDeprecationWarning` about `httpx` — that is expected, harmless, and
must not be "fixed" by adding a dependency.

```python
from fastapi.testclient import TestClient

from app.main import app


def test_preflight_lists_every_registered_platform():
    with TestClient(app) as client:
        response = client.get("/api/compare/preflight")
    assert response.status_code == 200
    providers = {row["provider"] for row in response.json()["platforms"]}
    assert providers == {"blinkit", "instamart", "zepto"}


def test_compare_requires_text_or_image():
    with TestClient(app) as client:
        response = client.post("/api/compare/stream", data={"text": ""})
    assert response.status_code == 422


def test_choose_rejects_an_unknown_report():
    with TestClient(app) as client:
        response = client.post(
            "/api/compare/choose",
            json={"report_id": "nope", "provider_id": "blinkit"},
        )
    assert response.status_code == 404


def test_choose_rejects_a_provider_not_in_the_report():
    from app.main import REPORTS
    from app.models import ComparisonReport, PlatformOutcome

    report = ComparisonReport(
        platforms=[PlatformOutcome(provider="blinkit", display_name="Blinkit")],
        winner="blinkit", ranking=["blinkit"],
    )
    REPORTS[report.id] = report
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/compare/choose",
                json={"report_id": report.id, "provider_id": "zepto"},
            )
        assert response.status_code == 422
    finally:
        REPORTS.pop(report.id, None)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_compare_api.py -v`
Expected: FAIL — 404 on `/api/compare/preflight`.

- [ ] **Step 4: Implement the endpoints**

In `app/main.py`, add `REPORTS: dict[str, ComparisonReport] = {}` next to `DRAFTS`, and:

```python
@app.get("/api/compare/preflight")
async def compare_preflight() -> dict[str, object]:
    """Report connection and cart state for every platform before a run."""
    async def inspect(provider: GroceryProvider) -> ComparePreflightPlatform:
        try:
            status = await provider.status()
            if not status.connected:
                return ComparePreflightPlatform(
                    provider=provider.provider_id, display_name=provider.display_name,
                    connected=False, error=status.message or "Not connected.",
                )
            summary = await provider.cart_summary()
            return ComparePreflightPlatform(
                provider=provider.provider_id, display_name=provider.display_name,
                connected=True, cart_line_count=len(summary.lines),
                cart_total=summary.total, needs_clearing=bool(summary.lines),
            )
        except Exception as exc:
            return ComparePreflightPlatform(
                provider=provider.provider_id, display_name=provider.display_name,
                connected=False, error=str(exc) or exc.__class__.__name__,
            )

    platforms = await asyncio.gather(*(inspect(p) for p in providers.values()))
    return {
        "platforms": [platform.model_dump(mode="json") for platform in platforms],
        "needs_clearing": any(platform.needs_clearing for platform in platforms),
        "ready": [p.provider for p in platforms if p.connected],
    }


@app.post("/api/compare/stream")
async def compare_stream(
    text: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    clear_carts_confirmed: bool = Form(default=False),
) -> StreamingResponse:
    image_bytes, image_type = await _read_optional_image(image)
    if not text.strip() and not image_bytes:
        raise HTTPException(status_code=422, detail="Add a photo, a typed request, or both.")

    async def generate() -> AsyncIterator[bytes]:
        queue: list[StreamEvent] = []
        try:
            connected = {}
            for provider_id, provider in providers.items():
                try:
                    if (await provider.status()).connected:
                        connected[provider_id] = provider
                except ProviderError:
                    continue
            if not connected:
                raise ProviderError("No grocery platform is connected.")

            # Carts must be empty: fees are computed on the whole cart value.
            for provider_id, provider in connected.items():
                if not settings.cart_mutations_allowed_for(provider_id):
                    continue
                summary = await provider.cart_summary()
                if summary.lines:
                    if not clear_carts_confirmed:
                        raise ProviderError(
                            f"The {provider.display_name} cart is not empty. "
                            "Confirm clearing before comparing."
                        )
                    await provider.clear_cart(operation_id="preflight")

            yield encode_event(StreamEvent(
                event="stage", stage="planner",
                message="Reading the request and building a cart plan…"))
            plan = await asyncio.to_thread(
                plan_cart, text=text, image_bytes=image_bytes,
                image_media_type=image_type, settings=settings,
            )
            yield encode_event(StreamEvent(
                event="plan", stage="planner",
                message=f"Planned {len(plan.items)} item(s).",
                data=plan.model_dump(mode="json")))

            report = await run_comparison(plan, connected, settings, on_event=queue.append)
            REPORTS[report.id] = report
            for event in queue:
                yield encode_event(event)
            yield encode_event(StreamEvent(
                event="compare", stage="compare",
                message=(f"{report.winner} offers the best cart."
                         if report.winner else "No platform produced a comparable cart."),
                data=report.model_dump(mode="json")))
        except Exception as exc:
            yield encode_event(StreamEvent(
                event="error", stage="error",
                message=str(exc).strip() or exc.__class__.__name__))

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/api/compare/choose")
async def compare_choose(request: CompareChoiceRequest) -> dict[str, object]:
    """Keep the chosen platform's cart and clear the losers."""
    report = REPORTS.get(request.report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Comparison not found. Run a new comparison.")
    if request.provider_id not in {p.provider for p in report.platforms}:
        raise HTTPException(status_code=422, detail="That platform was not part of this comparison.")

    cleared, errors = [], []
    if request.clear_losers:
        for outcome in report.platforms:
            if outcome.provider == request.provider_id or outcome.status != "ok":
                continue
            if not settings.cart_mutations_allowed_for(outcome.provider):
                continue
            try:
                await providers[outcome.provider].clear_cart(operation_id=report.id)
                cleared.append(outcome.provider)
            except Exception as exc:
                errors.append(f"{outcome.display_name}: {exc}")

    return {
        "chosen": request.provider_id,
        "cleared": cleared,
        "errors": errors,
        "checkout_disabled": True,
    }
```

Extract the image-reading block currently inline in `create_draft_stream` (`app/main.py:140-150`) into `_read_optional_image(image)` and call it from both endpoints — do not duplicate it.

Import `run_comparison`, `ComparisonReport`, `ComparePreflightPlatform`, `CompareChoiceRequest`, `GroceryProvider`.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_compare_api.py -v`
Expected: PASS, 4 passing

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add app/main.py app/models.py tests/test_compare_api.py
git commit -m "feat: add comparison preflight, run, and choose endpoints"
```

---

### Task 12: Comparison UI

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/styles.css`

**Interfaces:**
- Consumes: `/api/compare/preflight`, `/api/compare/stream`, `/api/compare/choose` (Task 11)
- Produces: no code interface — this is the final user-facing layer

- [ ] **Step 1: Add the preflight gate**

On load, call `GET /api/compare/preflight` and render one row per platform: connected state, cart line count, and cart total. If `needs_clearing` is true, show the contents and a single explicit confirmation — the button text must name the consequence, e.g. **"Clear all carts and compare"**. Disconnected platforms get a Connect link and are excluded from the run rather than blocking it.

- [ ] **Step 2: Stream the run**

Post to `/api/compare/stream` with `clear_carts_confirmed` set from the gate. Reuse the existing NDJSON reader in `static/app.js` — the event shape is unchanged, with `data.provider` now naming which platform a stage belongs to. Show per-platform progress.

- [ ] **Step 3: Render the comparison table**

On the `compare` event, render a column per platform:
- Winner badge on `report.winner`.
- Per row: the item, and each platform's matched product, pack size, and price. Mark missing items and short packs distinctly — they are not the same thing.
- Footer per column: subtotal, each fee line, discounts, **real total**, and ETA.
- `report.reasons` as a plain-English list under the table.
- If `report.estimated` is true, show a prominent banner: totals are estimated because cart writes are disabled.
- Platforms with status `failed` / `not_connected` render as a greyed column showing `outcome.error` — never omitted, so a broken platform is visibly broken rather than absent.

- [ ] **Step 4: Wire the choose action**

A "Keep this cart" button per ranked column posts to `/api/compare/choose`. On success, show which carts were cleared and any errors, plus a reminder that checkout happens in the platform's own app.

- [ ] **Step 5: Verify manually**

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

With default settings (`SAFETY_LOCK=true`), confirm: preflight lists all three platforms, a run completes, totals are labelled estimated, and no cart was written. Then verify a real run with writes enabled for the platforms you have connected.

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add static/
git commit -m "feat: add multi-platform comparison UI"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `cart_summary` / `clear_cart` on the interface | 1 |
| Fee reconciliation invariant | 1, 5, 9 (`_rankable`) |
| Per-unit normalisation reusing `constraints.py` | 2 |
| Zepto settings, fail-closed `zepto_cart_writes` | 3 |
| Instamart cart read/clear with no allowlist growth | 4 |
| Blinkit cart read/clear | 5 |
| Zepto provider (search) | 6 |
| Zepto cart add/read/clear | 7 |
| Joint cross-platform matching | 8 |
| Shortfall folding into coverage | 9 (`build_outcome`) |
| Deterministic ranking, ETA tiebreak, failure labelling | 9 |
| Parallel fan-out, partial failure tolerance | 10 |
| Preflight, clear-carts confirmation, choose-winner | 11 |
| Dry run degrades to estimates | 9 (`estimated_summary`), 10, 11, 12 |
| Comparison UI with labelled gaps | 12 |

**Known gaps, deliberately accepted:**
- The re-verify-carts-empty step lives in the endpoint (Task 11) rather than the orchestrator, so it is covered by manual verification, not a unit test.
- Blinkit and Zepto DOM selectors cannot be written in advance. Tasks 5, 6, and 7 specify the pure parser fully and mark the selectors as live-DOM discovery, matching how `app/blinkit.py` is already built and verified.
- Task 9 flags a possible circular import (`models` ↔ `providers.base`) with a concrete remedy rather than guessing which way the dependency will fall.

**Type consistency:** `cart_summary()`, `clear_cart(*, operation_id)`, `per_unit_price`, `fill_ratio`, `match_across_platforms`, `build_outcome`, `estimated_summary`, `rank`, and `run_comparison` keep identical signatures wherever they appear across tasks.
