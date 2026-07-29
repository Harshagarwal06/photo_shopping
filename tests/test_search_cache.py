"""The search cache exists to stop recognition and the run that follows it from
fetching the same query twice within seconds."""

import asyncio

import pytest

from app.models import Product
from app.providers.base import ProviderAuthError, ProviderError
from app.search_cache import ProviderSearchCache


class CountingProvider:
    def __init__(self, provider_id: str = "blinkit"):
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self.calls: list[str] = []

    async def search(self, query: str) -> list[Product]:
        self.calls.append(query)
        return [
            Product(
                id=f"{self.provider_id}-{len(self.calls)}",
                name=f"{query} result",
                pack_size="1 item",
                price=50,
                handle="h",
            )
        ]


def test_a_repeated_query_reaches_the_provider_once():
    cache = ProviderSearchCache()
    provider = CountingProvider()

    async def run():
        first = await cache.search(provider, "atta", ttl_seconds=90)
        second = await cache.search(provider, "atta", ttl_seconds=90)
        return first, second

    first, second = asyncio.run(run())

    assert provider.calls == ["atta"]
    assert [product.id for product in first] == [product.id for product in second]


def test_queries_match_regardless_of_case_and_padding():
    cache = ProviderSearchCache()
    provider = CountingProvider()

    async def run():
        await cache.search(provider, "Amul Butter", ttl_seconds=90)
        await cache.search(provider, "  amul butter ", ttl_seconds=90)

    asyncio.run(run())

    assert provider.calls == ["Amul Butter"]


def test_providers_never_share_an_entry():
    cache = ProviderSearchCache()
    blinkit = CountingProvider("blinkit")
    zepto = CountingProvider("zepto")

    async def run():
        return (
            await cache.search(blinkit, "milk", ttl_seconds=90),
            await cache.search(zepto, "milk", ttl_seconds=90),
        )

    from_blinkit, from_zepto = asyncio.run(run())

    assert blinkit.calls == ["milk"] and zepto.calls == ["milk"]
    assert from_blinkit[0].id != from_zepto[0].id


def test_a_zero_ttl_disables_caching_entirely():
    cache = ProviderSearchCache()
    provider = CountingProvider()

    async def run():
        await cache.search(provider, "milk", ttl_seconds=0)
        await cache.search(provider, "milk", ttl_seconds=0)

    asyncio.run(run())

    assert provider.calls == ["milk", "milk"]


def test_an_expired_entry_is_fetched_again(monkeypatch):
    cache = ProviderSearchCache()
    provider = CountingProvider()
    clock = [1000.0]
    monkeypatch.setattr("app.search_cache.time.monotonic", lambda: clock[0])

    async def run():
        await cache.search(provider, "milk", ttl_seconds=90)
        clock[0] += 89
        await cache.search(provider, "milk", ttl_seconds=90)
        clock[0] += 2
        await cache.search(provider, "milk", ttl_seconds=90)

    asyncio.run(run())

    assert provider.calls == ["milk", "milk"]


def test_a_failed_search_is_never_cached():
    cache = ProviderSearchCache()

    class FailingOnce:
        provider_id = "blinkit"
        display_name = "Blinkit"

        def __init__(self):
            self.calls = 0

        async def search(self, query: str) -> list[Product]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider throttled")
            return []

    provider = FailingOnce()

    async def run():
        with pytest.raises(RuntimeError):
            await cache.search(provider, "milk", ttl_seconds=90)
        await cache.search(provider, "milk", ttl_seconds=90)

    asyncio.run(run())

    assert provider.calls == 2


def test_a_caller_cannot_mutate_another_callers_results():
    cache = ProviderSearchCache()
    provider = CountingProvider()

    async def run():
        first = await cache.search(provider, "milk", ttl_seconds=90)
        first.clear()
        return await cache.search(provider, "milk", ttl_seconds=90)

    assert len(asyncio.run(run())) == 1


def test_the_cache_stays_bounded():
    cache = ProviderSearchCache(max_entries=3)
    provider = CountingProvider()

    async def run():
        for index in range(5):
            await cache.search(provider, f"query {index}", ttl_seconds=90)
        # The oldest entry was evicted, so asking for it again refetches.
        await cache.search(provider, "query 0", ttl_seconds=90)

    asyncio.run(run())

    assert provider.calls.count("query 0") == 2


def test_identical_concurrent_queries_are_coalesced():
    cache = ProviderSearchCache()

    class SlowProvider(CountingProvider):
        async def search(self, query: str) -> list[Product]:
            await asyncio.sleep(0.01)
            return await super().search(query)

    provider = SlowProvider()

    async def run():
        return await asyncio.gather(
            cache.search(provider, "milk", ttl_seconds=90),
            cache.search(provider, " milk ", ttl_seconds=90),
            cache.search(provider, "MILK", ttl_seconds=90),
        )

    results = asyncio.run(run())

    assert provider.calls == ["milk"]
    assert len(results) == 3
    assert results[0] == results[1] == results[2]


def test_transient_provider_errors_are_retried():
    cache = ProviderSearchCache()

    class FailingTwice(CountingProvider):
        async def search(self, query: str) -> list[Product]:
            self.calls.append(query)
            if len(self.calls) < 3:
                raise ProviderError("temporarily throttled")
            return [
                Product(
                    id="recovered",
                    name="Milk",
                    pack_size="1 L",
                    price=60,
                    handle="milk",
                )
            ]

    provider = FailingTwice()

    result = asyncio.run(
        cache.search(
            provider,
            "milk",
            ttl_seconds=90,
            retry_attempts=3,
            retry_base_delay_seconds=0,
        )
    )

    assert [product.id for product in result] == ["recovered"]
    assert provider.calls == ["milk", "milk", "milk"]


def test_authentication_errors_are_never_retried():
    cache = ProviderSearchCache()

    class LoggedOut(CountingProvider):
        async def search(self, query: str) -> list[Product]:
            self.calls.append(query)
            raise ProviderAuthError("reconnect")

    provider = LoggedOut()

    with pytest.raises(ProviderAuthError, match="reconnect"):
        asyncio.run(
            cache.search(
                provider,
                "milk",
                ttl_seconds=90,
                retry_attempts=3,
            )
        )

    assert provider.calls == ["milk"]


def test_circuit_breaker_pauses_a_repeatedly_failing_provider(monkeypatch):
    cache = ProviderSearchCache()
    clock = [1000.0]
    monkeypatch.setattr("app.search_cache.time.monotonic", lambda: clock[0])

    class DownProvider(CountingProvider):
        async def search(self, query: str) -> list[Product]:
            self.calls.append(query)
            raise ProviderError("provider down")

    provider = DownProvider()

    async def run():
        for query in ("milk", "eggs"):
            with pytest.raises(ProviderError, match="provider down"):
                await cache.search(
                    provider,
                    query,
                    ttl_seconds=0,
                    circuit_breaker_failures=2,
                    circuit_breaker_cooldown_seconds=30,
                )
        with pytest.raises(ProviderError, match="temporarily paused"):
            await cache.search(
                provider,
                "bread",
                ttl_seconds=0,
                circuit_breaker_failures=2,
                circuit_breaker_cooldown_seconds=30,
            )

    asyncio.run(run())

    assert provider.calls == ["milk", "eggs"]
    assert cache.diagnostics()["blinkit"]["circuit_open"] is True
