"""Application service for safe estimated and verified cart comparisons."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from .compare import build_outcome, estimated_summary, rank
from .config import Settings
from .constraints import enforce_constraints
from .models import (
    CartPlan,
    CleanupOutcome,
    ComparisonChoiceRequest,
    ComparisonOperation,
    ComparisonPreflight,
    ComparisonProposal,
    PlatformOutcome,
    PlatformPreflight,
    ProposalOverrideRequest,
)
from .orchestrator import run_comparison
from .providers.base import GroceryProvider
from .state_store import SQLiteStateStore
from .telemetry import TELEMETRY


@dataclass
class _Confirmation:
    proposal_id: str
    provider_ids: tuple[str, ...]
    expires_at: float


class ComparisonService:
    def __init__(
        self,
        providers: dict[str, GroceryProvider],
        settings: Settings,
        state_store: SQLiteStateStore | None = None,
    ):
        self.providers = providers
        self.settings = settings
        self.state_store = state_store
        self.proposals: OrderedDict[str, ComparisonProposal] = OrderedDict()
        self.operations: OrderedDict[str, ComparisonOperation] = OrderedDict()
        self._confirmations: OrderedDict[str, _Confirmation] = OrderedDict()
        self._lock = asyncio.Lock()

    def _remember(
        self,
        store: OrderedDict,
        key: str,
        value,
        *,
        persistent_kind: str | None = None,
    ) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > self.settings.max_state_records:
            store.popitem(last=False)
        if persistent_kind and self.state_store is not None:
            self.state_store.save(
                persistent_kind,
                key,
                value.model_dump(mode="json"),
            )

    def get_proposal(self, proposal_id: str) -> ComparisonProposal | None:
        proposal = self.proposals.get(proposal_id)
        if proposal is not None or self.state_store is None:
            return proposal
        payload = self.state_store.load("comparison_proposal", proposal_id)
        if payload is None:
            return None
        try:
            proposal = ComparisonProposal.model_validate(payload)
        except ValueError:
            self.state_store.delete("comparison_proposal", proposal_id)
            return None
        self._remember(self.proposals, proposal_id, proposal)
        return proposal

    def get_operation(self, operation_id: str) -> ComparisonOperation | None:
        operation = self.operations.get(operation_id)
        if operation is not None or self.state_store is None:
            return operation
        payload = self.state_store.load("comparison_operation", operation_id)
        if payload is None:
            return None
        try:
            operation = ComparisonOperation.model_validate(payload)
        except ValueError:
            self.state_store.delete("comparison_operation", operation_id)
            return None
        self._remember(self.operations, operation_id, operation)
        return operation

    def _purge_expired_confirmations(self) -> None:
        now = time.monotonic()
        expired = [
            token
            for token, confirmation in self._confirmations.items()
            if confirmation.expires_at < now
        ]
        for token in expired:
            self._confirmations.pop(token, None)

    def _selected(self, provider_ids: list[str]) -> dict[str, GroceryProvider]:
        unique_ids = list(dict.fromkeys(provider_ids))
        return {
            provider_id: self.providers[provider_id]
            for provider_id in unique_ids
            if provider_id in self.providers
        }

    async def _platform_preflight(
        self,
        provider: GroceryProvider,
        *,
        mode: str,
    ) -> PlatformPreflight:
        try:
            status, capabilities = await asyncio.gather(
                # Browser-backed providers keep their login in a persistent profile,
                # while the in-memory connection flag is lost on every server restart.
                # Refresh here so a valid saved session is not reported as disconnected.
                provider.status(refresh=True),
                provider.capabilities(),
            )
        except Exception as exc:
            return PlatformPreflight(
                provider=provider.provider_id,
                display_name=provider.display_name,
                message=str(exc) or exc.__class__.__name__,
            )

        writes_allowed = self.settings.cart_mutations_allowed_for(provider.provider_id)
        cart_empty: bool | None = None
        message = status.message
        eligible = status.connected

        if mode == "verified" and status.connected:
            if not capabilities.cart_read:
                eligible = False
                message = f"{provider.display_name} cart reading is not ready."
            elif not capabilities.cart_add or not capabilities.operation_cleanup:
                eligible = False
                message = f"{provider.display_name} verified cart comparison is not ready."
            elif not writes_allowed:
                eligible = False
                message = f"{provider.display_name} cart writes are disabled."
            else:
                try:
                    summary = await provider.cart_summary()
                    cart_empty = not any(line.quantity > 0 for line in summary.lines)
                    if not cart_empty:
                        eligible = False
                        message = (
                            f"Empty the {provider.display_name} cart before verified comparison."
                        )
                except Exception as exc:
                    eligible = False
                    message = str(exc) or exc.__class__.__name__

        return PlatformPreflight(
            provider=provider.provider_id,
            display_name=provider.display_name,
            connected=status.connected,
            eligible=eligible,
            cart_empty=cart_empty,
            cart_mutations_allowed=writes_allowed,
            capabilities=capabilities,
            message=message,
        )

    async def preflight(
        self,
        provider_ids: list[str],
        *,
        mode: Literal["estimated", "verified"],
        proposal_id: str | None = None,
    ) -> ComparisonPreflight:
        selected = self._selected(provider_ids)
        platforms = await asyncio.gather(
            *(
                self._platform_preflight(provider, mode=mode)
                for provider in selected.values()
            )
        )
        missing = [
            provider_id
            for provider_id in dict.fromkeys(provider_ids)
            if provider_id not in selected
        ]
        platforms.extend(
            PlatformPreflight(
                provider=provider_id,
                display_name=provider_id.title(),
                message="This provider is not implemented in the current build.",
            )
            for provider_id in missing
        )

        eligible = [platform.provider for platform in platforms if platform.eligible]
        can_continue = bool(eligible)
        if mode == "verified":
            can_continue = bool(platforms) and all(platform.eligible for platform in platforms)

        token: str | None = None
        if mode == "verified" and can_continue and proposal_id:
            self._purge_expired_confirmations()
            proposal = self.get_proposal(proposal_id)
            expected = tuple(proposal.provider_ids) if proposal else ()
            requested = tuple(eligible)
            if proposal is not None and requested == expected:
                token = secrets.token_urlsafe(32)
                self._remember(
                    self._confirmations,
                    token,
                    _Confirmation(
                        proposal_id=proposal_id,
                        provider_ids=requested,
                        expires_at=(
                            time.monotonic()
                            + self.settings.comparison_confirmation_ttl_seconds
                        ),
                    ),
                )
                proposal.frozen = True
                if self.state_store is not None:
                    self.state_store.save(
                        "comparison_proposal",
                        proposal.id,
                        proposal.model_dump(mode="json"),
                    )
            else:
                can_continue = False

        return ComparisonPreflight(
            mode=mode,
            platforms=list(platforms),
            can_continue=can_continue,
            requires_confirmation=mode == "verified",
            confirmation_token=token,
        )

    async def estimate(
        self,
        plan: CartPlan,
        provider_ids: list[str],
    ) -> ComparisonProposal:
        started = time.perf_counter()
        selected = self._selected(provider_ids)
        preflight = await self.preflight(provider_ids, mode="estimated")
        eligible_ids = [
            platform.provider for platform in preflight.platforms if platform.eligible
        ]
        eligible = {
            provider_id: selected[provider_id]
            for provider_id in eligible_ids
            if provider_id in selected
        }

        drafts = {}
        if eligible:
            report = await run_comparison(
                plan,
                eligible,
                self.settings,
                force_estimated=True,
                draft_sink=drafts,
            )
        else:
            report = rank([], self.settings)

        present = {outcome.provider for outcome in report.platforms}
        for platform in preflight.platforms:
            if platform.provider in present:
                continue
            report.platforms.append(
                PlatformOutcome(
                    provider=platform.provider,
                    display_name=platform.display_name,
                    status=(
                        "not_connected"
                        if platform.provider in selected
                        else "unavailable"
                    ),
                    error=platform.message,
                )
            )

        report = rank(report.platforms, self.settings)
        report.estimated = True
        proposal = ComparisonProposal(
            mode="estimated",
            plan=plan,
            report=report,
            provider_ids=eligible_ids,
            drafts=drafts,
        )
        self._remember(
            self.proposals,
            proposal.id,
            proposal,
            persistent_kind="comparison_proposal",
        )
        TELEMETRY.record(
            "comparison",
            stage="estimated",
            outcome="ok",
            duration_ms=(time.perf_counter() - started) * 1000,
            item_count=len(plan.items),
        )
        return proposal

    def override(
        self,
        proposal_id: str,
        request: ProposalOverrideRequest,
    ) -> ComparisonProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError("Comparison proposal not found.")
        draft = proposal.drafts.get(request.provider_id)
        if draft is None:
            raise ValueError("That provider is not part of this proposal.")
        item = next(
            (
                draft_item
                for draft_item in draft.items
                if draft_item.planned.id == request.planned_item_id
            ),
            None,
        )
        if item is None:
            raise ValueError("That grocery item is not part of this proposal.")
        product = next(
            (
                candidate
                for candidate in item.candidates
                if candidate.id == request.product_id and candidate.in_stock
            ),
            None,
        )
        if product is None:
            raise ValueError("That product is not an available proposal candidate.")

        item.selected_product_id = product.id
        item.units_to_add = request.units_to_add
        item.reason = "Selected by you for the cross-platform proposal."
        proposal.drafts[request.provider_id] = enforce_constraints(
            draft.items,
            proposal.plan.constraints,
            dry_run=True,
            draft_id=draft.id,
            provider_id=draft.provider_id,
            provider_name=draft.provider_name,
        )

        outcomes = []
        for provider_id, provider_draft in proposal.drafts.items():
            provider = self.providers[provider_id]
            summary = estimated_summary(provider_id, provider_draft)
            outcomes.append(
                build_outcome(
                    provider_id,
                    provider.display_name,
                    provider_draft,
                    summary,
                    self.settings,
                )
            )
        outcomes.extend(
            outcome
            for outcome in proposal.report.platforms
            if outcome.provider not in proposal.drafts
        )
        proposal.report = rank(outcomes, self.settings)
        proposal.report.estimated = True
        proposal.frozen = False
        self._confirmations = OrderedDict(
            (
                token,
                confirmation,
            )
            for token, confirmation in self._confirmations.items()
            if confirmation.proposal_id != proposal_id
        )
        if self.state_store is not None:
            self.state_store.save(
                "comparison_proposal",
                proposal.id,
                proposal.model_dump(mode="json"),
            )
        return proposal

    async def verify(
        self,
        proposal_id: str,
        confirmation_token: str,
    ) -> ComparisonOperation:
        started = time.perf_counter()
        async with self._lock:
            confirmation = self._confirmations.pop(confirmation_token, None)
            if (
                confirmation is None
                or confirmation.proposal_id != proposal_id
                or confirmation.expires_at < time.monotonic()
            ):
                raise ValueError("The verified-comparison confirmation is invalid or expired.")
            proposal = self.get_proposal(proposal_id)
            if proposal is None:
                raise ValueError("Comparison proposal not found.")
            if not proposal.frozen:
                raise ValueError("Review and preflight the proposal again before verification.")

            final_preflight = await self.preflight(
                list(confirmation.provider_ids),
                mode="verified",
            )
            if not final_preflight.can_continue:
                raise ValueError(
                    "Verified comparison preflight changed. No cart was modified."
                )

            selected = self._selected(list(confirmation.provider_ids))
            operation_id = uuid4().hex

            async def settle(provider_id: str) -> PlatformOutcome:
                provider = selected[provider_id]
                draft = proposal.drafts[provider_id]
                selections = [
                    (product, item.units_to_add)
                    for item in draft.items
                    if not item.removed
                    and (product := item.selected_product) is not None
                    and item.units_to_add > 0
                ]
                try:
                    results = await provider.add_items(
                        selections,
                        operation_id=operation_id,
                    )
                    failed = [result.message for result in results if not result.success]
                    if failed:
                        raise ValueError("; ".join(failed))
                    summary = await provider.cart_summary()
                    return build_outcome(
                        provider_id,
                        provider.display_name,
                        draft,
                        summary,
                        self.settings,
                    )
                except Exception as exc:
                    return PlatformOutcome(
                        provider=provider_id,
                        display_name=provider.display_name,
                        status="failed",
                        error=str(exc) or exc.__class__.__name__,
                    )

            outcomes = await asyncio.gather(
                *(settle(provider_id) for provider_id in confirmation.provider_ids)
            )
            report = rank(list(outcomes), self.settings)
            operation = ComparisonOperation(
                id=operation_id,
                proposal_id=proposal.id,
                provider_ids=list(confirmation.provider_ids),
                report=report,
            )
            self._remember(
                self.operations,
                operation.id,
                operation,
                persistent_kind="comparison_operation",
            )
            TELEMETRY.record(
                "comparison",
                stage="verified",
                outcome="ok",
                duration_ms=(time.perf_counter() - started) * 1000,
                item_count=len(proposal.plan.items),
            )
            return operation

    async def choose(
        self,
        operation_id: str,
        request: ComparisonChoiceRequest,
    ) -> ComparisonOperation:
        operation = self.get_operation(operation_id)
        if operation is None:
            raise ValueError("Comparison operation not found.")
        if operation.status in {"cleaned", "cleanup_failed"} and operation.cleanup:
            return operation

        winner = request.winner or operation.report.winner
        if request.action == "keep_all":
            operation.winner = winner
            operation.status = "completed"
            if self.state_store is not None:
                self.state_store.save(
                    "comparison_operation",
                    operation.id,
                    operation.model_dump(mode="json"),
                )
            return operation

        targets = list(operation.provider_ids)
        if request.action == "keep_winner" and winner:
            targets = [provider_id for provider_id in targets if provider_id != winner]

        cleanup: list[CleanupOutcome] = []
        for provider_id in targets:
            provider = self.providers.get(provider_id)
            if provider is None:
                cleanup.append(
                    CleanupOutcome(
                        provider=provider_id,
                        success=False,
                        message="Provider is unavailable for cleanup.",
                    )
                )
                continue
            try:
                await provider.clear_cart(operation_id=operation.id)
                cleanup.append(
                    CleanupOutcome(
                        provider=provider_id,
                        success=True,
                        message=f"{provider.display_name} comparison cart restored.",
                    )
                )
            except Exception as exc:
                cleanup.append(
                    CleanupOutcome(
                        provider=provider_id,
                        success=False,
                        message=str(exc) or exc.__class__.__name__,
                    )
                )

        operation.winner = winner
        operation.cleanup = cleanup
        operation.status = (
            "cleaned" if all(outcome.success for outcome in cleanup) else "cleanup_failed"
        )
        if self.state_store is not None:
            self.state_store.save(
                "comparison_operation",
                operation.id,
                operation.model_dump(mode="json"),
            )
        return operation
