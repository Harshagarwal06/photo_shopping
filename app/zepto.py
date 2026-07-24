from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin

from .blinkit import PACK_RE, PRICE_RE
from .models import Product


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
        name = next(
            (line for line in lines if "₹" not in line and not PACK_RE.fullmatch(line)),
            query,
        )
        pack_match = PACK_RE.search(text)
        href = raw.get("href") or ""
        handle = href or raw.get("image") or f"{query}|{name}|{price}"
        discount_match = re.search(r"(\d+(?:\.\d+)?)%\s*OFF", text, re.I)
        eta_match = re.search(r"(\d+)\s*MINS?", text, re.I)
        add_text = raw.get("addText", "")
        products.append(
            Product(
                id=hashlib.sha1(f"zepto|{handle}".encode("utf-8")).hexdigest()[:12],
                name=name,
                pack_size=pack_match.group(0) if pack_match else "",
                price=price,
                mrp=mrp,
                discount_percent=float(discount_match.group(1)) if discount_match else 0,
                delivery_minutes=int(eta_match.group(1)) if eta_match else None,
                image_url=raw.get("image") or None,
                in_stock=not re.search(r"out of stock|notify|sold out", add_text, re.I),
                handle=handle,
                product_url=urljoin(base_url, href) if href else None,
                search_query=query,
            )
        )
    return products[:limit]
