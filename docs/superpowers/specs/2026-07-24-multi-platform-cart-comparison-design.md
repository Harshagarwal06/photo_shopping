# Multi-Platform Cart Comparison

**Date:** 2026-07-24
**Status:** Approved design
**Audience:** Personal tool for a single user, running locally on macOS.
**Builds on:** `2026-07-15-photo-grocery-blinkit-design.md`

## Goal

Take one grocery intent — a photo of a handwritten list, a typed request, or
both — and answer the question *"which instant-delivery app should I actually
order this from?"*

The system builds the same basket on Blinkit, Zepto, and Swiggy Instamart,
reads each platform's **real** cart total including fees and discounts, and
presents a full breakdown with a recommended winner. The user picks a
platform; the losing platforms' carts are cleared; checkout happens manually
in the winning app.

Comparison becomes the default flow. The single-platform path from the
previous spec survives only as the degenerate case where exactly one platform
is connected.

## Why "real fees" and not estimates

Item subtotal is not the final price. Delivery fee, handling fee, rain/surge
fee, small-cart fee, and auto-applied coupons or memberships materialise only
on the cart screen, and they depend on the cart's own value. A comparison of
subtotals will confidently recommend the wrong platform.

Two consequences drive the whole design:

1. We must actually build the basket on every platform before comparing.
2. Every platform's cart must start **empty**, because fee thresholds are
   computed on the total cart value. A pre-existing ₹300 in the Blinkit cart
   crosses the free-delivery threshold while an empty Zepto cart does not, and
   the comparison becomes meaningless. Diffing before/after does not rescue
   this — the fee itself is a function of the combined total.

## Current state (verified 2026-07-24)

The provider seam is further along than the previous spec describes:

- `app/providers/factory.py` already exposes
  `create_providers(settings) -> dict[str, GroceryProvider]`, instantiating
  every provider eagerly. `grocery_provider` only chooses a default.
- `app/main.py` already holds a `providers` registry, an `ACTIVE_PROVIDER_ID`,
  a `get_provider(provider_id)` helper, and per-provider status/connect/select
  endpoints.
- `app/config.py:62` already exposes
  `cart_mutations_allowed_for(provider_id)`, failing closed on
  `safety_lock`, `dry_run`, and `demo_mode`, with an extra
  `instamart_cart_writes` gate for Instamart.
- `app/providers/instamart_transport.py:35` enforces an MCP tool allowlist
  that **already includes `get_cart` and `update_cart`**.
- `app/blinkit.py:160-195` implements Chromium `SingletonLock` handling keyed
  on the single `settings.browser_profile_dir`.

So the registry migration is done. What is missing is running the pipeline
across the registry concurrently, reading carts, clearing carts, a Zepto
provider, and the comparison engine itself.

## Architecture

```
photo / text
    │
1.  planner.py                       CartPlan {items, constraints}   (unchanged, runs once)
    │
2.  PREFLIGHT  (own endpoint, before any work)
      · per-platform connection + login state
      · cart_summary() on each platform → show the user what is in them
      · ONE explicit confirmation to clear all carts
    │
3.  orchestrator: fan out across platforms, in parallel
      provider.search(term) per planned item      → candidates[platform][item]
    │
4.  matcher.match_across_platforms                → one LLM call per item,
    │                                               all platforms' candidates at once
5.  constraints.enforce_constraints               → per-platform draft (existing code, N×)
    │
6.  re-verify every cart empty, then build all carts in parallel
    │
7.  provider.cart_summary()                       → REAL subtotal, fees, discounts, ETA
    │
8.  compare.rank()                                → ComparisonReport (pure functions)
    │
9.  UI comparison screen → user picks winner
    │
10. clear_cart() on losers. Winner's cart survives.
```

### New modules

| Module | Purpose | LLM |
|---|---|---|
| `app/providers/zepto.py` | Third Playwright provider, mirrors `blinkit.py` | no |
| `app/units.py` | Pack-size parsing and per-unit normalisation | no |
| `app/compare.py` | Ranking, coverage tiers, winner selection | no |
| `app/orchestrator.py` | Parallel fan-out of the pipeline across platforms | no |

### Modified modules

