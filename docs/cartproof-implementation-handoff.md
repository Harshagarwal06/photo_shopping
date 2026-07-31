# CartProof implementation handoff

Date: 29 July 2026

Purpose: give an independent reviewer enough detail to verify the CartProof
implementation against the requested plan. This document describes the current
uncommitted working tree, not a released or committed version.

## Requested product behavior

CartProof is intended to sit between a reviewed grocery list and the existing
cross-platform comparison system:

1. Convert the reviewed list into one explicit shopping contract.
2. Let the user classify rules as required, preferred, or flexible.
3. Apply the same confirmed contract to every provider.
4. Produce deterministic evidence for each selected product and cart total.
5. Prevent a cheaper cart from winning if it violates a required rule.
6. Bind exact-cart verification to the contract version that was reviewed.
7. Expose a decision receipt explaining the result.

It does not add checkout or payment.

## Working-tree scope

Modified files:

- `README.md`
- `app/compare.py`
- `app/comparison_service.py`
- `app/main.py`
- `app/models.py`
- `app/orchestrator.py`
- `static/app.js`
- `static/index.html`
- `static/styles.css`
- `tests/test_comparison_api.py`
- `tests/test_comparison_service.py`
- `tests/test_state_store.py`

New files:

- `app/contracts.py`
- `app/cartproof.py`
- `tests/test_cartproof.py`
- `docs/cartproof-implementation-handoff.md` (this audit handoff)

At handoff time these changes are not staged or committed.

## Domain models added

Defined in `app/models.py`.

### `RequirementLevel`

Values:

- `required`
- `preferred`
- `flexible`

### `SubstitutionPolicy`

Values:

- `none`
- `same_brand`
- `equivalent`
- `any`

### `ItemContract`

Fields:

- `planned_item_id`
- `product_name`
- `quantity`
- `unit`
- `quantity_level`
- `brand`
- `brand_level`
- `substitution_policy`
- `min_fill_ratio`, default `0.9`
- `max_fill_ratio`, default `1.1`
- `item_price_cap`

Validation:

- Quantity must be positive.
- Minimum fill ratio must be positive.
- Maximum fill ratio must be at least `1`.
- Maximum cannot be below minimum.
- A required-brand, no-substitution, or same-brand rule requires a non-empty brand.

### `ShoppingContract`

Fields:

- Random UUID-like `id`
- Integer `version`, starting at `1`
- One or more `ItemContract` records
- Optional final `cart_budget`
- `status`: `draft` or `confirmed`
- SHA-256 `fingerprint`

### Evidence models

- `RequirementCheck`
- `ItemProof`
- `PlatformProof`
- `DecisionReceipt`

`PlatformOutcome` now optionally contains `proof`.

`ComparisonReport` now contains:

- `contract_id`
- `contract_version`
- `contract_fingerprint`

`ComparisonProposal` now optionally contains a snapshot of the confirmed contract.

## Contract construction

Implemented in `app/contracts.py`.

### Draft defaults

`build_contract(plan)` creates a conservative draft:

- Quantity becomes required when the parsed unit is not `item`/`pack`, the
  quantity is not `1`, or the raw text contains a digit.
- Otherwise quantity is flexible.
- Brand extraction currently searches the existing fixed
  `PROVIDER_QUERY_BRANDS` list.
- A detected brand is preferred by default.
- A brand becomes required only when the raw wording contains one of a small
  English hard-rule patterns such as `must`, `only`, `exact`,
  `do not substitute`, `no substitution`, or `same brand`.
- A hard brand defaults to `same_brand`.
- A preferred brand defaults to `equivalent`.
- No detected brand defaults to flexible/`any`.
- Existing item caps and the cart budget are copied from `CartPlan.constraints`.

AI/planner output does not silently create most hard restrictions. The UI remains
the confirmation authority.

### Confirmation and fingerprint

`confirm_contract()`:

- Rejects a stale request version.
- Requires the contract item IDs to match the bound plan exactly.
- Rejects duplicate contract item IDs.
- Increments the version only when an already-confirmed contract is confirmed again.
- Computes a SHA-256 fingerprint over:
  - contract ID
  - version
  - sorted item contracts
  - cart budget

