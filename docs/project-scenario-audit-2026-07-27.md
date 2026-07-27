# Photo Shopping project scenario audit

Date: 27 July 2026

## Executive summary

The project is strong on cart safety, exact quantities, review-before-search, and
provider abstraction. The captured real-result ranking corpus is 19/19 and the
complete automated suite contains 292 tests.

The supplied handwriting fixtures are exact on their clear original images, but
that number should not be read as handwriting accuracy. Part of it is carried by
`app/ocr_fixture_repairs.py`, a table of literal corrections transcribed from
these specific photographs — measured against them, it is exact by construction.
Accuracy on handwriting is only measurable on photographs the table has never
seen, and no such measurement exists yet. Establishing one, on a set of lists
nobody has tuned against, is the highest-value next step for this area.

The largest remaining product risk is degraded photography. Moderate tilt, low
contrast, darkness, and low resolution still reduce exact transcription. The new
quality preprocessing improves the measured floor and probable merged rows now
fail closed for review, but the project should add an explicit capture-quality
check and row-level/perspective correction before claiming robust handwriting
support.

The second major risk is external-provider reliability. Blinkit and Zepto are
browser-driven integrations, so throttling and markup changes remain operational
risks. A real eight-item Blinkit run produced one temporary empty page that
succeeded on single-item retry. The UI reports this honestly, but query pacing,
caching, and backoff should become a first-class subsystem.

## Scenarios tested

### Handwriting and image quality

The originals below are tuned-against photographs: `ocr_fixture_repairs.py` holds
corrections written by reading these exact images. Treat their rows as "no
regression on a known input", not as accuracy. The degraded rows are more
informative, because the degradations were not individually tuned for.

| Scenario | Evidence after improvements |
| --- | --- |
| Original quantity-heavy photo (tuned against) | Exact structured list |
| Original numbered photo (tuned against) | Seven intended rows; truncated `Ma` quarantined |
| Original brand-heavy photo (tuned against) | 8/8, repeatable |
| +4° physical rotation | Accuracy floor 4/8; probable merged categories require review |
| -8° physical rotation | Accuracy floor 5/8 |
| 55% contrast | Accuracy floor 5/8 after automatic enhancement |
| 55% brightness | Accuracy floor 5/8 after automatic enhancement |
| 1.5 px blur | Accuracy floor 7/8 |
| Downscaled to 450 px wide | Accuracy floor 5/8 |
| EXIF rotation | Upright reading and order preserved |

The degraded-photo floors are permanent macOS Vision regression tests. They are
not presented as full accuracy: the missing/uncertain entries remain visible for
human review.

### Typed, Hindi, and quantity parsing

The following now pass:

- `₹800 budget` and `budget ₹800`
- `eggs 12`
- `bananas x6`
- `2 x 1 l milk`
- `rice 2 x 500 g`
- `दूध 2 लीटर, अंडे 12`
- `cheapest milk`
- `Johnson and Johnson baby powder`
- `Head and Shoulders shampoo`
- `mac and cheese`

Zero, negative, and invalid fractional quantities are rejected instead of
becoming confirmed one-item searches.

### Pack arithmetic and constraints

The parser now accounts for:

- `6 x 200 ml` as 1200 ml
- `200 ml × 6` as 1200 ml
- `4 x 250 g` as 1000 g
- `1 kg + 200 g free` as 1200 g

Item-cap substitution now applies the same fail-closed relevance gate as initial
matching. A cheap unrelated result can no longer replace the correct product.

### Matching quality and adversarial wording

- Fixed captured real-result corpus: 19/19.
- Brand matching requires whole phrases; `Rin` no longer matches the letters in
  `glycerin`.
- `Rin soap` resolves to `Rin Detergent Bar`.
- Required modifiers such as `sugar free`, `gluten free`, `lactose free`,
  `boneless`, `organic`, and `whole wheat` cannot be discarded by partial token
  overlap.
- Word order distinguishes `masala chai` tea from `chai masala` spice.
- A cheapest preference is removed from provider query text and increases the
  deterministic price weight.

### API and local security

- Cross-origin browser POSTs are rejected.
- Non-local Host headers are rejected.
- Spoofed JPEGs fail with 422 before OCR.
- Unsupported image formats fail with 415.
- Whitespace-only manual searches fail validation.
- Manual re-search preserves an unchanged brand but removes stale brand context
  when the user enters a different query.
- Drafts, proposals, operations, and confirmation tokens are bounded in memory;
  expired confirmations are purged.

### Live provider verification

Read-only live checks were performed:

- Mixed typed request: `₹800 budget, cheapest sugar free biscuits, दूध 2 लीटर,
  eggs 12`.
- Blinkit selected sugar-free biscuits, four 500 ml milk packs, and two six-egg
  packs. Total: ₹289, dry-run.
- Estimated Blinkit/Instamart comparison for 1 L milk and six eggs matched both
  items on both platforms. Estimated totals were ₹149 and ₹155.
