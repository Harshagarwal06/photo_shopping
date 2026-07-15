# Photo Shopping — Handwritten Grocery List to Blinkit Cart

**Date:** 2026-07-15
**Status:** Approved design
**Audience:** Personal tool for a single user, running locally on macOS.

## Goal

Upload a photo of a handwritten grocery list to a local website. The system
reads and interprets the list, finds the best-matching products on Blinkit,
lets the user review the matches, and then adds the confirmed products to the
user's Blinkit cart automatically. The user completes checkout themselves in
the Blinkit app.

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

```
Browser (single page UI)
        │ HTTP
FastAPI backend (app/main.py)
        ├── parser.py   — photo → structured items   (HF Inference API, Qwen2.5-VL)
        ├── matcher.py  — item + candidates → ranked picks (same model, text-only)
        └── blinkit.py  — search + add-to-cart        (Playwright, persistent profile)
```

### Repository layout

```
photo_shopping/
├── app/
│   ├── main.py        # FastAPI app: API routes + serves static frontend
│   ├── parser.py      # photo → list of items
│   ├── matcher.py     # re-rank Blinkit search results per item
│   └── blinkit.py     # Playwright client: ensure_login, search, add_to_cart
├── static/            # single-page frontend: plain HTML/CSS/JS, no build step
├── browser_profile/   # persistent Chromium profile with Blinkit session (gitignored)
├── .env               # HF_TOKEN (gitignored)
└── requirements.txt
```

## Flow

1. **Upload & parse.** User uploads a photo. `parser.py` sends it to
   Qwen2.5-VL via the HF Inference API (`huggingface_hub.InferenceClient`,
   chat completion with image). Output: JSON list of
   `{item, quantity, unit, raw_text}`. The prompt normalises Hindi/Hinglish
   names to English search terms (e.g. "doodh" → "milk") while keeping the
   raw text for display.
2. **Search.** For each item, `blinkit.py` types the query into blinkit.com
   search and scrapes the top ~5 results: name, size, price, image URL,
   in-stock status, and a handle sufficient to add the product later.
   Results reflect the user's saved delivery address (stored in the browser
   profile).
3. **Match & review.** `matcher.py` sends the item plus candidates to the
   same model (text-only) to pick the best match with size/quantity awareness.
   The UI shows one card per item: best match pre-selected, alternatives
   alongside. The user can swap the selection, edit the query and re-search,
   or remove an item. Unmatched items are clearly flagged with an editable
   query.
4. **Add to cart.** On confirm, `blinkit.py` clicks "Add" for each selected
   product, respecting quantities, and reports per-item success/failure.
   Checkout and payment happen manually in the Blinkit app.

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

- `parser.py` / `matcher.py`: unit tests with fixture responses (recorded
  model outputs) validating parsing, normalisation, and ranking logic.
- `blinkit.py`: cannot be meaningfully mocked; verified manually against the
  live site using dry-run mode.
- Frontend: manual testing (personal tool; no build step).

## Out of scope (v1)

- Zepto/Amazon integrations (architecture leaves a clean seam).
- Learning user preferences over time (e.g. "doodh" always = Amul Taaza 1L).
- Automatic checkout or any payment handling.
- Multi-user support, deployment, authentication of the tool itself.
