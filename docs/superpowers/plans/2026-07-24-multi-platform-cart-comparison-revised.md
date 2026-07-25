# Multi-Platform Cart Comparison — Revised Implementation Plan

**Date:** 2026-07-24  
**Status:** Approved for implementation planning  
**Supersedes:** `2026-07-24-multi-platform-cart-comparison.md` for execution  
**Baseline:** 119 passing tests

## Goal

Turn one typed grocery request or uploaded grocery-list photo into a trustworthy
comparison across Blinkit, Zepto, and Swiggy Instamart.

The comparison must show equivalent products, pack quantities, item coverage,
substitutions, item subtotals, fees, discounts, final totals, and delivery
estimates. It recommends a platform, but the user always chooses where to order.
The application never checks out, pays, or places an order.

## Approved product decisions

These decisions are locked for version one:

1. **Estimated comparison is the default.**
   It searches and compares products without modifying any cart. All platform
   fees that were not read from a real cart are visibly labelled estimates.
2. **Verified comparison is explicit.**
   It may temporarily add items to participating carts only after the user
   reviews the proposed matches and confirms the mutation.
3. **Version one requires empty carts for verified comparison.**
   The application refuses to start a verified run when any participating cart
   is non-empty. It does not delete or replace unrelated user items.
4. **Controlled substitutions are allowed.**
   Exact brand, variant, and size constraints are respected when specified.
   Generic requests may use the closest equivalent. Every substitution is
   disclosed before cart mutation.
5. **Quantity coverage is mandatory.**
   A cheaper but smaller basket cannot win. The default acceptable fill range
   is 90–110% of the requested quantity where measurements are comparable.
6. **Losing carts are restored automatically.**
   Because verified runs start with empty carts in version one, restoration
   means removing only the items created by that comparison operation.
7. **The winning cart remains populated for manual review.**
   The user may instead choose **Keep all carts**.
8. **Checkout remains structurally unavailable.**
   There is no checkout, payment, or order-placement endpoint, provider method,
   MCP tool, browser action, or UI control.

## Current implementation assessment

The repository contains a tested comparison core, but not a usable comparison
product.

| Capability | Current state | Work required |
|---|---|---|
| Cart and fee models | Implemented | Keep and extend |
| Unit-price and fill-ratio helpers | Implemented | Add upper-bound and equivalence tests |
| Cross-platform matcher | Implemented | Add substitution policy and user overrides |
| Deterministic comparison engine | Implemented | Align with approved rules and richer breakdown |
| Parallel orchestrator | Partial; fake-provider tests only | Add preflight, eligibility, mutation state, cleanup |
| Instamart search/address integration | Implemented and live-tested | Harden cart summary and verified-run behavior |
| Instamart cart read/clear | Implemented | Live-test populated cart only after explicit approval |
| Blinkit product search/add | Implemented | Finish real cart summary and operation-scoped cleanup |
| Blinkit cart parser | Implemented | Wire it to the provider and live-test |
| Zepto product parser/config | Implemented | Build live provider, login, search, add, cart read, cleanup |
| Provider registry | Blinkit and Instamart only | Register Zepto |
| Comparison API | Missing | Add preflight, estimate, verify, choose, cleanup endpoints |
| Comparison UI | Missing | Build complete workflow and breakdown |
| Real three-platform validation | Missing | Perform gated manual verification |

## Safety invariants

Every implementation task must preserve these invariants:

- `settings.cart_mutations_allowed_for(provider_id)` guards every cart write.
- Estimated mode performs zero cart mutations.
- Verified mode requires a server-issued, single-use confirmation token tied to:
  - the comparison run ID,
  - participating provider IDs,
  - selected address state,
  - proposed products and quantities,
  - a recent empty-cart preflight.
- A verified run rechecks all carts immediately before the first mutation.
- If any cart is non-empty, the entire verified run aborts before writing.
- Cart cleanup removes only items recorded in that run’s operation ledger.
- Cleanup and winner-selection endpoints are idempotent.
- Failures are reported per platform; failed platforms are never ranked as cheap.
- Unreconciled real totals are excluded from ranking.
- Estimated totals are never presented as verified totals.
- Auto-applied cart discounts may be counted. Payment-method, wallet, or
  checkout-only offers are excluded because checkout is unavailable.