The fingerprint is an integrity identifier, not a cryptographic signature or
proof of user identity.

### Applying the contract

`plan_for_contract()` copies contract product names, quantities, and units into
the retrieval plan.

Required/same-brand rules retain the brand in matching context. Preferred brands
are removed from matching context so the existing relevance gate does not
incorrectly turn a preference into a hard requirement. Their substitution is
disclosed later by the proof engine.

## Deterministic proof engine

Implemented in `app/cartproof.py`. It does not call an LLM.

### Item checks

For every contract item:

1. **Availability**
   - Missing selection or zero packs is a hard failure.

2. **Relevance**
   - Reuses `matcher.match_is_reasonable`.
   - Failure is hard.

3. **Brand**
   - Uses case-insensitive whole-phrase matching against the product name.
   - Required brand, `none`, and `same_brand` failures are hard.
   - Preferred-brand failure is a warning.
   - Flexible-brand failure passes.

4. **Quantity**
   - Reuses `units.fill_ratio`, which uses the existing measurement parser.
   - A ratio within the contract's minimum/maximum range passes.
   - An out-of-range required quantity fails.
   - An unparseable required quantity is `unverified`, which makes the platform
     ineligible.
   - Preferred quantity problems are warnings.
   - Flexible quantity problems currently pass with an explanatory message.

5. **Item price cap**
   - Selected product price multiplied by pack count must remain under the cap.
   - Failure is hard.

### Basket checks

1. A cart summary must exist.
2. Subtotal plus disclosed fees must reconcile to the provider-reported total.
3. If a final cart budget exists, the total must be within it.

Budget checks use estimated fees in estimated mode and provider cart totals in
exact mode.

### Platform status

- `compliant`: no hard failures and no preference warnings
- `qualified`: no hard failures but at least one preference warning
- `non_compliant`: at least one failed or unverified required check

`required_failures` counts failed/unverified checks, not unique failed items. One
item may contribute more than one failure.

## Ranking behavior

Implemented in `app/compare.py`.

A platform is rankable only when:

- provider status is `ok`
- a cart summary exists
- the cart arithmetic reconciles
- CartProof is absent for a legacy comparison, or the proof says `eligible`

With a contract, sorting is lexicographic:

1. Existing coverage tier
2. Number of preferred-requirement warnings
3. Final total
4. ETA tiebreak only among carts with the same coverage and warning count and
   within `eta_tiebreak_rupees` of the current best

If every cart is noncompliant, `winner` is `null`; the UI says no compliant cart
was found.

Contract ID, version, and fingerprint are copied into every comparison report.

## Service lifecycle and persistence

Implemented in `app/comparison_service.py`.

### Contract registry

The service keeps bounded in-memory ordered dictionaries for contracts and their
bound plans.

Contracts persist in the existing SQLite state store under
`shopping_contract`, containing:

- serialized contract
- serialized bound cart plan

They use the state store's existing expiry behavior. Default state-record TTL is
24 hours.

### Estimated comparison

`ComparisonService.estimate()`:

- Accepts an optional confirmed contract.
- Validates that its IDs match the plan.
- Applies the contract to the plan.
- Passes the contract through the orchestrator into every outcome/proof.
- Stores a deep contract snapshot in the proposal.

For backward compatibility, a caller that omits the contract receives an
automatically generated and automatically confirmed default contract. The
browser UI does not use this path, but the direct API can.

### Manual product override

Changing a product or pack count:

- Recalculates the estimated summary.
- Rebuilds the CartProof evidence.
- Reranks providers.
- Unfreezes the proposal.
- Invalidates existing exact-total confirmation tokens for that proposal.

### Exact-total verification

The verification token stores the proposal's contract fingerprint.

Immediately before writing comparison quantities, verification checks:

- proposal is still frozen
- current contract exists
- contract is confirmed
- registry fingerprint matches proposal snapshot
- confirmation-token fingerprint matches proposal snapshot
- normal empty-cart/provider-write preflight still passes

Exact cart results are proved and ranked against the same contract snapshot.

### Decision receipts

Receipts are projections of the stored report, not separate signed artifacts.

They expose:

