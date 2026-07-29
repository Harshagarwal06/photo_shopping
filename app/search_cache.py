"""Reliable, bounded access to provider catalogue searches.

The scheduler combines short-lived caching, identical-request coalescing,
per-provider pacing and retries, and a circuit breaker. Cart reads and writes
never pass through this module.
"""

from __future__ import annotations

import asyncio
import random
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field

from .models import Product
from .providers.base import (
    GroceryProvider,
    ProviderAuthError,
    ProviderError,
    ProviderSafetyError,
)
from .telemetry import TELEMETRY


@dataclass
class _LoopRuntime:
    coordination_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    provider_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    inflight: dict[tuple[str, str], asyncio.Task[list[Product]]] = field(
        default_factory=dict
    )


class ProviderSearchCache:
    """Bounded provider searches with cache, pacing, retry, and health state."""

    def __init__(self, *, max_entries: int = 256):
        self._max_entries = max_entries
        self._entries: OrderedDict[
            tuple[str, str], tuple[float, list[Product]]
        ] = OrderedDict()
        self._failures: dict[str, int] = {}
        self._circuit_open_until: dict[str, float] = {}
        self._provider_last_started: dict[str, float] = {}
        self._runtimes: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopRuntime
        ] = weakref.WeakKeyDictionary()

    def _runtime(self) -> _LoopRuntime:
        loop = asyncio.get_running_loop()
        runtime = self._runtimes.get(loop)
        if runtime is None:
            runtime = _LoopRuntime()
            self._runtimes[loop] = runtime
        return runtime

    def clear(self) -> None:
        self._entries.clear()
        self._failures.clear()
        self._circuit_open_until.clear()
        self._provider_last_started.clear()
        for runtime in self._runtimes.values():
            for task in runtime.inflight.values():
                task.cancel()
        self._runtimes.clear()

    def diagnostics(self) -> dict[str, dict[str, float | int | bool]]:
        """Return provider health without queries, products, or user data."""
        now = time.monotonic()
        provider_ids = set(self._failures) | set(self._circuit_open_until)
        return {
            provider_id: {
                "consecutive_failures": self._failures.get(provider_id, 0),
                "circuit_open": self._circuit_open_until.get(provider_id, 0) > now,
                "retry_after_seconds": max(
                    0.0, self._circuit_open_until.get(provider_id, 0) - now
                ),
            }
            for provider_id in sorted(provider_ids)
        }

    def _fresh(
        self, key: tuple[str, str], ttl_seconds: float
    ) -> list[Product] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, products = entry
        if time.monotonic() - stored_at >= ttl_seconds:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return list(products)

    def _store(self, key: tuple[str, str], products: list[Product]) -> None:
        self._entries[key] = (time.monotonic(), list(products))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def _check_circuit(self, provider: GroceryProvider) -> None:
        open_until = self._circuit_open_until.get(provider.provider_id, 0)
        now = time.monotonic()
        if open_until <= now:
            if open_until:
                self._circuit_open_until.pop(provider.provider_id, None)
            return
        retry_after = max(1, round(open_until - now))
        TELEMETRY.record(
            "provider_search",
            stage="circuit_breaker",
            provider=provider.provider_id,
            outcome="rejected",
        )
        raise ProviderError(
            f"{provider.display_name} search is temporarily paused after repeated "
            f"failures. Try again in about {retry_after} seconds."
        )

    async def _wait_for_provider_slot(
        self,
        provider_id: str,
        *,
        min_interval_seconds: float,
    ) -> None:
        if min_interval_seconds <= 0:
            self._provider_last_started[provider_id] = time.monotonic()
            return
        elapsed = time.monotonic() - self._provider_last_started.get(
            provider_id, float("-inf")
        )
        remaining = min_interval_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._provider_last_started[provider_id] = time.monotonic()

    async def _search_and_store(
        self,
        runtime: _LoopRuntime,
        provider: GroceryProvider,
        query: str,
        key: tuple[str, str],
        *,
        cache_results: bool,
        min_interval_seconds: float,
        retry_attempts: int,
        retry_base_delay_seconds: float,
        retry_jitter_ratio: float,
        circuit_breaker_failures: int,
        circuit_breaker_cooldown_seconds: float,
    ) -> list[Product]:
        provider_lock = runtime.provider_locks.setdefault(
            provider.provider_id, asyncio.Lock()
        )
        async with provider_lock:
            self._check_circuit(provider)
            last_error: Exception | None = None
            attempts = max(1, retry_attempts)
            for attempt in range(attempts):
                await self._wait_for_provider_slot(
                    provider.provider_id,
                    min_interval_seconds=min_interval_seconds,
                )
                started = time.perf_counter()
                try:
                    products = await provider.search(query)
                except ProviderAuthError:
                    TELEMETRY.record(
                        "provider_search",
                        stage="provider",
                        provider=provider.provider_id,
                        outcome="auth_required",
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                    raise
                except ProviderSafetyError:
                    TELEMETRY.record(
                        "provider_search",
                        stage="provider",
                        provider=provider.provider_id,
                        outcome="safety_blocked",
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                    raise
                except Exception as exc:
                    TELEMETRY.record(
                        "provider_search",
                        stage="provider",
                        provider=provider.provider_id,
                        outcome="retry"
                        if attempt + 1 < attempts
                        else "failed",
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                    last_error = exc
                    if attempt + 1 >= attempts:
                        break
                    base = retry_base_delay_seconds * (2**attempt)
                    jitter = base * retry_jitter_ratio * random.random()
                    await asyncio.sleep(base + jitter)
                else:
                    TELEMETRY.record(
                        "provider_search",
                        stage="provider",
                        provider=provider.provider_id,
                        outcome="ok",
                        duration_ms=(time.perf_counter() - started) * 1000,
                        item_count=len(products),
                    )
                    self._failures.pop(provider.provider_id, None)
                    self._circuit_open_until.pop(provider.provider_id, None)
                    if cache_results:
                        self._store(key, products)
                    return list(products)

            failures = self._failures.get(provider.provider_id, 0) + 1
            self._failures[provider.provider_id] = failures
            if (
                circuit_breaker_failures > 0
                and failures >= circuit_breaker_failures
            ):
                self._circuit_open_until[provider.provider_id] = (
                    time.monotonic() + circuit_breaker_cooldown_seconds
                )
            assert last_error is not None
            raise last_error

    async def search(
        self,
        provider: GroceryProvider,
        query: str,
        *,
        ttl_seconds: float,
        min_interval_seconds: float = 0,
        retry_attempts: int = 1,
        retry_base_delay_seconds: float = 0,
        retry_jitter_ratio: float = 0,
        circuit_breaker_failures: int = 0,
        circuit_breaker_cooldown_seconds: float = 0,
    ) -> list[Product]:
        normalized = query.casefold().strip()
        key = (provider.provider_id, normalized)
        if ttl_seconds > 0:
            cached = self._fresh(key, ttl_seconds)
            if cached is not None:
                TELEMETRY.record(
                    "provider_search",
                    stage="cache",
                    provider=provider.provider_id,
                    outcome="hit",
                    item_count=len(cached),
                )
                return cached

        self._check_circuit(provider)
        runtime = self._runtime()
        async with runtime.coordination_lock:
            if ttl_seconds > 0:
                cached = self._fresh(key, ttl_seconds)
                if cached is not None:
                    TELEMETRY.record(
                        "provider_search",
                        stage="cache",
                        provider=provider.provider_id,
                        outcome="hit",
                        item_count=len(cached),
                    )
                    return cached
            task = runtime.inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._search_and_store(
                        runtime,
                        provider,
                        query.strip(),
                        key,
                        cache_results=ttl_seconds > 0,
                        min_interval_seconds=min_interval_seconds,
                        retry_attempts=retry_attempts,
                        retry_base_delay_seconds=retry_base_delay_seconds,
                        retry_jitter_ratio=retry_jitter_ratio,
                        circuit_breaker_failures=circuit_breaker_failures,
                        circuit_breaker_cooldown_seconds=(
                            circuit_breaker_cooldown_seconds
                        ),
                    )
                )
                runtime.inflight[key] = task
            else:
                TELEMETRY.record(
                    "provider_search",
                    stage="coalesced",
                    provider=provider.provider_id,
                    outcome="joined",
                )

        try:
            return list(await asyncio.shield(task))
        finally:
            if task.done():
                async with runtime.coordination_lock:
                    if runtime.inflight.get(key) is task:
                        runtime.inflight.pop(key, None)


SEARCH_CACHE = ProviderSearchCache()


async def cached_search(
    provider: GroceryProvider,
    query: str,
    settings,
) -> list[Product]:
    return await SEARCH_CACHE.search(
        provider,
        query,
        ttl_seconds=settings.search_cache_ttl_seconds,
        min_interval_seconds=settings.search_provider_min_interval_seconds,
        retry_attempts=settings.search_retry_attempts,
        retry_base_delay_seconds=settings.search_retry_base_delay_seconds,
        retry_jitter_ratio=settings.search_retry_jitter_ratio,
        circuit_breaker_failures=settings.search_circuit_breaker_failures,
        circuit_breaker_cooldown_seconds=(
            settings.search_circuit_breaker_cooldown_seconds
        ),
    )