- No code path may navigate to or activate checkout, payment, or order placement.

## Target user flow

### 1. Create one grocery intent

The user enters text, uploads a list photo, or does both. Planning runs once and
produces normalized requested items and constraints.

### 2. Select platforms

Blinkit, Zepto, and Instamart appear as platform cards. Each card shows:

- connection state,
- delivery-location readiness,
- search availability,
- cart-write capability,
- whether verified comparison is available.

Disconnected platforms remain visible with a clear action or error.

### 3. Run estimated comparison

The application searches eligible platforms concurrently, matches equivalent
products, normalizes quantities, and displays:

- selected product per requested item and platform,
- brand/variant/pack differences,
- requested versus supplied quantity,
- per-unit price where comparable,
- missing or partial items,
- item subtotal,
- estimated fees and estimated final total,
- delivery estimate when search results provide it.

No cart changes occur.

### 4. Review substitutions

Before verified comparison, the user can:

- accept a proposed substitute,
- choose another candidate,
- require an exact brand or pack,
- exclude an item from the run,
- exclude a platform from the run.

The review creates a frozen comparison proposal.

### 5. Preflight verified comparison

The application checks each participating platform:

- still connected,
- same delivery location,
- cart is empty,
- required cart-read/add/cleanup capabilities are available,
- cart writes are explicitly enabled.

If any selected platform fails, verified comparison does not start. The user may
remove that platform and rerun preflight.

### 6. Confirm cart mutation

The confirmation screen states exactly which providers and items will be
modified. Confirmation creates a short-lived, single-use mutation token.

### 7. Build and verify carts

The application:

1. rechecks that every cart is empty,
2. records an operation ledger,
3. adds the approved products and quantities,
4. reads each real cart,
5. extracts lines, fees, discounts, total, and ETA,
6. reconciles `subtotal + fees - discounts == reported total`,
7. marks failures honestly and continues with healthy platforms.

### 8. Present the decision breakdown

The report ranks platforms using:

1. full coverage,
2. partial coverage,
3. missing items,
4. verified final total within the same coverage tier,
5. faster ETA when totals are within `ETA_TIEBREAK_RUPEES`.

The screen shows item rows and bill rows, not only a winner badge.

### 9. Choose outcome

The user chooses one of:

- **Keep winner and clear losing carts**
- **Keep all carts**
- **Cancel and clear all comparison carts**

Checkout remains manual in the selected provider’s own application.

## Data and API design

Add or refine these models in `app/models.py`:

- `ComparisonMode`: `estimated | verified`
- `PlatformCapability`
- `PlatformPreflight`
- `ComparisonPreflight`
- `ComparisonProposal`
- `ProposalItem`
- `ProviderProposal`
- `SubstitutionDecision`
- `VerifiedComparisonRequest`
- `ComparisonOperation`
- `OperationCartLine`
- `ComparisonCleanupRequest`
- `CleanupOutcome`

The operation record must contain:

- immutable run ID,
- proposal hash,
- provider IDs,
- selected address/location fingerprints,
- preflight timestamp,
- requested items,
- approved product IDs and quantities,
- successfully added lines,
- cart summaries,
- cleanup state,
- selected winner,
- terminal status.

Keep operation records in memory for version one, following the current draft
storage pattern. Structure the repository interface so persistence can be added
later without changing endpoint contracts.

Add endpoints in `app/main.py`:

- `POST /api/comparisons/preflight`
- `POST /api/comparisons/estimate`
- `POST /api/comparisons/proposals/{id}/verify-preflight`
- `POST /api/comparisons/proposals/{id}/verify`
- `GET /api/comparisons/{id}`
- `POST /api/comparisons/{id}/choose`
- `POST /api/comparisons/{id}/cleanup`

