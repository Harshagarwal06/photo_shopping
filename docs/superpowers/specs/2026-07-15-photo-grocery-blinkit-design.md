# Photo Shopping — Grocery Cart Assistant for Blinkit

**Date:** 2026-07-15
**Status:** Approved design
**Audience:** Personal tool for a single user, running locally on macOS.
**Inspiration:** Uber's Cart Assistant (multi-stage LLM pipeline: intent →
draft cart → user review → checkout), scaled down to a single-user tool.

## Goal

Give the system a grocery intent — a photo of a handwritten list, a typed
request, or both — and it produces a draft Blinkit cart: it interprets the
request (including expanding dishes into ingredients), finds the
best-matching products on Blinkit, applies budget/price constraints, works
out purchasable quantities, and lets the user review everything before it
adds the confirmed products to the user's Blinkit cart automatically. The
user completes checkout themselves in the Blinkit app.

Example inputs it must handle:
- Photo of a handwritten list ("doodh 2L, atta, 12 ande")
- "paneer butter masala for 4, plus dish soap, under ₹800 total"
- Photo + typed addendum ("also add coffee, the cheapest 200g pack")

## Constraints and context

- Blinkit has no public API. Integration is via Playwright browser automation
  of blinkit.com using the user's own logged-in session. Acceptable because
  this is a single-user personal tool.
- Handwriting interpretation uses an open model chosen by the user
  (`Qwen/Qwen2.5-VL-7B-Instruct`) called through the Hugging Face Inference
  API with the user's `HF_TOKEN`. No Anthropic/OpenAI APIs.
- Lists may be in English, Hindi (Devanagari), or Hinglish, with quantities
  ("doodh 2L", "atta x1").
- Everything runs locally: one Python process, opened in the user's browser.

## Architecture