- comparison ID and kind
- contract identity/version/fingerprint
- winner
- estimated/exact flag
- full platform outcomes and proof details
- ranking reasons

## API changes

### Contract endpoints

`POST /api/contracts/preview`

- JSON body: `CartPlan`
- Response: draft `ShoppingContract`

`GET /api/contracts/{contract_id}`

- Response: stored `ShoppingContract`
- Returns 404 when missing/expired

`POST /api/contracts/{contract_id}/confirm`

- JSON body: `ContractConfirmRequest`
- Response: confirmed `ShoppingContract`
- Returns 409 for stale/mismatched/invalid contract state

### Comparison endpoint change

`POST /api/comparisons/estimate`

New optional multipart field:

- `contract_id`

When supplied, the server loads and validates that contract. Missing/expired IDs
return 422.

### Receipt endpoints

`GET /api/comparisons/proposals/{proposal_id}/receipt`

`GET /api/comparisons/{operation_id}/receipt`

Neither endpoint performs cart mutations.

No checkout, payment, or order-placement endpoint was added.

## Browser flow

Implemented in `static/index.html`, `static/app.js`, and `static/styles.css`.

Comparison now follows:

1. User enters text/photo input.
2. Existing transcription review runs when required.
3. Browser requests a draft shopping contract.
4. Contract screen shows every item before provider search.
5. User edits and confirms the contract.
6. Browser submits `contract_id` with the comparison.
7. Comparison cards display CartProof status and expandable item/basket checks.

Editable contract fields:

- Quantity level
- Brand
- Brand level
- Substitution policy
- Minimum supplied percentage
- Maximum supplied percentage
- Optional item price cap
- Optional final cart budget

Product name and quantity are edited in the preceding transcription/list-review
screen rather than duplicated in the contract screen.

Result behavior:

- Winner header says `CartProof recommendation`.
- Winner platform card says `CartProof choice`.
- No eligible result says `No compliant cart`.
- Each platform shows `compliant`, `qualified`, or `non_compliant` proof styling.
- Progress UI now hides when comparison rendering completes.
- Asset cache keys were changed to `cartproof-1`.

## Tests added or extended

### `tests/test_cartproof.py`

Tests:

- conservative contract defaults
- confirmation fingerprint creation
- rejection of a contract missing a plan item
- required-brand failure disqualifies a platform
- preferred-brand substitution remains rankable as a warning
- unparseable pack size cannot pass a required quantity
- cheaper noncompliant cart cannot win
- cart budget is a required basket check

### `tests/test_comparison_service.py`

Added:

- confirmed contract is stored in the proposal and receipt
- editing the contract invalidates an exact-total confirmation

### `tests/test_state_store.py`

Added:

- contract and bound plan recover through a new service/store instance

### `tests/test_comparison_api.py`

Asserts contract and receipt routes exist and the existing no-checkout assertion
still passes.

## Validation already performed

