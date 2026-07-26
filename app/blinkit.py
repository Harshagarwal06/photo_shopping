from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import os
import re
import signal
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .config import Settings
from .demo import demo_search
from .models import AddResult, CartLine, CartSummary, FeeLine, Product


class BlinkitError(RuntimeError):
    """A Blinkit browser error that can be shown in the local UI."""


def _looks_like_profile_conflict(message: str) -> bool:
    """Detect Chromium's "profile already in use" family of launch failures."""
    lowered = message.lower()
    return (
        "existing browser session" in lowered
        or "already in use" in lowered
        or "singletonlock" in lowered
        or "processsingleton" in lowered
    )


# Blinkit answers roughly the seventh rapid search in a session with a shell page:
# correct URL and title, readyState complete, but no product markup at all and a
# body of about seventeen characters. It recovers within seconds. Measured: the
# first six searches succeed in ~1.2s each, then five consecutive empties, then
# success again. An empty scrape is therefore throttling, not an empty catalogue
# — Blinkit answers even nonsense queries with fallback products.
# Spacing the requests is what actually avoids the limit; retrying alone does not,
# because each retry is another request against the same window. Measured over
# twelve consecutive searches: back to back, six succeed and five fail; with this
# gap, twelve of twelve succeed.
MIN_SEARCH_INTERVAL_S = 2.5
SEARCH_RETRY_DELAYS_MS = (0, 4_000, 8_000)

PRICE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d{1,2})?)")
PACK_RE = re.compile(
    r"\b(?:\d+\s*x\s*)?\d+(?:\.\d+)?\s*(?:kg|g|gm|l|ltr|ml|pcs?|pieces?|count|packs?)\b",
    re.IGNORECASE,
)
NON_NAME_RE = re.compile(
    r"^(?:\d+%\s*OFF|\d+\s*MINS?|ADD|OUT OF STOCK|NOTIFY|\d+)$",
    re.IGNORECASE,
)


def _normalise_product_name(name: str) -> str:
    without_pack = re.sub(r"\([^)]*\)", " ", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", without_pack).strip()


def _product_image_key(product: Product) -> str:
    """Return the stable asset filename from Blinkit's resized image URL."""
    source = product.image_url or product.handle
    if not source:
        return ""
    return Path(urlparse(source).path).name


def _history_match_count(name: str, history: Counter[str]) -> int:
    normalised = _normalise_product_name(name)
    exact = history.get(normalised, 0)
    if exact:
        return exact
    tokens = set(normalised.split())
    if not tokens:
        return 0
    return max(
        (
            count
            for previous, count in history.items()
            if len(tokens & set(previous.split())) / len(tokens | set(previous.split())) >= 0.7
        ),
        default=0,
    )


def _products_from_raw(
    query: str,
    raw_products: list[dict],
    limit: int,
    *,
    history: Counter[str] | None = None,
    base_url: str = "https://blinkit.com",
) -> list[Product]:
    history = history or Counter()
    products: list[Product] = []
    for raw in raw_products:
        text = raw.get("text", "")
        price_matches = list(PRICE_RE.finditer(text))
        if not price_matches:
            continue
        prices = [float(match.group(1).replace(",", "")) for match in price_matches]
        price = prices[0]
        mrp = next((value for value in prices[1:] if value > price), None)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        name = next(
            (
                line
                for line in lines
                if "₹" not in line
                and not PACK_RE.fullmatch(line)
                and not NON_NAME_RE.fullmatch(line)
            ),
            query,
        )
        pack_match = PACK_RE.search(text)
        pack_size = pack_match.group(0) if pack_match else ""
        href = raw.get("href") or ""
        handle = href or raw.get("image") or f"{query}|{name}|{pack_size}|{price}"
        product_id = hashlib.sha1(handle.encode("utf-8")).hexdigest()[:12]
        add_text = raw.get("addText", "")
        discount_match = re.search(r"(\d+(?:\.\d+)?)%\s*OFF", text, re.I)
        eta_match = re.search(r"(\d+)\s*MINS?", text, re.I)
        rating_match = re.search(r"\b([1-5](?:\.\d)?)\s*(?:★|stars?|rating)\b", text, re.I)
        review_match = re.search(r"\b([\d,]+)\s*(?:reviews?|ratings?)\b", text, re.I)
        products.append(
            Product(
                id=product_id,
                name=name,
                pack_size=pack_size,
                price=price,
                mrp=mrp,
                discount_percent=float(discount_match.group(1)) if discount_match else 0,
                delivery_minutes=int(eta_match.group(1)) if eta_match else None,
                rating=float(rating_match.group(1)) if rating_match else None,
                review_count=int(review_match.group(1).replace(",", "")) if review_match else 0,
                past_order_count=_history_match_count(name, history),
                sponsored=bool(raw.get("sponsored")),
                image_url=raw.get("image"),
                in_stock=not bool(re.search(r"out of stock|notify", add_text, re.I)),
                handle=handle,
                product_url=urljoin(base_url, href) if href else None,
                search_query=query,
            )
        )
    return products[:limit]


BILL_TOTAL_LABELS = {"item total", "items total", "subtotal", "sub total", "mrp total"}
GRAND_TOTAL_LABELS = {"grand total", "to pay", "total amount", "bill total"}
ETA_MINUTES_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)
SIGNED_PRICE_RE = re.compile(r"(-?)\s*₹\s*(-?[\d,]+(?:\.\d+)?)")
QUANTITY_RE = re.compile(r"(\d+)\s*[x×]\s*₹", re.IGNORECASE)