Mutation endpoints require the proposal ID and confirmation token. Repeated
requests return the existing operation result rather than repeating writes.

## Implementation sequence

### Task 1: Freeze and extend comparison contracts

**Files**

- Modify `app/models.py`
- Modify `app/providers/base.py`
- Modify `app/config.py`
- Add `tests/test_comparison_models.py`
- Extend `tests/test_cart_summary.py`
- Extend `tests/test_zepto_safety.py`

**Work**

- Add the mode, preflight, proposal, operation, and cleanup models.
- Add provider capability reporting:
  - search,
  - address selection,
  - cart read,
  - cart add,
  - operation-scoped cleanup.
- Add `max_fill_ratio=1.1`.
- Make provider-specific cart-write gates explicit for all three platforms.
- Define terminal operation states and idempotency rules.

**Acceptance**

- Invalid state transitions fail validation.
- Estimated mode cannot carry a mutation token.
- Verified mode cannot start without eligible platforms and a confirmation token.
- Full test suite remains green.

### Task 2: Finish the Zepto provider

**Files**

- Create `app/providers/zepto.py`
- Expand `app/zepto.py`
- Modify `app/providers/factory.py`
- Modify `.env.example`
- Extend `tests/test_zepto_products.py`
- Add `tests/test_zepto_provider.py`
- Add `tests/test_zepto_cart.py`
- Extend `tests/test_zepto_safety.py`

**Work**

- Implement a Playwright client with its own persistent profile.
- Implement connection/status, login handoff, product search, and result mapping.
- Implement guarded cart add.
- Implement cart summary scraping with reconciliation.
- Implement operation-scoped cleanup.
- Register Zepto in the provider factory.
- Expose it through health and provider-status responses.

**Acceptance**

- Zepto appears in `/api/health`.
- Search works against a connected session.
- Dry-run add performs no click.
- Cart write and cleanup are blocked by default.
- Cart parser fails closed when the bill does not reconcile.
- Blinkit and Zepto browser profiles can run concurrently.

### Task 3: Complete Blinkit cart integration

**Files**

- Modify `app/blinkit.py`
- Modify `app/providers/blinkit.py`
- Extend `tests/test_blinkit_cart.py`
- Add `tests/test_blinkit_operation_cleanup.py`

**Work**

- Wire the existing cart parser to a live `BlinkitClient.cart_summary()`.
- Add robust cart-line identity and quantity extraction.
- Implement operation-scoped cleanup of comparison-created quantities.
- Never expose a whole-cart destructive clear method to normal flows.
- Return capability and reconciliation diagnostics.

**Acceptance**

- `BlinkitProvider.cart_summary()` no longer raises “not implemented.”
- Empty and populated carts parse correctly in fixtures.
- Missing fee lines disqualify the summary.
- Cleanup cannot remove quantities not recorded in the operation ledger.

### Task 4: Harden Instamart verified-cart behavior

**Files**

- Modify `app/providers/instamart.py`
- Extend `tests/test_instamart_cart.py`
- Extend `tests/test_instamart_provider.py`
- Extend `tests/test_instamart_safety.py`

**Work**

- Align cart summary mapping with current live MCP payloads.
- Record the exact quantities added by each comparison operation.
- Replace broad cleanup assumptions with operation-scoped quantity removal.
- Verify the cart after add and cleanup.
- Expose cart-read/add/cleanup capabilities through diagnostics.

**Acceptance**

- No new MCP tool is added to the allowlist.
- Cleanup cannot clear unrelated quantities.
- Repeated cleanup is safe.
- Populated-cart totals reconcile or the outcome fails closed.

### Task 5: Implement read-only preflight

**Files**

- Create `app/comparison_service.py`
- Modify `app/main.py`
- Add `tests/test_comparison_preflight.py`

**Work**

- Check connection, delivery location, capabilities, and cart state concurrently.
- Keep disconnected and unavailable platforms visible.
- For verified mode, require empty carts and enabled provider writes.
- Create a proposal only from selected eligible providers.
- Do not mutate carts.