Commands that passed:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/pyright
node --check static/app.js
.venv/bin/python -m py_compile app/*.py
git diff --check
```

Full pytest completed successfully; two pre-existing browser-marked tests were
skipped under the default test command.

Live local browser validation was performed in `DEMO_MODE=true`:

- text list reached the contract editor
- explicit quantities defaulted to required
- contract confirmation succeeded
- comparison request succeeded
- valid two-item list produced a CartProof recommendation
- two provider proof cards rendered
- a missing required item produced no compliant winner
- no browser console error appeared
- progress panel was confirmed hidden after the final result

The browser validation was desktop-sized. A separate mobile viewport pass was
not performed.

## Important limitations and review targets

These should not be mistaken for completed features:

1. **Direct API compatibility bypass**
   - `ComparisonService.estimate()` auto-confirms a default contract when no
     contract is supplied.
   - The browser always confirms explicitly, but a direct API caller can omit
     `contract_id`.
   - Reviewer should decide whether strict enforcement should replace compatibility.

2. **`none` is not exact-SKU enforcement**
   - `none` and `same_brand` currently make brand matching hard.
   - `none` does not prove that every SKU attribute is identical.

3. **Preferred brand does not actively optimize selection**
   - Preferred brands are removed from hard matching context to avoid treating
     them as required.
   - The proof engine warns when they are missed, and ranking prefers fewer
     warnings, but candidate selection is not explicitly biased toward that brand.

4. **Brand extraction is limited**
   - Automatic extraction uses a fixed known-brand list.
   - Unknown brands must be entered manually.
   - Proof checks the product name, not a structured provider brand field.

5. **No ingredient/allergen verification**
   - Dietary and allergy guarantees were intentionally excluded because the
     product model lacks reliable ingredient/label evidence.

6. **Fingerprint is not a signature**
   - It detects content/version changes inside this application.
   - It does not prove who approved the contract.

7. **Receipt is not timestamped or downloadable**
   - JSON endpoints exist.
   - There is no generated timestamp, PDF, or download/copy button.

8. **Legacy receipt behavior**
   - A stored pre-CartProof comparison has no contract metadata.
   - The receipt builder raises `ValueError`; the HTTP receipt route does not
     currently translate that case into a dedicated 409/422 response.

9. **Flexible unverified quantity**
   - An unparseable flexible quantity check currently receives `pass` with an
     explanation that it could not be proved.
   - Reviewer should confirm this is the desired semantics.

10. **No dedicated API integration tests**
    - Route existence is tested.
    - Domain/service behavior is tested.
    - Full request/response tests for contract preview/confirm/receipt endpoints
      have not been added.

11. **External-provider validation**
    - The UI was validated in demo mode.
    - Exact multi-provider cart mutation was not performed during CartProof testing.
    - Existing mutation safety, empty-cart preflight, and checkout blocking remain.

12. **Parser accuracy remains upstream**
    - CartProof proves against the reviewed plan.
    - If the parser produces a malformed item and the user confirms it, CartProof
      does not independently reconstruct the original sentence.

## Current runtime warning

The locally running non-demo server was started using the repository's current
environment. Its health response reported:

- `demo_mode: false`
- `dry_run: false`
- `safety_lock: false`
- `auto_add_to_cart: true`
- `blinkit_cart_writes: true`
- `checkout_disabled: true`

Therefore testing the currently running normal server can modify the Blinkit cart,
although checkout remains disabled. Use `DEMO_MODE=true` or disable cart writes for
read-only review.

## Suggested independent review checklist

Ask the reviewer to:

1. Read `app/models.py`, `app/contracts.py`, and `app/cartproof.py`.
2. Verify required/preferred/flexible semantics against expected product behavior.
3. Challenge brand and substitution rules with adversarial product names.
4. Test under-supply, over-supply, multipacks, bonus packs, unknown pack sizes,
   and dimension mismatches.
5. Confirm a required failure can never win through override, estimated, exact,
   restart-recovery, or missing-provider paths.
6. Verify preference ordering cannot be bypassed by the ETA tiebreak.
7. Test stale contract versions and fingerprint changes through the HTTP API.
8. Test legacy proposals against receipt endpoints.
9. Decide whether direct comparisons must reject missing `contract_id`.
10. Run the full suite and add request-level API tests.
11. Run mobile and keyboard-only UI checks.
12. Use demo mode unless real cart writes are explicitly intended.

## Copy-paste review request

```text
Review the CartProof implementation in this repository as an adversarial code
review. Start with docs/cartproof-implementation-handoff.md, then inspect every
referenced file rather than trusting the document.

Verify:
- the shopping contract is correctly built, confirmed, versioned, fingerprinted,
  persisted, and bound to comparison proposals;
- every required failure or unverified required quantity makes a platform
  ineligible;
- preferred violations affect ranking but do not become hard failures;
- a cheaper noncompliant cart can never win;
- overrides and exact-cart verification recompute or revalidate proof correctly;
- estimated and exact totals both reconcile and respect the final budget;
- no checkout/payment/order route or capability was introduced;
- contract and receipt APIs handle malformed, stale, expired, and legacy state;
- the UI cannot compare through its normal flow without explicit confirmation.

Pay special attention to the limitations listed in the handoff. Run pytest,
ruff, pyright, JavaScript syntax checks, and targeted API/browser tests. Report
findings by severity with exact file and line references. Do not modify code
unless separately asked.
```