def _signed_price(text: str) -> float | None:
    match = SIGNED_PRICE_RE.search(text)
    if not match:
        return None
    value = float(match.group(2).replace(",", ""))
    return -abs(value) if match.group(1) == "-" or value < 0 else value


def _bill_pairs(bill_text: str) -> list[tuple[str, float]]:
    """Pair each bill label with the price on the following line."""
    lines = [line.strip() for line in bill_text.splitlines() if line.strip()]
    pairs: list[tuple[str, float]] = []
    for index, line in enumerate(lines):
        if "₹" in line:
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        amount = _signed_price(following)
        if amount is not None:
            pairs.append((line, amount))
    return pairs


def cart_summary_from_raw(raw: dict, *, provider: str) -> CartSummary:
    """Convert scraped cart text into a CartSummary. Pure; unit-tested.

    ``estimated`` reflects whether the platform actually reported a grand
    total, not whether the cart happens to have zero fees: it is True when no
    recognised grand-total label was found in the bill text, so the total had
    to be derived locally as subtotal + fees. A derived total always
    reconciles against itself (CartSummary.reconciles compares the reported
    total to computed_total), so treating it as verified would silently
    defeat the reconciliation safety check documented on CartSummary.
    """
    lines: list[CartLine] = []
    for entry in raw.get("lines", []) or []:
        text = entry.get("text", "")
        line_total = _signed_price(text.splitlines()[-1] if text.splitlines() else "")
        if line_total is None:
            continue
        quantity_match = QUANTITY_RE.search(text)
        quantity = int(quantity_match.group(1)) if quantity_match else 1
        text_lines = [item.strip() for item in text.splitlines() if item.strip()]
        name = next(
            (item for item in text_lines if "₹" not in item and not PACK_RE.fullmatch(item)),
            "",
        )
        handle = entry.get("handle") or name
        lines.append(
            CartLine(
                product_id=hashlib.sha1(handle.encode("utf-8")).hexdigest()[:12],
                name=name,
                quantity=quantity,
                unit_price=round(line_total / quantity, 2) if quantity else line_total,
                line_total=line_total,
            )
        )

    pairs = _bill_pairs(raw.get("billText", "") or "")
    subtotal = next(
        (amount for label, amount in pairs if label.casefold() in BILL_TOTAL_LABELS),
        round(sum(line.line_total for line in lines), 2),
    )
    reported_total = next(
        (amount for label, amount in pairs if label.casefold() in GRAND_TOTAL_LABELS),
        None,
    )
    fees = [
        FeeLine(label=label, amount=amount)
        for label, amount in pairs
        if label.casefold() not in BILL_TOTAL_LABELS
        and label.casefold() not in GRAND_TOTAL_LABELS
    ]
    total_was_reported = reported_total is not None
    total = (
        reported_total
        if total_was_reported
        else round(subtotal + sum(fee.amount for fee in fees), 2)
    )

    eta_match = ETA_MINUTES_RE.search(raw.get("etaText", "") or "")
    return CartSummary(
        provider=provider,
        lines=lines,
        subtotal=round(subtotal, 2),
        fees=fees,
        total=round(total, 2),
        delivery_eta_minutes=int(eta_match.group(1)) if eta_match else None,
        estimated=not total_was_reported,
        raw_note=(
            ""
            if total_was_reported
            else (
                f"{provider} did not report a total; the total shown was "
                "calculated locally from the subtotal and fees."
            )
        ),
    )


class BlinkitClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._cleanup_registered = False
        self._order_history: Counter[str] | None = None
        self._last_search_at = 0.0

    async def _open_persistent_context(self) -> BrowserContext:
        assert self._playwright is not None
        return await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(Path(self.settings.browser_profile_dir)),
            headless=self.settings.browser_headless,
            viewport={"width": 1380, "height": 900},
            locale="en-IN",
        )

    def _singleton_lock_pid(self) -> int | None:
        """Return the pid recorded in the profile's SingletonLock symlink.

        Chromium locks a user-data-dir with a ``SingletonLock`` symlink whose
        target is ``<hostname>-<pid>``. Returns None when there is no lock.
        """
        lock = Path(self.settings.browser_profile_dir) / "SingletonLock"
        try:
            target = os.readlink(lock)
        except OSError:
            return None
        pid = target.rsplit("-", 1)[-1]
        return int(pid) if pid.isdigit() else None

    def _remove_stale_lock(self) -> bool:
        """Clear the profile's Singleton lock files if their owner is gone.

        Returns True only when a *stale* lock (no live owning process) was
        removed. A lock held by a live process is a real conflict and is left
        untouched, to avoid corrupting a profile that is genuinely in use.
        """
        pid = self._singleton_lock_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass  # owner is gone -> the lock is stale
        except OSError:
            return False  # owner exists but is unreachable -> a real conflict
        else:
            return False  # owner is alive -> a real conflict
        profile = Path(self.settings.browser_profile_dir)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            (profile / name).unlink(missing_ok=True)
        return True

    def _register_cleanup(self) -> None:
        # atexit backstop only: SIGTERM/SIGINT belong to uvicorn's graceful
        # shutdown (which runs lifespan -> close()); hijacking them would stop
        # that cleanup from running. atexit fires after the loop has stopped.
        if self._cleanup_registered:
            return
        atexit.register(self._terminate_orphan_browser)
        self._cleanup_registered = True

    def _terminate_orphan_browser(self) -> None:
        """Best-effort teardown at interpreter exit so a hard restart does not
        leave a Chromium process holding the profile lock."""
        pid = self._singleton_lock_pid()
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    async def _get_page(self) -> Page:
        if self._page and not self._page.is_closed():
            return self._page
        self.settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = await async_playwright().start()
            try:
                self._context = await self._open_persistent_context()
            except Exception as exc:
                # A previous session may have crashed and left its lock behind.
                # If that owner is gone, clear the stale lock and retry once.
                if _looks_like_profile_conflict(str(exc)) and self._remove_stale_lock():
                    self._context = await self._open_persistent_context()
                else:
                    raise
        except Exception as exc:
            if self._playwright:
                await self._playwright.stop()
            self._playwright = None
            message = str(exc)
            if "Executable doesn't exist" in message:
                message = (
                    "Blinkit's browser is not installed. Run "
                    "`.venv/bin/playwright install chromium`, then try again."
                )
            elif _looks_like_profile_conflict(message):
                message = (
                    "Blinkit's browser is still running from a previous session. "
                    "Close that Chromium window (or restart the app) and try again."
                )
            else:
                message = f"Blinkit's browser could not start: {message.splitlines()[0]}"
            raise BlinkitError(message) from exc
        self._context.set_default_timeout(self.settings.navigation_timeout_ms)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._register_cleanup()
        return self._page

    async def _login_visible(self, page: Page) -> bool:
        markers = [
            page.get_by_text(re.compile(r"^log\s*in$", re.I)),
            page.get_by_text(re.compile(r"login.*phone|phone.*login", re.I)),
            page.locator('input[type="tel"]'),
        ]
        for marker in markers:
            try:
                if await marker.first.is_visible(timeout=900):
                    return True
            except Exception:
                continue
        return False

    async def ensure_login(self, *, wait_for_user: bool = True) -> bool:
        if self.settings.demo_mode:
            return True
        async with self._lock:
            page = await self._get_page()
            await page.goto(self.settings.blinkit_base_url, wait_until="domcontentloaded")
            if not await self._login_visible(page):
                return True
            if self.settings.browser_headless:
                raise BlinkitError(
                    "Blinkit needs a phone-OTP login. Set BROWSER_HEADLESS=false and try again."
                )
            if not wait_for_user:
                return False
            try:
                await page.wait_for_function(
                    r"""() => !document.querySelector('input[type="tel"]') &&
                    ![...document.querySelectorAll('button,div')].some(el =>
                      /^log\s*in$/i.test((el.textContent || '').trim()) && el.offsetParent !== null)""",
                    timeout=300_000,
                )
            except Exception as exc:
                raise BlinkitError(
                    "Blinkit login was not completed within five minutes. Finish login and try again."
                ) from exc
            return True

    @property
    def _history_cache_path(self) -> Path:
        return Path(self.settings.browser_profile_dir) / "order_history.json"

    def _read_history_cache(self) -> Counter[str] | None:
        try:
            payload = json.loads(self._history_cache_path.read_text(encoding="utf-8"))
            if time.time() - float(payload["updated_at"]) > 24 * 60 * 60:
                return None
            return Counter({str(name): int(count) for name, count in payload["products"].items()})
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _write_history_cache(self, history: Counter[str]) -> None:
        try:
            self._history_cache_path.write_text(
                json.dumps(
                    {"updated_at": time.time(), "products": dict(history)},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    async def _get_order_history(self, page: Page) -> Counter[str]:
        if self._order_history is not None:
            return self._order_history
        cached = self._read_history_cache()
        if cached is not None:
            self._order_history = cached
            return cached

        history: Counter[str] = Counter()
        limit = self.settings.order_history_limit
        if limit <= 0:
            self._order_history = history
            return history
        try:
            orders_url = f"{self.settings.blinkit_base_url}/account/orders"
            await page.goto(orders_url, wait_until="domcontentloaded")
            await page.wait_for_selector(
                '[role="button"] img[src*="/cms-assets/cms/product/"]',
                timeout=8_000,
            )
            order_cards = page.locator(
                '[role="button"]',
                has=page.locator('img[src*="/cms-assets/cms/product/"]'),
            )
            count = min(await order_cards.count(), limit)
            for index in range(count):
                order_cards = page.locator(
                    '[role="button"]',
                    has=page.locator('img[src*="/cms-assets/cms/product/"]'),
                )
                await order_cards.nth(index).click()
                await page.wait_for_timeout(350)
                names = await page.evaluate(
                    r"""() => {
                      const lines = (document.body?.innerText || '')
                        .split(/\n+/).map(line => line.trim()).filter(Boolean);
                      const start = lines.findIndex(line => /items? in this order/i.test(line));
                      const end = lines.findIndex(
                        (line, index) => index > start && /^bill details$/i.test(line)
                      );
                      if (start < 0 || end < 0) return [];
                      const section = lines.slice(start + 1, end);
                      const names = [];
                      for (let i = 0; i < section.length - 2; i++) {
                        if (/\s+x\s+\d+$/i.test(section[i + 1]) && /^₹[\d,]+/.test(section[i + 2])) {
                          names.push(section[i]);
                        }
                      }
                      return names;
                    }"""
                )
                history.update(_normalise_product_name(name) for name in names if name)
                await page.goto(orders_url, wait_until="domcontentloaded")
        except Exception:
            # Ranking still works without order history if Blinkit's account UI changes.
            history = Counter()
        self._order_history = history
        self._write_history_cache(history)
        return history

    async def search(self, query: str) -> list[Product]:
        query = query.strip()
        if not query:
            return []
        if self.settings.demo_mode:
            return demo_search(query)
        await self.ensure_login()
        async with self._lock:
            page = await self._get_page()
            history = await self._get_order_history(page)
            for delay_ms in SEARCH_RETRY_DELAYS_MS:
                if delay_ms:
                    await page.wait_for_timeout(delay_ms)
                raw_products = await self._scrape_search(page, query)
                if raw_products:
                    return _products_from_raw(
                        query,
                        raw_products,
                        self.settings.search_result_limit,
                        history=history,
                        base_url=self.settings.blinkit_base_url,
                    )
            raise BlinkitError(
                f"Blinkit returned an empty page for “{query}” on "
                f"{len(SEARCH_RETRY_DELAYS_MS)} attempts, which is how it responds to "
                "rapid searching. Wait a minute and try again."
            )

    async def _scrape_search(self, page: Page, query: str) -> list[dict]:
        """Run one search and return the raw product rows found on the page."""
        gap = MIN_SEARCH_INTERVAL_S - (time.monotonic() - self._last_search_at)
        if gap > 0:
            await page.wait_for_timeout(gap * 1_000)
        self._last_search_at = time.monotonic()
        url = f"{self.settings.blinkit_base_url}/s/?q={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(
                'a[href*="/prn/"], [data-testid*="product"], '
                '[role="button"][data-pf="reset"] img[src*="/cms-assets/cms/product/"]',
                timeout=12_000,
            )
        except Exception:
            # Blinkit serves a shell with no product markup when it is throttling;
            # the caller retries rather than reporting the products as missing.
            pass
        return await page.evaluate(
            """(limit) => {
                  const links = [...document.querySelectorAll('a[href*="/prn/"]')];
                  const modernRoots = [
                    ...new Set(
                      [...document.querySelectorAll(
                        '[role="button"][data-pf="reset"] img[src*="/cms-assets/cms/product/"]'
                      )]
                        .map(img => img.closest('[role="button"][data-pf="reset"]'))
                        .filter(root => root && /₹\\s*[\\d,]+/.test(root.innerText || ''))
                    )
                  ];
                  const legacyRoots = [
                    ...document.querySelectorAll(
                      '[data-testid*="product-card"], [class*="Product__"]'
                    )
                  ];
                  const roots = links.length ? links : modernRoots.length ? modernRoots : legacyRoots;
                  const seen = new Set();
                  const out = [];
                  for (const node of roots) {
                    const root = modernRoots.includes(node)
                      ? node
                      : node.closest(
                          '[data-testid*="product-card"], article, li, [class*="Product"]'
                        ) || node.parentElement || node;
                    const hrefNode = node.matches('a[href]') ? node : root.querySelector('a[href]');
                    const href = hrefNode?.href || '';
                    const text = (root.innerText || '').trim();
                    if (!text || seen.has(href || text)) continue;
                    seen.add(href || text);
                    const img = root.querySelector('img');
                    const add = [...root.querySelectorAll('button, [role="button"]')]
                      .find(b => /^(add|out of stock|notify)$/i.test(
                        (b.innerText || b.textContent || '').trim()
                      ));
                    out.push({
                      text,
                      href,
                      image: img?.currentSrc || img?.src || null,
                      addText: add?.innerText || '',
                      sponsored: Boolean(root.querySelector('img[src*="ad_without_bg"]'))
                    });
                    if (out.length >= limit) break;
                  }
                  return out;
                }""",
            self.settings.search_result_limit,
        )

    async def add_to_cart(self, product: Product, qty: int) -> AddResult:
        if qty < 1:
            raise BlinkitError("Cart quantity must be at least one.")
        if not self.settings.cart_mutations_allowed_for("blinkit"):
            mode = (
                "Demo mode"
                if self.settings.demo_mode
                else "Safety lock"
                if self.settings.safety_lock
                else "Dry run"
            )
            return AddResult(
                product_id=product.id,
                product_name=product.name,
                requested_units=qty,
                success=True,
                dry_run=True,
                message=f"{mode}: would add {qty} × {product.name}; no Blinkit button was clicked.",
            )
        await self.ensure_login()
        async with self._lock:
            page = await self._get_page()
            try:
                root = await self._find_product_card(
                    page,
                    product.search_query or product.name,
                    product,
                )
                if root is None and _normalise_product_name(
                    product.search_query
                ) != _normalise_product_name(product.name):
                    root = await self._find_product_card(page, product.name, product)
                if root is None:
                    raise BlinkitError(
                        f"{product.name} is currently unavailable in Blinkit's search results."
                    )

                add = root.locator('[role="button"], button').filter(
                    has_text=re.compile(r"^ADD$", re.I)
                )
                plus = root.locator("button:has(.icon-plus)")
                if await add.count() and await add.first.is_visible():
                    await add.first.click()
                    remaining = qty - 1
                elif await plus.count() and await plus.first.is_visible():
                    # The product is already in the cart; add the full requested amount.
                    remaining = qty
                else:
                    raise BlinkitError("Blinkit's Add control was not available for this product.")
                for _ in range(remaining):
                    await plus.first.click()
                return AddResult(
                    product_id=product.id,
                    product_name=product.name,
                    requested_units=qty,
                    success=True,
                    message=f"Added {qty} × {product.name}.",
                )
            except Exception as exc:
                return AddResult(
                    product_id=product.id,
                    product_name=product.name,
                    requested_units=qty,
                    success=False,
                    message=f"Blinkit could not add this item: {str(exc).splitlines()[0]}",
                )

    async def cart_summary(self) -> CartSummary:
        await self.ensure_login()
        async with self._lock:
            page = await self._get_page()
            await page.goto(
                f"{self.settings.blinkit_base_url}/cart",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(500)
            raw = await page.evaluate(
                r"""() => {
                  const text = node => (node?.innerText || '').trim();
                  const productRoots = [
                    ...new Set(
                      [...document.querySelectorAll(
                        'button, [role="button"], [data-testid*="cart"]'
                      )]
                        .map(node => node.closest('article, li, [role="listitem"], section, div'))
                        .filter(node => node && /₹\s*[\d,]+/.test(text(node))
                          && node.querySelector('img'))
                    )
                  ];
                  const lines = productRoots.slice(0, 100).map(node => ({
                    text: text(node),
                    handle: node.querySelector('a[href*="/prn/"]')?.href
                      || node.querySelector('img')?.src || text(node).slice(0, 120)
                  }));
                  const billHeading = [...document.querySelectorAll('h1,h2,h3,h4,div,span')]
                    .find(node => /^bill details$/i.test(text(node)));
                  const billRoot = billHeading?.closest('section, article, [role="dialog"], div');
                  const etaNode = [...document.querySelectorAll('body *')]
                    .find(node => /delivery\s+(?:in|by)\s+\d+\s*(?:min|minute)/i.test(text(node))
                      && node.children.length < 4);
                  return {
                    lines,
                    billText: text(billRoot),
                    etaText: text(etaNode)
                  };
                }"""
            )
            return cart_summary_from_raw(raw, provider="blinkit")

    async def remove_from_cart(self, product: Product, qty: int) -> None:
        if qty < 1:
            return
        if not self.settings.cart_mutations_allowed_for("blinkit"):
            raise BlinkitError("Blinkit cart writes are disabled.")
        await self.ensure_login()
        async with self._lock:
            page = await self._get_page()
            await page.goto(
                f"{self.settings.blinkit_base_url}/cart",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(400)
            cards = page.locator(
                'article, li, [role="listitem"], [data-testid*="cart"]',
                has_text=product.name,
            )
            if not await cards.count():
                raise BlinkitError(
                    f"{product.name} was not found in the Blinkit cart during cleanup."
                )
            card = cards.first
            minus = card.locator(
                'button:has(.icon-minus), button[aria-label*="decrease" i], '
                '[role="button"][aria-label*="decrease" i]'
            )
            if not await minus.count():
                minus = card.get_by_text(re.compile(r"^[−-]$"))
            if not await minus.count():
                raise BlinkitError(
                    f"Blinkit's quantity control was unavailable for {product.name}."
                )
            for _ in range(qty):
                if not await minus.first.is_visible():
                    break
                await minus.first.click()
                await page.wait_for_timeout(120)

    async def _find_product_card(self, page: Page, query: str, product: Product):
        """Find a hydrated product card without clicking it.

        Blinkit can reorder results between requests, so match the stable product
        image asset first and use the product name only as a fallback.
        """
        await page.goto(
            f"{self.settings.blinkit_base_url}/s/?q={quote_plus(query)}",
            wait_until="domcontentloaded",
        )
        try:
            await page.wait_for_selector(
                '[role="button"][data-pf="reset"] '
                'img[src*="/cms-assets/cms/product/"], '
                'a[href*="/prn/"], [data-testid*="product-card"]',
                timeout=12_000,
            )
        except Exception:
            return None

        product_roots = page.locator(
            '[role="button"][data-pf="reset"]',
            has=page.locator('img[src*="/cms-assets/cms/product/"]'),
        ).filter(has_text=PRICE_RE)
        image_key = _product_image_key(product)
        if image_key:
            by_image = product_roots.filter(
                has=page.locator(f'img[src*="{image_key}"]')
            )
            if await by_image.count():
                return by_image.first

        by_name = product_roots.filter(has_text=product.name)
        if await by_name.count():
            return by_name.first

        legacy_cards = page.locator(
            'a[href*="/prn/"], [data-testid*="product-card"], [class*="Product__"]',
            has_text=product.name,
        )
        return legacy_cards.first if await legacy_cards.count() else None

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._context = None
        self._playwright = None
        self._page = None