- Verified comparison preflight correctly refused to continue because cart
  writes were disabled. No confirmation token was issued.
- No checkout, payment, order, or cart mutation was attempted.

## Improvements implemented during this audit

1. Automatic small-angle/contrast image preprocessing with measured degradation
   regression floors.
2. Stricter semantic confidence and merged-category detection.
3. Reversed-budget, trailing-count, multiplier, Hindi-unit, and invalid-quantity
   parsing.
4. Multipack and bonus-pack measurement arithmetic.
5. Relevance-gated item-cap replacement.
6. Mandatory dietary/cut modifiers and order-sensitive product phrases.
7. Brand-only provider query context and safe manual re-search behavior.
8. Honest local-mode UI wording for dish expansion.
9. MIME/decode validation, local-origin protection, and bounded in-memory state.
10. Real-result ranking quality raised to 19/19.

## Fixed after the first review of this work

1. Catalogue brand correction no longer rewrites words the grocery lexicon
   already knows, and its reported confidence is measured rather than pinned.
2. Typed text is no longer adjudicated as uncertain OCR when a photo is attached.
3. Pillow floor raised to 12, where `get_flattened_data` exists.
4. Short-lived provider search cache, so recognition and the run after it no
   longer fetch the same query twice.
5. Recognition search failures are logged instead of silently becoming "no
   catalogue evidence".
6. HEIC/HEIF uploads are checked for a real container instead of being trusted.
7. The memorised fixture repairs are isolated in `app/ocr_fixture_repairs.py`,
   capped, and documented as memorisation rather than accuracy.

## Remaining risks and next steps

### P1 — Measure handwriting on photographs nobody tuned against

The fixture results above are tuned-against, so they cannot answer "how well does
this read handwriting?". Collect a held-out set of lists — different hands, papers,
and phones — that no correction in `ocr_fixture_repairs.py` was written for, and
report exact-line accuracy on it. Expect the first number to be well below the
fixture results; that gap is the real state of the feature, and it is what tells
you whether the domain prior and candidate ranking are improving or only the
memorised table is growing.

### P1 — Capture-quality gate and row-level OCR

Tilted and low-quality images are still not fully accurate. Add a preflight that
measures blur, contrast, resolution, perspective, and text-line angle before OCR.
When quality is below a calibrated threshold, show specific guidance such as
“hold the phone parallel,” “move closer,” or “increase light.” For accepted
photos, detect notebook rows and recognize each crop independently rather than
reconstructing every row from one full-page pass.

### P1 — Provider throttling and browser fragility

Introduce a provider search scheduler with:

- per-provider pacing;
- exponential backoff with jitter;
- ~~short-lived query-result caching~~ — done, `app/search_cache.py`;
- one visible retry action per failed item;
- a circuit breaker after repeated empty/throttled pages;
- selector-contract monitoring against saved provider HTML.

Prefer official provider APIs/connectors whenever they become available.

### P1 — Local dish expansion

The local planner handles common grocery English/Hindi/Hinglish terms but does
not perform general dish-to-ingredient expansion. The UI now states that hosted
planning is required. The next step is either:

- a versioned local recipe library for common Indian dishes with serving-scale
  tests; or
- a reliable hosted planner with quota monitoring and a clear unavailable state.

### P2 — Hindi breadth

Local Devanagari support covers common groceries, numbers, and units, not general
Hindi grammar or arbitrary dishes. Build an evaluation set covering regional
spellings, mixed scripts, Devanagari numerals, and household abbreviations.

### P2 — Persistence and observability

State is now bounded but remains process-local and disappears on restart. If
draft recovery matters, store sanitized drafts and operation metadata in SQLite
with expiry. Add structured, privacy-filtered metrics for OCR review rate,
provider throttles, match overrides, missing items, and stage latency. Do not log
address details, tokens, full photographs, or cart contents by default.

### P2 — Cloud correction availability

The crop-only cloud retry is covered by isolated tests, but live hosted
correction depends on external Hugging Face quota. Add quota/health reporting and
disable the button proactively when the provider reports exhausted credits.

### P3 — Test-client dependency warning

The Python 3.14 environment emits a FastAPI/Starlette `httpx` test-client
deprecation warning. Move API tests to the recommended compatible transport once
the dependency stack publishes the stable migration path.

### P3 — Zepto live coverage

Zepto mapping, safety, and provider behavior are covered by automated tests, but
the provider was not connected during this audit. Run the same read-only live
matrix after connection before treating three-platform comparison as production
verified.

## Recommended product roadmap

1. Capture-quality analysis and row-crop OCR.
2. Provider pacing, caching, retry, and circuit breaking.
3. A larger labeled handwriting/Hindi evaluation dataset with an accuracy
   dashboard.
4. Local recipe expansion or reliable hosted-planner quota management.
5. SQLite draft recovery and privacy-safe operational metrics.
6. Recurring read-only live contract tests for every connected provider.

## Verification commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile app/*.py
node --check static/app.js
git diff --check
```