**Acceptance**

- Preflight performs zero cart writes.
- A non-empty cart blocks verified mode.
- Estimated mode remains available when writes are disabled.
- Errors are returned per platform.

### Task 6: Refactor estimated comparison

**Files**

- Modify `app/orchestrator.py`
- Modify `app/compare.py`
- Modify `app/matcher.py`
- Extend `tests/test_orchestrator.py`
- Extend `tests/test_compare.py`
- Extend `tests/test_cross_platform_matcher.py`

**Work**

- Filter the run to eligible, user-selected providers.
- Preserve `not_connected`, `unavailable`, and `failed` outcomes.
- Search platforms concurrently.
- Apply exact-match constraints and controlled substitution rules.
- Enforce both minimum and maximum fill ratios.
- Build estimated bill summaries without calling `add_items()` or `cart_summary()`.
- Generate a frozen proposal suitable for user review.

**Acceptance**

- Estimated mode never writes or reads a mutated cart.
- Smaller baskets cannot win on price.
- Oversized substitutions outside 110% require explicit user approval.
- Platform failures do not erase healthy results.
- Every estimate is visibly marked.

### Task 7: Add proposal review and override APIs

**Files**

- Modify `app/main.py`
- Modify `app/comparison_service.py`
- Add `tests/test_comparison_proposals.py`

**Work**

- Allow candidate changes, exact-match requirements, removals, and platform exclusions.
- Recalculate coverage and estimates after every override.
- Hash and freeze the approved proposal.
- Reject stale or tampered proposals during verified preflight.

**Acceptance**

- Product IDs must belong to the corresponding provider’s candidate set.
- Overrides cannot increase quantities beyond configured limits.
- Any edit invalidates an earlier verification token.

### Task 8: Implement verified comparison as a state machine

**Files**

- Create `app/comparison_operations.py`
- Modify `app/orchestrator.py`
- Modify `app/main.py`
- Add `tests/test_verified_comparison.py`
- Add `tests/test_comparison_idempotency.py`

**Work**

- Issue a short-lived, single-use confirmation token after successful preflight.
- Recheck empty carts immediately before mutation.
- Add approved lines to providers concurrently.
- Record successful writes in the operation ledger as they occur.
- Read and reconcile real cart summaries.
- Convert partial platform failures into failed outcomes.
- Never retry an uncertain write blindly.

**Acceptance**

- No valid token means no write.
- A stale preflight means no write.
- One non-empty cart aborts the run before all writes.
- Repeated verify requests do not duplicate quantities.
- Real totals are rankable only when reconciled.

### Task 9: Implement winner selection and cleanup

**Files**

- Modify `app/comparison_operations.py`
- Modify `app/main.py`
- Add `tests/test_comparison_cleanup.py`

**Work**

- Support:
  - keep winner and clean losers,
  - keep all carts,
  - cancel and clean every comparison cart.
- Remove only operation-ledger quantities.
- Verify each cleanup.
- Preserve the winning cart for manual checkout outside this app.
- Return per-platform cleanup status and remediation messages.

**Acceptance**

- Cleanup is idempotent.
- Unrelated items cannot be removed.
- A cleanup failure remains visible and retryable.
- Winner selection never calls checkout or payment.

### Task 10: Build the comparison interface

**Files**

- Modify `static/index.html`
- Modify `static/app.js`
- Modify `static/styles.css`

**Work**

- Add estimated/verified mode controls.
- Add platform cards and connection readiness.
- Add preflight blocking states.
- Add item-by-platform proposal table.
- Add substitution and quantity warnings.
- Add candidate override controls.
- Add verified-comparison confirmation.
- Add final bill breakdown:
  - item subtotal,
  - each fee,
  - discounts,
  - final total,
  - ETA,
  - coverage,
  - estimated/verified badge.
- Add recommendation reasons and savings.
- Add winner/cleanup actions with progress and recovery states.

**Acceptance**

