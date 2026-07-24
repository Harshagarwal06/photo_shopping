# Photo Shopping

A local grocery assistant for typed requests and photographed handwritten lists. It
parses the request, searches a configured grocery provider, ranks one best product for
each requested item, and builds a reviewable draft.

Blinkit and Swiggy Instamart are both available from the provider selector. Instamart
uses Swiggy's official MCP endpoint and OAuth 2.1 flow; Blinkit keeps its existing
browser-automation integration.

## What is implemented

- English, Hindi, and Hinglish request parsing plus on-device macOS Vision OCR.
- A common provider interface with Blinkit and Instamart active side by side.
- Official Instamart OAuth 2.1 with PKCE and dynamic client registration.
- OAuth tokens stored in the macOS Keychain, never in the browser or project files.
- Instamart address selection, product search, go-to/previous-item signals, cart reads,
  and guarded cart updates.
- Deterministic product ranking using relevance, pack fit, price, discount, delivery,
  ratings/reviews, sponsorship, and previous-order preference when available.
- Read–merge–replace–verify cart updates, serialized to reduce lost-update races.
- A strict MCP tool allowlist. Checkout, payment, order placement, cart clearing, and
  address mutations are not exposed to the application.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

For hosted request parsing, add a Hugging Face token to `.env`. Typed grocery requests
can fall back to the local parser when hosted inference is unavailable.

## Configure Instamart

Use these settings in `.env`:

```dotenv
GROCERY_PROVIDER=blinkit
INSTAMART_MCP_URL=https://mcp.swiggy.com/im
SWIGGY_OAUTH_BASE_URL=https://mcp.swiggy.com
SWIGGY_REDIRECT_URI=http://localhost:8000/api/providers/instamart/callback

AUTO_ADD_TO_CART=true
INSTAMART_CART_WRITES=false
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

The app is currently configured for port `8000`; the OAuth redirect URI must use the
same port and `localhost` host.

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

Blinkit uses a persistent Playwright browser profile because it has no supported public
consumer shopping API in this project. `GROCERY_PROVIDER=blinkit` only makes it the
initial UI selection; Instamart remains available in the selector. Install Blinkit's
browser once with:

```bash
playwright install chromium
```

## Demo and tests

`DEMO_MODE=true` exercises the interface with a local catalogue and never mutates a
real cart.

```bash
pytest
```

The tests cover OAuth state/PKCE, MCP tool allowlisting, Instamart response mapping,
ranking data, cart merging and verification, mutation idempotency, and the checkout
safety boundary.