| Module | Change |
|---|---|
| `app/providers/base.py` | Add `cart_summary()` and `clear_cart()` to the ABC; add `CartSummary`, `CartLine`, `FeeLine` |
| `app/providers/factory.py` | Register `zepto` |
| `app/matcher.py` | Add `match_across_platforms()`; keep `match_product()` for single-item re-search |
| `app/config.py` | Add Zepto settings, `zepto_cart_writes`, fee-estimate table, ranking thresholds |
| `app/main.py` | Preflight + comparison endpoints; pick-winner endpoint |
| `app/models.py` | Comparison models |
| `static/` | Comparison UI |

`planner.py` and `constraints.py` are not modified.

## Provider interface additions

```python
class FeeLine(BaseModel):
    label: str        # "Delivery fee", "Handling fee", "Rain surge", "Coupon"
    amount: float     # negative for discounts

class CartLine(BaseModel):
    product_id: str
    name: str
    quantity: int
    unit_price: float
    line_total: float

class CartSummary(BaseModel):
    provider: str
    lines: list[CartLine]
    subtotal: float
    fees: list[FeeLine]
    total: float                      # what the platform itself reports
    delivery_eta_minutes: int | None
    estimated: bool = False           # True when fees came from config, not the cart
    raw_note: str = ""
```

`clear_cart(operation_id: str) -> None` is destructive and guarded (see
Safety).

### Per-provider mapping

- **Instamart:** `cart_summary()` → `get_cart`; `clear_cart()` → `update_cart`
  with every quantity zeroed. Both tools are already on the allowlist, so this
  needs **no expansion of MCP permissions**. Open question: whether `get_cart`
  returns fee lines at all — resolved by a spike before anything is built on it.
- **Blinkit / Zepto:** `cart_summary()` scrapes the cart panel;
  `clear_cart()` drives each line's quantity to zero. Both are new Playwright
  surfaces and the most fragile code in the feature.

## The honesty mechanisms

A comparison that is confidently wrong is worse than no comparison. Three
deliberate guards:

### Fee reconciliation

`CartSummary` asserts `subtotal + Σfees == total` to the paisa. If a scrape
misses a fee line, the arithmetic disagrees with the platform's own reported
total, and the platform is marked `failed` rather than reported as
artificially cheap. This is the primary detector for a silently-drifting
scraper — it converts the dangerous failure mode (wrong answer) into the safe
one (visible error).

### Per-unit normalisation

`app/units.py` parses free-text pack sizes — `"1 L"`, `"500 ml"`, `"1 kg"`,
`"250 g"`, `"6 x 100 g"`, `"12 pieces"`, `"1 dozen"` — into
`(magnitude, dimension)` where dimension is one of `volume_ml`, `mass_g`,
`count`. Per-unit prices are comparable only within the same dimension.

Unparseable input returns `None` and the item is marked *not price-comparable
per unit*. It is never guessed.

### Shortfall folds into coverage

If Zepto's cheaper total comes from supplying 500ml where 1L was requested,
that is not a cheaper cart — it is a smaller one. A platform supplying less
than `min_fill_ratio` (default 0.9) of an item's requested quantity counts
that item as **partial**, not matched.

This makes "cheaper because the packs are smaller" lose on the merits, rather
than needing a caveat in the UI that nobody reads.

## Ranking (`app/compare.py`)

Deterministic pure functions, no LLM. Arithmetic is the one thing that must be
exactly right, and it is not a job for a 3B vision model.

```python
class Substitution(BaseModel):
    item: str
    requested: str
    supplied: str
    reason: str
    per_unit_delta: float | None      # None when not comparable

class PlatformOutcome(BaseModel):
    provider: str
    display_name: str
    status: Literal["ok", "not_connected", "unavailable", "failed"]
    error: str = ""
    summary: CartSummary | None = None
    matched_items: int = 0
    partial_items: list[str] = []
    missing_items: list[str] = []
    substitutions: list[Substitution] = []

class ComparisonReport(BaseModel):
    id: str
    platforms: list[PlatformOutcome]
    winner: str | None
    ranking: list[str]
    reasons: list[str]
    estimated: bool
```

Rules, applied lexicographically:

1. Only `status == "ok"` platforms are rankable. `not_connected`,
   `unavailable`, and `failed` are rendered as labeled columns carrying the
   real error message. **A broken Zepto must never be indistinguishable from
   an expensive Zepto** — this is the failure mode the whole design is built
   to avoid.
2. Sort by coverage tier: full coverage beats partial (per the shortfall
   rule), which beats missing items.
