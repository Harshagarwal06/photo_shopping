"""Fan the single-platform pipeline out across every connected provider."""

from __future__ import annotations

import asyncio
from typing import Callable

from .compare import build_outcome, estimated_summary, rank
from .config import Settings
from .constraints import enforce_constraints
from .matcher import match_across_platforms
from .models import (
    CartPlan,
    CartSummary,
    ComparisonReport,
    DraftCart,
    DraftItem,
    PlatformOutcome,
    StreamEvent,
)
from .providers.base import GroceryProvider
from .search_cache import cached_search

EventSink = Callable[[StreamEvent], None] | None


def _emit(on_event: EventSink, stage: str, message: str, provider: str = "") -> None:
    if on_event is not None:
        on_event(StreamEvent(event="stage", stage=stage, message=message,
                             data={"provider": provider}))


async def _search_platform(
    provider: GroceryProvider,
    plan: CartPlan,
    on_event: EventSink,
    settings: Settings,
) -> dict[str, list]:
    """Search every planned item on one platform. Items stay sequential."""
    results = {}
    for index, item in enumerate(plan.items, start=1):
        if item.needs_review and not item.confirmed:
            results[item.id] = []
            _emit(
                on_event,
                "retrieval",
                f"Skipped uncertain transcription {item.search_term}; review is required.",
                provider.provider_id,
            )
            continue
        _emit(on_event, "retrieval",
              f"Searching {provider.display_name} for {item.provider_query} "
              f"({index}/{len(plan.items)})…", provider.provider_id)
        results[item.id] = await cached_search(provider, item.provider_query, settings)
    return results


async def run_comparison(
    plan: CartPlan,
    providers: dict[str, GroceryProvider],
    settings: Settings,
    on_event: EventSink = None,
    *,
    force_estimated: bool = False,
    operation_id: str | None = None,
    draft_sink: dict[str, DraftCart] | None = None,
) -> ComparisonReport:
    """Build the same basket everywhere and rank the resulting real totals."""
    # 1. Search every platform concurrently.
    searches = await asyncio.gather(
        *(
            _search_platform(provider, plan, on_event, settings)
            for provider in providers.values()
        ),
        return_exceptions=True,
    )

    candidates: dict[str, dict[str, list]] = {}
    failures: dict[str, str] = {}
    for provider_id, result in zip(providers, searches):
        if isinstance(result, BaseException):
            failures[provider_id] = str(result) or result.__class__.__name__
        else:
            candidates[provider_id] = result

    # 2. Joint match: one decision per item across all healthy platforms.
    _emit(on_event, "matcher", "Comparing products across platforms…")
    drafts: dict[str, DraftCart] = {}
    items_by_provider: dict[str, list[DraftItem]] = {pid: [] for pid in candidates}
    for planned in plan.items:
        match = await asyncio.to_thread(
            match_across_platforms,
            planned,
            {pid: candidates[pid].get(planned.id, []) for pid in candidates},
            settings,
        )
        for provider_id in candidates:
            decision = match.picks.get(provider_id)
            items_by_provider[provider_id].append(
                DraftItem(
                    planned=planned,
                    candidates=candidates[provider_id].get(planned.id, []),
                    selected_product_id=decision.product_id if decision else None,
                    units_to_add=decision.units_to_add if decision else 0,
                    reason=decision.reason if decision else match.equivalence_note,
                )
            )

    for provider_id, items in items_by_provider.items():
        drafts[provider_id] = enforce_constraints(
            items, plan.constraints,
            dry_run=not settings.cart_mutations_allowed_for(provider_id),
            provider_id=provider_id,
            provider_name=providers[provider_id].display_name,
        )
    if draft_sink is not None:
        draft_sink.update(drafts)

    # 3. Build each cart and read its real total, concurrently.
    async def settle(provider_id: str) -> tuple[str, CartSummary | None, str]:
        provider = providers[provider_id]
        draft = drafts[provider_id]
        try:
            if force_estimated or not settings.cart_mutations_allowed_for(provider_id):
                return provider_id, estimated_summary(provider_id, draft), ""
            selections = [
                (product, item.units_to_add)
                for item in draft.items
                if not item.removed
                and (product := item.selected_product) is not None
                and item.units_to_add > 0
            ]
            _emit(on_event, "cart", f"Building the {provider.display_name} cart…",
                  provider_id)
            add_results = await provider.add_items(
                selections,
                operation_id=operation_id or draft.id,
            )
            failed = [result.message for result in add_results if not result.success]
            if failed:
                raise RuntimeError("; ".join(failed))
            return provider_id, await provider.cart_summary(), ""
        except Exception as exc:
            return provider_id, None, str(exc) or exc.__class__.__name__

    settled = await asyncio.gather(*(settle(pid) for pid in drafts))

    # 4. Fold into outcomes and rank.
    outcomes: list[PlatformOutcome] = []
    for provider_id, summary, error in settled:
        outcomes.append(
            build_outcome(
                provider_id, providers[provider_id].display_name,
                drafts[provider_id], summary, settings,
                status="ok" if summary is not None else "failed",
                error=error,
            )
        )
    for provider_id, error in failures.items():
        outcomes.append(
            PlatformOutcome(
                provider=provider_id,
                display_name=providers[provider_id].display_name,
                status="failed", error=error,
            )
        )

    _emit(on_event, "compare", "Ranking platforms…")
    return rank(outcomes, settings)