The backend is a pipeline of focused stages (Uber's "multi-prompt state
graph", scaled down). LLM stages produce structured JSON; deterministic
stages validate and enforce. Each stage has one responsibility and a typed
input/output, so failures are inspectable per stage.

```
Browser (single page UI)
        │ HTTP
FastAPI backend (app/main.py)
        │
        ├── 1. planner.py     — photo and/or text → planned items      (LLM, vision+text)
        ├── 2. blinkit.py     — planned item → candidate products      (Playwright)
        ├── 3. matcher.py     — candidates → ranked pick + quantity    (LLM, text-only)
        ├── 4. constraints.py — price caps, budget, qty validation     (deterministic)
        │         ▼
        │   draft cart → user review in UI → confirm
        │         ▼
        └── 5. blinkit.py     — add confirmed products to cart         (Playwright)
```

### Repository layout

```
photo_shopping/
├── app/
│   ├── main.py        # FastAPI app: API routes + serves static frontend
│   ├── planner.py     # photo/text → planned items + constraints (cart plan)
│   ├── matcher.py     # candidates → best pick + purchasable quantity
│   ├── constraints.py # deterministic: price caps, budget totals, qty sanity
│   └── blinkit.py     # Playwright client: ensure_login, search, add_to_cart
├── static/            # single-page frontend: plain HTML/CSS/JS, no build step
├── browser_profile/   # persistent Chromium profile with Blinkit session (gitignored)
├── .env               # HF_TOKEN (gitignored)
└── requirements.txt
```

## Pipeline stages

1. **Cart plan (`planner.py`, LLM).** Input: photo and/or typed text — the UI
   always offers both. Output: structured plan:
   `{items: [{search_term, context, quantity, unit, raw_text, source}], constraints: {cart_budget, item_caps, preferences}}`.
   - Normalises Hindi/Hinglish to English search terms ("doodh" → "milk"),
     keeping `raw_text` for display.
   - **Intent expansion:** a dish ("paneer butter masala for 4") expands into
     ingredient items, each tagged `source: "expanded from: <dish>"` so the
     review UI can group them and the user can drop staples they already own.
   - **Constraint capture:** explicit budgets/caps/preferences ("under ₹800",
     "cheapest") are separated from items, per Uber's design: retrieval
     language (search terms) apart from reasoning context.
   - Search terms keep context out ("gluten-free atta" searches fine, but
     "tomatoes for salad" searches "tomatoes" with context preserved for the
     matcher).
2. **Candidate retrieval (`blinkit.py`, Playwright).** For each planned item,
   type the search term into blinkit.com and scrape the top ~5 results: name,
   pack size, price, image URL, in-stock status, and a handle sufficient to
   add the product later. Results reflect the user's saved delivery address
   (stored in the browser profile). Items are searched sequentially — one
   real browser, personal-sized lists; concurrency is out of scope.
3. **Relevance + quantity judging (`matcher.py`, LLM, text-only).** Given a
   planned item (with context) and its candidates, pick the best match and
   compute purchasable units: "2L doodh" → one 2L pack (or 2 × 1L if no 2L
   exists), "12 ande" → one 12-count tray, never 12 trays. Context steers
   choices (canned vs fresh tomatoes for a stew vs salad). Output includes
   the pick, `units_to_add`, and a one-line reason shown in the UI.
4. **Constraint enforcement (`constraints.py`, deterministic).** No LLM. Using
   real scraped prices: apply per-item caps (swap the pick to the best
   candidate under the cap, else flag), compute the cart total against the
   budget, sanity-check quantities (`units_to_add` × pack size ≈ requested
   amount; cap runaway values). Violations are never silently fixed by
   guesswork — they are flagged for the review step.
5. **Draft cart review (UI).** One card per item: best match pre-selected
   (image, pack size, price, match reason), alternatives alongside, expanded
   ingredients grouped under their dish, running cart total with budget
   indicator. The user can swap picks, edit quantities, edit the query and
   re-search, or remove items. Unmatched items are clearly flagged with an
   editable query. Nothing is added without confirmation.
6. **Cart assembly (`blinkit.py`, Playwright).** On confirm, click "Add" for
   each selected product, respecting `units_to_add`, and report per-item
   success/failure. Checkout and payment happen manually in the Blinkit app.

## Blinkit client (`blinkit.py`)

- Playwright Chromium with a **persistent context** in `browser_profile/`.
- `ensure_login()` — detects the login wall; on first run (or session expiry)
  opens a headful window for manual phone-OTP login and address selection.
  The app never sees or stores credentials; the session lives in the profile.
- `search(query) -> list[Product]` and `add_to_cart(product, qty) -> Result`.
- This is the only fragile module and the only swap point (future: Zepto
  client, or direct private-API calls) — nothing else touches Playwright.
- **Dry-run mode:** performs everything except clicking "Add".

## Model backend

- Backend and model id are config values: `MODEL_BACKEND=hf`,
  `MODEL_ID=Qwen/Qwen2.5-VL-7B-Instruct`. Swapping models, or moving to a
  local Ollama backend later, requires no code changes outside config.
- Known trade-offs, accepted: the photo is sent to the HF inference
  provider's servers; the free tier has monthly credit limits; a 7B model
  will occasionally misread chaotic handwriting — the review step is the
  safety net.
- The planner and matcher stages (intent expansion, constraint capture,
  quantity reasoning) lean harder on model quality than plain OCR does. If
  the 7B model proves unreliable at these, the config-level fix is pointing
  `MODEL_ID` at a larger hosted open model (e.g. Qwen2.5-VL-72B-Instruct)
  for the planner while keeping 7B elsewhere — per-stage model ids are
  supported in config from day one.

## Error handling

- No search results → item shown as unmatched, query editable, never dropped
  silently.
- Add-to-cart failure (out of stock, UI change) → reported per item in the
  final summary.
- HF API errors (rate limit, no active provider) → surfaced directly in the
  UI with the underlying message.
- Blinkit session expired → detected, user prompted to re-login via the
  headful window; no mysterious failures.

## Testing

- `planner.py` / `matcher.py`: unit tests with fixture responses (recorded
  model outputs) validating parsing, normalisation, expansion tagging, and
  ranking logic.
- `constraints.py`: pure functions, fully unit-tested (caps, budget totals,
  quantity sanity checks).
- `blinkit.py`: cannot be meaningfully mocked; verified manually against the
  live site using dry-run mode.
- Frontend: manual testing (personal tool; no build step).

## Out of scope (v1)

- Zepto/Amazon integrations (architecture leaves a clean seam).
- Learning user preferences over time (e.g. "doodh" always = Amul Taaza 1L).
- Automatic checkout or any payment handling.
- Multi-user support, deployment, authentication of the tool itself.
