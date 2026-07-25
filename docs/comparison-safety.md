# Comparison safety boundary

## Estimated mode

- Never calls a provider cart-write method.
- Uses current product prices and explicitly labelled fee estimates.
- May recommend an estimated winner, but never labels the total verified.

## Verified mode

- Requires every selected platform to be connected and capable of cart read, add,
  and operation-scoped cleanup.
- Requires every selected cart to be empty.
- Requires global safety flags and the provider-specific cart-write flag.
- Issues a short-lived, single-use confirmation token only after preflight.
- Rechecks preflight immediately before mutation.
- Records added quantities under one operation ID.
- Reads and reconciles real totals before ranking.

## Cleanup

- `keep_winner` removes comparison quantities from losing platforms.
- `keep_all` makes no cleanup calls.
- `clear_all` removes comparison quantities from every participating platform.
- Cleanup is idempotent and refuses unknown operation IDs.
- Provider cleanup must subtract only operation-ledger quantities.

## Permanent boundary

The application has no checkout, payment, or order-placement route, provider method,
allowed MCP tool, browser action, or interface control.
