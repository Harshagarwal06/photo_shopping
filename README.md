# Photo Shopping

A local grocery assistant for typed requests and photographed handwritten lists. It
parses the request, searches a configured grocery provider, ranks one best product for
each requested item, and builds a reviewable draft.

Blinkit, Swiggy Instamart, and Zepto are available from the provider selector.
Instamart uses Swiggy's official MCP endpoint and OAuth 2.1 flow; Blinkit and
Zepto use isolated local browser profiles.

## What is implemented

- English, Hindi, and Hinglish request parsing plus on-device macOS Vision OCR.
- A pre-search transcription review with handwriting crops, alternative readings,
  editable brands/quantities, and uncertain lines excluded by default.
- Semantic OCR confidence, duplicate/page-number filtering, and a fail-closed product
  relevance gate so malformed handwriting cannot silently select an unrelated item.
- An optional Groq Qwen vision retry that sends only isolated uncertain line strips,
  never the complete photograph.
- A local capture-quality preflight for resolution, lighting, contrast, focus, tilt,
  and perspective, with specific retake guidance before unsafe photos reach OCR.
- A common provider interface with Blinkit and Instamart active side by side.
- Official Instamart OAuth 2.1 with PKCE and dynamic client registration.
- OAuth tokens stored in the macOS Keychain, never in the browser or project files.
- Instamart address selection, product search, go-to/previous-item signals, cart reads,
  and guarded cart updates.
- Deterministic product ranking using relevance, pack fit, price, discount, delivery,
  ratings/reviews, sponsorship, and previous-order preference when available.
- Read–merge–replace–verify cart updates, serialized to reduce lost-update races.
- A strict MCP tool allowlist. Checkout, payment, order placement, and address mutations
  are not exposed; cart updates remain separately gated and operation-scoped.
- Estimated cross-platform comparison with item coverage, fee estimates, substitutions,
  delivery details, and a deterministic recommendation.
- Coalesced, paced provider searches with transient retry/backoff and circuit breaking.
- Expiring SQLite recovery for drafts and comparison operations, plus privacy-filtered
  reliability metrics that never contain requests, photos, addresses, tokens, or carts.
- Token-gated verified comparison preflight that requires empty carts and explicit
  provider-specific cart-write opt-ins.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

For the fast hosted handwriting second opinion, add a Groq API key to `.env`:

```dotenv
MODEL_BACKEND=local
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL_ID=qwen/qwen3.6-27b
CLOUD_MODEL_BACKEND=groq
RECOGNITION_POLICY=autonomous_safe
```

Hugging Face and NVIDIA remain supported fallbacks. Typed grocery requests can fall
back to the local parser when hosted inference is unavailable.
The local parser handles common English, Hindi, and Hinglish grocery terms, quantities,
budgets, brands, and price preferences; general dish-to-ingredient expansion requires
hosted planning.

## Configure Instamart

Use these settings in `.env`:

```dotenv
GROCERY_PROVIDER=blinkit
INSTAMART_MCP_URL=https://mcp.swiggy.com/im
SWIGGY_OAUTH_BASE_URL=https://mcp.swiggy.com
SWIGGY_REDIRECT_URI=http://localhost:8000/api/providers/instamart/callback

AUTO_ADD_TO_CART=true
BLINKIT_CART_WRITES=false
INSTAMART_CART_WRITES=false
ZEPTO_CART_WRITES=false
CHECKOUT_DISABLED=true
```

`INSTAMART_CART_WRITES=false` is intentional for the first authenticated test. Search,
ranking, previous-order signals, address selection, and cart reads work without giving
the app permission to mutate the cart. After those responses have been verified for
your account, set it to `true` and restart the server to allow adding the selected
items. Checkout remains unavailable regardless of this setting.

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), choose **Blinkit** or **Swiggy
Instamart** from the provider selector, and connect that service. Instamart returns to
the local app after its OAuth flow; Blinkit keeps the existing saved browser session.

For a photographed request, the app reads the image locally first and pauses before any
provider search. Review the recognized product, brand, quantity, and unit. Low-confidence
lines are unchecked, so they cannot be searched accidentally. Editing a line checks it
for inclusion. If a hosted-model key is configured, the retry action sends only those
isolated line strips; failure leaves the local review unchanged. In review mode,
successful cloud suggestions remain unchecked until you confirm them manually.