- A user can complete estimated comparison without cart mutation.
- Verified mode explains the mutation before confirmation.
- Estimated and failed outcomes cannot look verified.
- Mobile layout supports horizontally dense comparison data accessibly.
- Keyboard and screen-reader labels cover all interactive controls.

### Task 11: Add integration and safety coverage

**Files**

- Add `tests/test_comparison_api.py`
- Add `tests/test_comparison_safety.py`
- Add fixture payloads for all providers
- Update provider-specific tests

**Work**

- Test the complete estimated API flow with fake providers.
- Test verified preflight, token use, mutation, reconciliation, ranking, and cleanup.
- Test one-provider and two-provider degradation.
- Test expired sessions and mid-run failures.
- Assert that checkout/payment/order placement identifiers are absent from routes,
  providers, allowed tools, and UI actions.

**Acceptance**

- Full suite passes.
- Safety tests prove no mutation without explicit verified confirmation.
- Failure injection cannot produce a false cheap winner.

### Task 12: Perform gated live verification

**Order**

1. Instamart read-only estimated run.
2. Blinkit read-only estimated run.
3. Zepto read-only estimated run.
4. Three-platform estimated run.
5. One provider verified with an empty cart and one low-cost item.
6. Cleanup verification.
7. Two-provider verified run.
8. Three-provider verified run only after the earlier gates pass.

**Rules**

- Keep checkout disabled.
- Use empty carts.
- Start with one inexpensive item.
- Capture only sanitized payload shapes in fixtures.
- Stop immediately on reconciliation or cleanup failure.
- Do not enable a provider’s writes until its read-only path passes.

**Acceptance**

- Three-platform estimated comparison completes.
- Verified comparison returns reconciled real totals on every healthy provider.
- Losing carts return to empty.
- Winner cart contains only the approved comparison quantities.
- No order is placed.

### Task 13: Documentation and operational handoff

**Files**

- Modify `README.md`
- Modify `.env.example`
- Add `docs/comparison-safety.md`
- Add `docs/comparison-live-test-checklist.md`

**Work**

- Document provider setup and browser-profile isolation.
- Document estimated versus verified totals.
- Document empty-cart requirements.
- Document substitution rules.
- Document recovery from failed cleanup.
- Document the hard checkout boundary.

## Ranking contract

Ranking remains deterministic:

1. Exclude non-`ok` and unreconciled outcomes.
2. Full coverage beats partial coverage.
3. Partial coverage beats missing items.
4. Within a coverage tier, lower final total wins.
5. If totals differ by at most `ETA_TIEBREAK_RUPEES`, faster ETA wins.
6. Estimated reports may recommend an estimated winner, but must label the
   recommendation and every total as estimated.
7. Substitutions, unverified quantities, and platform failures are displayed as
   disclosures, never hidden scoring weights.

## Definition of done

The feature is complete only when:

- Blinkit, Zepto, and Instamart are registered providers.
- All three can search at the same delivery location.
- Estimated comparison works without cart mutation.
- Proposed equivalents can be reviewed and overridden.
- Verified comparison requires explicit confirmation and empty carts.
- Real totals include readable fees and discounts and reconcile arithmetically.
- The comparison screen shows item and bill breakdowns.
- Coverage prevents smaller or missing baskets from winning on price.
- The user can keep a winner, keep all carts, or cancel.
- Losing comparison carts are restored automatically and verifiably.
- Checkout and order placement remain unavailable.
- Automated tests and the gated live checklist pass.

## Recommended delivery slices

1. **Slice A — Estimated MVP:** Tasks 1, 2, 5, 6, 7, and estimated portions of 10–11.
2. **Slice B — Blinkit + Instamart verified beta:** Tasks 3, 4, 8, 9, and gated
   live verification for those providers.
3. **Slice C — Zepto and three-platform verified:** Complete Task 2’s live cart
   work, then Tasks 8–12 for all three providers.
4. **Slice D — Documentation and hardening:** Task 13 plus failure-recovery polish.

Do not expose verified comparison in the UI until the selected providers pass
their live add, cart-summary, reconciliation, and cleanup gates.
