from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .blinkit import PACK_RE, PRICE_RE, cart_summary_from_raw
from .config import Settings
from .models import AddResult, CartSummary, Product


class ZeptoError(RuntimeError):
    """A Zepto browser error that is safe to show in the local UI."""


def zepto_products_from_raw(
    query: str,
    raw_products: list[dict],
    limit: int,
    *,
    base_url: str = "https://www.zeptonow.com",
) -> list[Product]:
    products: list[Product] = []
    for raw in raw_products:
        text = raw.get("text", "")
        price_matches = list(PRICE_RE.finditer(text))
        if not price_matches:
            continue
        prices = [float(m.group(1).replace(",", "")) for m in price_matches]
        price = prices[0]
        mrp = next((value for value in prices[1:] if value > price), None)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        name = raw.get("name") or next(
            (
                line
                for line in lines
                if "₹" not in line
                and not PACK_RE.fullmatch(line)
                and not re.fullmatch(
                    r"(?:add|out of stock|notify(?: me)?|sold out|\d+(?:\.\d+)?%?\s*off)",
                    line,
                    re.I,
                )
            ),
            query,
        )
        pack_size = raw.get("pack") or ""
        pack_match = PACK_RE.search(pack_size or text)
        href = raw.get("href") or ""
        handle = href or raw.get("image") or f"{query}|{name}|{price}"
        discount_match = re.search(r"(\d+(?:\.\d+)?)%\s*OFF", text, re.I)
        eta_match = re.search(r"(\d+)\s*MINS?", text, re.I)
        add_text = raw.get("addText", "")
        products.append(
            Product(
                id=hashlib.sha1(f"zepto|{handle}".encode("utf-8")).hexdigest()[:12],
                name=name,
                pack_size=pack_size or (pack_match.group(0) if pack_match else ""),
                price=price,
                mrp=mrp,
                discount_percent=float(discount_match.group(1)) if discount_match else 0,
                delivery_minutes=int(eta_match.group(1)) if eta_match else None,
                image_url=raw.get("image") or None,
                in_stock=raw.get("inStock", not re.search(
                    r"out of stock|notify|sold out",
                    add_text,
                    re.I,
                )),
                handle=handle,
                product_url=urljoin(base_url, href) if href else None,
                search_query=query,
            )
        )
    return products[:limit]


