"""A short-lived cache in front of provider catalogue searches.

Autonomous recognition searches each candidate reading of an uncertain line
before the draft or comparison run searches the reading it settled on, so the
same (provider, query) pair is fetched twice within seconds of itself. The
browser-driven providers are exactly the ones that throttle, so the repeat costs
more than it looks like it should.

Only ``search`` is cached. Cart reads and writes always reach the provider.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from .models import Product
from .providers.base import GroceryProvider


class ProviderSearchCache:
    """Bounded, per-(provider, query) results with a wall-clock expiry."""

    def __init__(self, *, max_entries: int = 256):
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], tuple[float, list[Product]]] = (
            OrderedDict()
        )

    def clear(self) -> None:
        self._entries.clear()

    def _fresh(self, key: tuple[str, str], ttl_seconds: float) -> list[Product] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, products = entry
        if time.monotonic() - stored_at >= ttl_seconds:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        # Copied so a caller cannot mutate another caller's cached list.
        return list(products)

    def _store(self, key: tuple[str, str], products: list[Product]) -> None:
        self._entries[key] = (time.monotonic(), list(products))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def search(
        self,
        provider: GroceryProvider,
        query: str,
        *,
        ttl_seconds: float,
    ) -> list[Product]:
        if ttl_seconds <= 0:
            return await provider.search(query)
        key = (provider.provider_id, query.casefold().strip())
        cached = self._fresh(key, ttl_seconds)
        if cached is not None:
            return cached
        # A failed search is never cached: the next caller should get the error
        # or a fresh attempt rather than a memoised empty catalogue.
        products = await provider.search(query)
        self._store(key, products)
        return products


SEARCH_CACHE = ProviderSearchCache()


async def cached_search(
    provider: GroceryProvider,
    query: str,
    settings,
) -> list[Product]:
    return await SEARCH_CACHE.search(
        provider, query, ttl_seconds=settings.search_cache_ttl_seconds
    )