With `RECOGNITION_POLICY=autonomous_safe`, the editable transcription checkpoint is
replaced by an automatic decision stage. Local Vision, an independent hosted suggestion,
and selected-provider catalogue results are scored together. Strong readings continue
automatically; unresolved lines are skipped without searching or adding a product. Set
the policy back to `review` to restore the manual checkpoint.

The app is currently configured for port `8000`; the OAuth redirect URI must use the
same port and `localhost` host.

The app answers only on a loopback address. A request whose `Host` is anything other
than `localhost`, `127.0.0.1`, or `::1` is refused, and a browser write from another
origin is refused separately. This blocks DNS rebinding, and it also means the app
cannot be reached from another device on the same network — opening it from a phone at
`http://192.168.x.x:8000` returns 400 by design.

## Compare apps

Choose the platforms under **Compare across apps**, then select **Compare estimated
prices**. Estimated mode searches every connected platform without changing a cart.
It shows the selected products, quantities, coverage warnings, item subtotal, estimated
fees, final total, and recommendation.

**Check verified-mode readiness** is deliberately stricter. It is available only when
every participating provider is connected, its cart is empty, and its provider-specific
cart-write flag is enabled. The app then asks for one explicit confirmation before
adding anything. Checkout and order placement remain unavailable.

For version one, verified comparison refuses to run against non-empty carts. After a
verified comparison, the user can keep the winning cart, keep all carts, or remove only
the quantities recorded for that comparison operation.

## Safety model

The app's Instamart integration is narrower than the remote MCP server:

- Only address reads, product search, go-to items, cart reads/updates, and order-history
  reads are callable.
- There is no checkout or payment route, UI control, provider method, or allowed MCP
  tool.
- Cart updates require `AUTO_ADD_TO_CART=true`, all general safety flags to permit
  mutations, and `INSTAMART_CART_WRITES=true`.
- Each cart mutation reads the current cart, merges requested quantities, sends the
  complete replacement cart, then reads it again to verify the result.

## Blinkit fallback

Blinkit and Zepto use separate persistent Playwright browser profiles because they have
no supported public consumer shopping API in this project. `GROCERY_PROVIDER` only
chooses the initial selection; all providers remain available. Install Chromium once:

```bash
playwright install chromium
```

## Demo and tests

`DEMO_MODE=true` exercises the interface with a local catalogue and never mutates a
real cart. Demo mode parses the request you actually submit; only provider products
and prices are synthetic.

```bash
.venv/bin/python -m pytest
```

The tests cover OAuth state/PKCE, MCP tool allowlisting, Instamart response mapping,
ranking data, cart merging and verification, mutation idempotency, the checkout safety
boundary, and real macOS Vision regressions for numbered and quantity-heavy handwritten
lists.

The broader degraded-photo, parsing, matching, provider, and security results are in
[the 27 July project scenario audit](docs/project-scenario-audit-2026-07-27.md).

For development checks:

```bash
pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pyright
.venv/bin/python -m pytest --cov=app
RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest -m browser
```

The browser test requires Chromium once:

```bash
.venv/bin/python -m playwright install chromium
```

## Held-out handwriting evaluation

Known fixtures are regression inputs, not handwriting-accuracy evidence. Copy
`docs/held-out-handwriting-manifest.example.json`, point it at photographs outside
`tests/fixtures`, and keep `tuned_against` set to `false`:

```bash
.venv/bin/python tools/evaluate_handwriting.py path/to/manifest.json \
  --output handwriting-report.json
```

The evaluator reports exact lines, exact structured items, field accuracy, review
rate, missed items, and unsafe false accepts. It refuses the tuned fixture directory.

## Local diagnostics and recovery

Drafts, proposals, and completed comparison operations survive server restarts for
24 hours by default in `.photo_shopping_state.sqlite3`. Photographs, delivery
addresses, OAuth credentials, and confirmation tokens are never written there.

`GET /api/diagnostics` reports aggregate provider-search and stage-latency health.
It contains no query text, product names, cart contents, or error messages.