class ZeptoClient:
    """Small, fail-closed Playwright client for Zepto's consumer website."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    async def _get_page(self) -> Page:
        if self._page and not self._page.is_closed():
            return self._page
        self.settings.zepto_profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            playwright = await async_playwright().start()
            self._playwright = playwright
            self._context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(Path(self.settings.zepto_profile_dir)),
                headless=self.settings.browser_headless,
                viewport={"width": 1380, "height": 900},
                locale="en-IN",
            )
        except Exception as exc:
            if self._playwright:
                await self._playwright.stop()
            self._playwright = None
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else "Unknown error"
            raise ZeptoError(f"Zepto's browser could not start: {message}") from exc
        context = self._context
        if context is None:
            raise ZeptoError("Zepto's browser context did not start.")
        context.set_default_timeout(self.settings.navigation_timeout_ms)
        self._page = (
            context.pages[0]
            if context.pages
            else await context.new_page()
        )
        return self._page

    async def _login_control_visible(self, page: Page) -> bool:
        login = page.get_by_text(
            re.compile(r"^(log\s*in|sign\s*in)$", re.I),
            exact=True,
        )
        for index in range(await login.count()):
            if await login.nth(index).is_visible():
                return True
        return False

    async def ensure_login(self, *, wait_for_user: bool = False) -> bool:
        page = await self._get_page()
        await page.goto(self.settings.zepto_base_url, wait_until="domcontentloaded")

        # Zepto renders its authentication controls after DOMContentLoaded. Checking
        # immediately creates a false "connected" result that flips back to
        # disconnected as soon as Search runs.
        await page.wait_for_timeout(1_200)

        if not await self._login_control_visible(page):
            # Require two consecutive logged-in observations. This avoids accepting
            # the empty React shell before the Login control has rendered.
            await page.wait_for_timeout(500)
            if not await self._login_control_visible(page):
                return True

        # A persistent profile can briefly render Login while its saved session is
        # still being restored. Give a normal status refresh a small grace period;
        # an explicit Connect action keeps waiting for the user.
        wait_seconds = 180 if wait_for_user else 3
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await page.wait_for_timeout(500)
            if not await self._login_control_visible(page):
                # Give the authenticated home screen one render pass before the
                # caller starts a search in the same persistent session.
                await page.wait_for_timeout(350)
                return True
        return False

    async def search(self, query: str) -> list[Product]:
        query = query.strip()
        if not query:
            return []
        if not await self.ensure_login(wait_for_user=False):
            raise ZeptoError("Connect Zepto before searching.")
        async with self._lock:
            page = await self._get_page()
            await page.goto(
                f"{self.settings.zepto_base_url}/search?query={quote_plus(query)}",
                wait_until="domcontentloaded",
            )
            try:
                await page.wait_for_selector(
                    'a[href*="/pn/"], [data-testid*="product"]',
                    timeout=12_000,
                )
            except Exception:
                pass
            raw = await page.evaluate(
                r"""limit => {
                  const roots = [...document.querySelectorAll(
                    'a[href*="/pn/"], [data-testid*="product"]'
                  )];
                  const seen = new Set();
                  const out = [];
                  for (const node of roots) {
                    // A Zepto product link is itself the complete card. Using its
                    // nearest div selects the whole results grid and duplicates
                    // the first product's text across every result.
                    const root = node.matches('a[href*="/pn/"]')
                      ? node
                      : node.closest('a[href*="/pn/"], article, li, [role="listitem"]') || node;
                    const hrefNode = node.matches('a[href]') ? node : root.querySelector('a[href]');
                    const href = hrefNode?.href || '';
                    const text = (root.innerText || '').trim();
                    if (!text || !/₹\s*[\d,]+/.test(text) || seen.has(href || text)) continue;
                    seen.add(href || text);
                    const img = root.querySelector('img');
                    const add = [...root.querySelectorAll('button, [role="button"]')]
                      .find(button => /^(add|out of stock|notify|sold out)$/i.test(
                        (button.innerText || button.textContent || '').trim()
                      ));
                    out.push({
                      text,
                      href,
                      image: img?.currentSrc || img?.src || null,
                      addText: add?.innerText || '',
                      name: (root.querySelector('[data-slot-id="ProductName"]')?.innerText
                        || img?.alt || '').trim(),
                      pack: (root.querySelector('[data-slot-id="PackSize"]')?.innerText
                        || '').trim(),
                      inStock: root.querySelector('[data-is-out-of-stock="true"]') == null
                    });
                    if (out.length >= limit) break;
                  }
                  return out;
                }""",
                self.settings.search_result_limit,
            )
            return zepto_products_from_raw(
                query,
                raw,
                self.settings.search_result_limit,
                base_url=self.settings.zepto_base_url,
            )

    async def add_to_cart(self, product: Product, qty: int) -> AddResult:
        if qty < 1:
            raise ZeptoError("Cart quantity must be at least one.")
        if not self.settings.cart_mutations_allowed_for("zepto"):
            return AddResult(
                product_id=product.id,
                product_name=product.name,
                requested_units=qty,
                success=True,
                dry_run=True,
                message=(
                    f"Zepto safe test: would add {qty} × {product.name}; "
                    "no cart button was clicked."
                ),
            )
        if not await self.ensure_login(wait_for_user=False):
            raise ZeptoError("Connect Zepto before modifying its cart.")
        async with self._lock:
            page = await self._get_page()
            await page.goto(
                product.product_url
                or f"{self.settings.zepto_base_url}/search?query={quote_plus(product.search_query or product.name)}",
                wait_until="domcontentloaded",
            )
            add = page.get_by_text(re.compile(r"^add$", re.I))
            if not await add.count():
                raise ZeptoError(f"Zepto's Add control was unavailable for {product.name}.")
            await add.first.click()
            plus = page.locator(
                'button[aria-label*="increase" i], [role="button"][aria-label*="increase" i]'
            )
            for _ in range(qty - 1):
                if not await plus.count():
                    raise ZeptoError(
                        f"Zepto's quantity control was unavailable for {product.name}."
                    )
                await plus.first.click()
            return AddResult(
                product_id=product.id,
                product_name=product.name,
                requested_units=qty,
                success=True,
                message=f"Added {qty} × {product.name} to the Zepto cart.",
            )

    async def cart_summary(self) -> CartSummary:
        if not await self.ensure_login(wait_for_user=False):
            raise ZeptoError("Connect Zepto before reading its cart.")
        async with self._lock:
            page = await self._get_page()
            await page.goto(
                f"{self.settings.zepto_base_url}/cart",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(500)
            raw = await page.evaluate(
                r"""() => {
                  const text = node => (node?.innerText || '').trim();
                  const roots = [...document.querySelectorAll(
                    'article, li, [role="listitem"], [data-testid*="cart"]'
                  )].filter(node => /₹\s*[\d,]+/.test(text(node)) && node.querySelector('img'));
                  const lines = roots.slice(0, 100).map(node => ({
                    text: text(node),
                    handle: node.querySelector('a[href*="/pn/"]')?.href
                      || node.querySelector('img')?.src || text(node).slice(0, 120)
                  }));
                  const heading = [...document.querySelectorAll('h1,h2,h3,h4,div,span')]
                    .find(node => /^(bill details|payment details)$/i.test(text(node)));
                  const billRoot = heading?.closest('section, article, [role="dialog"], div');
                  const etaNode = [...document.querySelectorAll('body *')]
                    .find(node => /\d+\s*(?:min|minute)/i.test(text(node))
                      && node.children.length < 4);
                  return {lines, billText: text(billRoot), etaText: text(etaNode)};
                }"""
            )
            return cart_summary_from_raw(raw, provider="zepto")

    async def remove_from_cart(self, product: Product, qty: int) -> None:
        if not self.settings.cart_mutations_allowed_for("zepto"):
            raise ZeptoError("Zepto cart writes are disabled.")
        async with self._lock:
            page = await self._get_page()
            await page.goto(
                f"{self.settings.zepto_base_url}/cart",
                wait_until="domcontentloaded",
            )
            cards = page.locator(
                'article, li, [role="listitem"], [data-testid*="cart"]',
                has_text=product.name,
            )
            if not await cards.count():
                raise ZeptoError(
                    f"{product.name} was not found in the Zepto cart during cleanup."
                )
            card = cards.first
            minus = card.locator(
                'button[aria-label*="decrease" i], [role="button"][aria-label*="decrease" i]'
            )
            for _ in range(qty):
                if not await minus.count() or not await minus.first.is_visible():
                    raise ZeptoError(
                        f"Zepto's quantity control was unavailable for {product.name}."
                    )
                await minus.first.click()
                await page.wait_for_timeout(120)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._context = None
        self._playwright = None
        self._page = None
