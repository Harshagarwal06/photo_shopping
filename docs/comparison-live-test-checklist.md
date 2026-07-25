# Live comparison verification checklist

Keep every cart empty and checkout disabled.

1. Confirm the full automated test suite passes.
2. Connect one provider and run an estimated one-item comparison.
3. Confirm the result is labelled estimated and the provider cart is unchanged.
4. Repeat read-only estimated testing for Blinkit, Instamart, and Zepto.
5. Run an estimated three-platform comparison.
6. Enable writes for one provider only after its cart read and cleanup paths pass.
7. Use one inexpensive item and run verified preflight.
8. Confirm a non-empty cart blocks the run before mutation.
9. Confirm a valid empty-cart run produces a reconciled real total.
10. Select `clear_all` and verify the cart returns to empty.
11. Repeat for a two-provider run, then three providers.
12. Stop on any reconciliation or cleanup failure. Do not proceed to checkout.