3. Within a tier, sort by real final total, ascending.
4. ETA breaks ties within a price band: if totals are within
   `eta_tiebreak_rupees` (default ₹20), the faster platform wins.
5. Substitutions and fee deltas are emitted as generated reason strings —
   disclosures, never hidden weights in the ranking.

If no platform reaches `status == "ok"`, `winner` is `None` and the report
explains why per platform.

## Safety model

Comparison writes to every platform's cart, so the existing fail-closed
pattern is extended rather than bypassed.

- `cart_mutations_allowed_for(provider_id)` gains a `zepto_cart_writes` gate
  mirroring `instamart_cart_writes`.
- **`clear_cart` is guarded more tightly than `add_items`.** It runs only
  when: (a) the user gave the explicit preflight confirmation for this run, or
  (b) the platform is a confirmed loser of a run this app populated, matched by
  `operation_id`. There is no other path that clears a cart.
- **Dry run degrades honestly instead of refusing.** With writes disabled the
  comparison still runs, using a per-platform fee-estimate table from config,
  and marks every total `estimated: true` through the API and prominently in
  the UI. This keeps the whole feature developable and testable without
  touching a real cart, and it is the safe default.
- A run aborts loudly if any cart is non-empty at step 6, even though
  preflight cleared it — the user may have added something in between. It
  never compares polluted baskets.
- Checkout stays disabled on every platform. Unchanged.

## Execution and failure handling

- Platforms run **concurrently**; per-platform progress streams through the
  existing NDJSON `StreamEvent` channel, extended with a `provider` field.
  Within a platform, item searches stay sequential.
- Blinkit and Zepto each drive a headful Chromium with a **separate persistent
  profile directory and therefore a separate `SingletonLock`**. The existing
  lock logic in `app/blinkit.py` is keyed on `settings.browser_profile_dir`, so
  Zepto requires `zepto_profile_dir`. Two headful browsers run at once.
- **All logins are resolved during preflight.** A login wall appearing
  mid-run inside a parallel fan-out is unrecoverable, so the run refuses to
  start until every participating platform reports connected.
- A platform failing mid-run (timeout, scrape breakage, expired session, fee
  reconciliation mismatch) is captured as a `failed` outcome with its real
  error. The remaining platforms still produce a comparison.

## Testing

| Target | Approach |
|---|---|
| `app/units.py` | Pure functions. Heavy unit tests including unparseable input returning `None` |
| `app/compare.py` | Pure functions. Ranking, ties, ETA tiebreak, coverage tiers, shortfall demotion, partial failures, all-platforms-failed |
| `CartSummary` | Reconciliation test: a missing fee line must fail, not pass silently |
| `matcher.match_across_platforms` | Fixture LLM responses, including "no equivalent on this platform" |
| `app/orchestrator.py` | Fake providers — concurrency, one provider raising, all raising |
| Safety | `test_zepto_safety.py` mirroring `test_instamart_safety.py`; `clear_cart` guard tests asserting every unguarded path refuses |
| `zepto.py` cart read / add / clear | Manual against the live site, dry run first — same as `blinkit.py` is verified today |
| Frontend | Manual (personal tool, no build step) |

## Risks

1. **Zepto is the critical path.** No comparison is trustworthy until the
   Zepto provider works end to end, and a broken scraper produces a losing
   platform rather than an error. It is built and verified first; fee
   reconciliation is its safety net.
2. Fee line labels vary per platform and change without notice.
   Reconciliation is the detector; the estimate table is the fallback.
3. Two headful Chromium instances plus an MCP session is memory-heavy and
   slow. Preflight login resolution is what keeps it recoverable.
4. Clearing carts is irreversible. One confirmation gate, fail-closed
   settings, `operation_id` matching, no exceptions.
5. A full run mutates three real carts and takes minutes. This is a
   deliberate action, not something to fire casually.
6. Whether Instamart's `get_cart` returns fee lines is unknown. If it does
   not, Instamart falls back to estimated fees and is labeled as such — it is
   not silently compared on subtotal alone.

## Out of scope

- BigBasket, DMart, JioMart, or any fourth platform (the registry makes each
  one a single new file).
- Automatic checkout or payment. Unchanged from the previous spec.
- Learning platform preferences over time.
- Price history or "wait for a better price" tracking.
- Multi-user support, deployment, hosting.
