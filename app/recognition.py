"""Autonomous, fail-closed adjudication for uncertain photographed list lines."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .config import Settings
from .local_vision import KNOWN_TERMS, _normalise_ocr_candidate, _parse_item
from .matcher import match_is_reasonable
from .models import CartPlan, PlannedItem, Product, PROVIDER_QUERY_BRANDS
from .providers.base import GroceryProvider
from .search_cache import cached_search


logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]+")
# Genuine OCR brand typos measure around 0.67-0.75 against the brand they meant,
# so this gate is deliberately loose. The lexicon check in _closest_brand_typo,
# not this number, is what keeps real grocery words from being "corrected".
BRAND_TYPO_SIMILARITY = 0.62
BRAND_LABELS = {
    "havmor": "Havmor",
    "kitkat": "KitKat",
    "mother dairy": "Mother Dairy",
}


@dataclass
class _Hypothesis:
    item: PlannedItem
    source: str
    catalog_hits: int = 0
    score: float = 0
    independent_agreement: bool = False


def _tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(value.casefold())


def _normalized(value: str) -> str:
    return " ".join(_tokens(_normalise_ocr_candidate(value)))


def _similarity(left: PlannedItem, right: PlannedItem) -> float:
    left_value = _normalized(left.provider_query)
    right_value = _normalized(right.provider_query)
    if not left_value or not right_value:
        return 0
    left_tokens = set(left_value.split())
    right_tokens = set(right_value.split())
    token_score = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    sequence_score = SequenceMatcher(None, left_value, right_value).ratio()
    return max(token_score, sequence_score)


def _lexicon_score(item: PlannedItem) -> float:
    tokens = _tokens(item.provider_query)
    if not tokens:
        return 0
    known = set(KNOWN_TERMS)
    return sum(token in known for token in tokens) / len(tokens)


def _hypothesis_from_raw(raw: str, base: PlannedItem) -> PlannedItem | None:
    parsed = _parse_item(
        raw,
        "photo",
        vision_confidence=max(0.25, min(base.confidence, 0.7)),
    )
    if parsed is None:
        return None
    # Quantity is safety-sensitive. OCR alternatives may help with the product
    # name, but they cannot silently change the locally parsed amount or unit.
    parsed.id = base.id
    parsed.quantity = base.quantity
    parsed.unit = base.unit
    parsed.crop_box = list(base.crop_box)
    return parsed


def _build_hypotheses(
    local: PlannedItem,
    cloud: PlannedItem | None,
    settings: Settings,
) -> list[_Hypothesis]:
    hypotheses = [_Hypothesis(local.model_copy(deep=True), "local")]
    if cloud is not None:
        cloud_copy = (
            _hypothesis_from_raw(cloud.raw_text, cloud)
            if cloud.raw_text
            else None
        ) or cloud.model_copy(deep=True)
        cloud_copy.confidence = max(cloud_copy.confidence, cloud.confidence)
        cloud_copy.source = cloud.source
        cloud_has_visible_measurement = (
            cloud_copy.unit in {"g", "kg", "ml", "l"}
            and bool(re.search(r"\d", local.raw_text))
        )
        if not cloud_has_visible_measurement:
            cloud_copy.quantity = local.quantity
            cloud_copy.unit = local.unit
        hypotheses.append(_Hypothesis(cloud_copy, "cloud"))
    for raw in [*local.alternatives, local.raw_text]:
        parsed = _hypothesis_from_raw(raw, local)
        if parsed is not None:
            hypotheses.append(_Hypothesis(parsed, "local_alternative"))

    deduplicated: list[_Hypothesis] = []
    seen: dict[str, _Hypothesis] = {}
    for hypothesis in hypotheses:
        key = _normalized(hypothesis.item.provider_query)
        if not key:
            continue
        if key in seen:
            existing = seen[key]
            if {existing.source, hypothesis.source} == {"local", "cloud"}:
                existing.independent_agreement = True
                existing.item.confidence = max(
                    existing.item.confidence, hypothesis.item.confidence
                )
            continue
        seen[key] = hypothesis
        deduplicated.append(hypothesis)
        if len(deduplicated) >= settings.autonomous_max_hypotheses:
            break
    return deduplicated


async def _search_hypotheses(
    providers: dict[str, GroceryProvider],
    hypotheses: list[_Hypothesis],
    settings: Settings,
) -> dict[str, dict[str, list[Product]]]:
    queries = list(dict.fromkeys(hypothesis.item.provider_query for hypothesis in hypotheses))

    async def search_provider(
        provider_id: str,
        provider: GroceryProvider,
    ) -> tuple[str, dict[str, list[Product]]]:
        results: dict[str, list[Product]] = {}
        for query in queries:
            try:
                results[query] = await cached_search(provider, query, settings)
            except Exception:
                # Autonomous recognition must remain available when one provider
                # is disconnected or temporarily unhealthy. Logged rather than
                # discarded, so a provider that is failing every query is not
                # indistinguishable from one that simply has no stock.
                logger.warning(
                    "Recognition search failed on %s for %r; treating as no "
                    "catalogue evidence.",
                    provider_id,
                    query,
                    exc_info=True,
                )
                results[query] = []
        return provider_id, results

    searched = await asyncio.gather(
        *(search_provider(provider_id, provider) for provider_id, provider in providers.items())
    )
    return dict(searched)


def _catalog_hits(
    hypothesis: _Hypothesis,
    search_results: dict[str, dict[str, list[Product]]],
) -> int:
    probe = hypothesis.item.model_copy(
        update={"needs_review": False, "confirmed": True}
    )
    return sum(
        any(
            product.in_stock and match_is_reasonable(probe, product)[0]
            for product in provider_results.get(hypothesis.item.provider_query, [])
        )
        for provider_results in search_results.values()
    )


def _brand_from_name(name: str) -> str | None:
    lowered = name.casefold()
    return next(
        (
            brand
            for brand in sorted(PROVIDER_QUERY_BRANDS, key=len, reverse=True)
            if re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", lowered)
        ),
        None,
    )


def _closest_brand_typo(hypotheses: list[_Hypothesis], brand: str) -> tuple[str, float] | None:
    brand_tokens = brand.split()
    target = brand if len(brand_tokens) > 1 else brand_tokens[0]
    best: tuple[str, float] | None = None
    for hypothesis in hypotheses:
        for token in _tokens(hypothesis.item.provider_query):
            if token in PROVIDER_QUERY_BRANDS:
                continue
            # A word the grocery lexicon already recognises is a product, not a
            # misreading. Brand resemblance alone cannot separate the two:
            # "atta"/"Tata" and "oregano"/"Oreo" score as high as the genuine
            # typos this correction exists for, so the lexicon has to decide.
            if token in KNOWN_TERMS:
                continue
            similarity = SequenceMatcher(None, token, target).ratio()
            if best is None or similarity > best[1]:
                best = (token, similarity)
    return best


def _independent_brand_prefix_support(
    hypotheses: list[_Hypothesis],
    brand: str,
) -> int:
    """Count independent engines that read a meaningful prefix of ``brand``."""
    supported_sources: set[str] = set()
    for hypothesis in hypotheses:
        if hypothesis.source not in {"local", "cloud"}:
            continue
        if any(
            len(token) >= 2 and brand.startswith(token)
            for token in _tokens(hypothesis.item.provider_query)
        ):
            supported_sources.add(hypothesis.source)
    return len(supported_sources)


def _local_cloud_prefix_support(hypotheses: list[_Hypothesis]) -> bool:
    """Whether both OCR engines saw compatible product-word prefixes."""
    local_tokens = {
        token
        for hypothesis in hypotheses
        if hypothesis.source == "local"
        for token in _tokens(hypothesis.item.provider_query)
        if len(token) >= 2
    }
    cloud_tokens = {
        token
        for hypothesis in hypotheses
        if hypothesis.source == "cloud"
        for token in _tokens(hypothesis.item.provider_query)
        if len(token) >= 2
    }
    return any(
        local.startswith(cloud) or cloud.startswith(local)
        for local in local_tokens
        for cloud in cloud_tokens
    )


def _catalog_brand_correction(
    hypotheses: list[_Hypothesis],
    search_results: dict[str, dict[str, list[Product]]],
    settings: Settings,
) -> _Hypothesis | None:
    provider_votes: dict[str, set[str]] = {}
    provider_products: dict[str, dict[str, set[str]]] = {}
    for provider_id, query_results in search_results.items():
        provider_brands: set[str] = set()
        for products in query_results.values():
            for product in products[:3]:
                brand = _brand_from_name(product.name)
                if brand:
                    provider_brands.add(brand)
                    provider_products.setdefault(brand, {}).setdefault(
                        provider_id, set()
                    ).add(product.id)
        for brand in provider_brands:
            provider_votes.setdefault(brand, set()).add(provider_id)

    quorum = min(
        settings.autonomous_catalog_min_providers,
        max(1, len(search_results)),
    )
    typos: dict[str, tuple[str, float]] = {}
    for brand, voters in provider_votes.items():
        if len(voters) < quorum:
            continue
        closest = _closest_brand_typo(hypotheses, brand)
        if closest is None or closest[1] < BRAND_TYPO_SIMILARITY:
            continue
        if quorum == 1:
            # One catalogue is enough only with two other independent signals:
            # both OCR engines saw the brand prefix, and at least two distinct
            # top products repeat that brand. This turns Ma/Mag into Maggi
            # without letting a single broad search result invent a brand.
            if _independent_brand_prefix_support(hypotheses, brand) < 2:
                continue
            provider_id = next(iter(voters))
            if len(provider_products.get(brand, {}).get(provider_id, set())) < 2:
                continue
        typos[brand] = closest
    if not typos:
        return None
    votes, brand = max(
        ((len(provider_votes[brand]), brand) for brand in typos),
        key=lambda entry: (entry[0], len(entry[1]), entry[1]),
    )
    base = max(hypotheses, key=lambda hypothesis: hypothesis.item.confidence).item
    typo = typos[brand][0]
    search_tokens = _tokens(base.search_term)
    remaining = [token for token in search_tokens if token != typo]
    label = BRAND_LABELS.get(brand, brand.title())
    if remaining:
        corrected_search = " ".join(remaining)
        corrected_context = " ".join(part for part in (base.context, label) if part).strip()
    else:
        corrected_search = label
        corrected_context = base.context
    corrected = base.model_copy(
        deep=True,
        update={
            "search_term": corrected_search,
            "context": corrected_context,
            "confidence": max(base.confidence, settings.autonomous_catalog_confidence),
        },
    )
    # The score is left unset: _score_hypotheses rates this correction from the
    # same evidence as every other hypothesis, so a weak brand consensus scores
    # low instead of being pinned to a passing constant.
    return _Hypothesis(item=corrected, source="catalog_correction", catalog_hits=votes)


def _score_hypotheses(
    hypotheses: list[_Hypothesis],
    provider_count: int,
) -> None:
    independent = [
        hypothesis
        for hypothesis in hypotheses
        if hypothesis.source in {"local", "cloud"}
    ]
    for hypothesis in hypotheses:
        catalog_ratio = hypothesis.catalog_hits / max(1, provider_count)
        if hypothesis.source == "catalog_correction":
            # A correction exists precisely because it disagrees with what the
            # engines read, so engine agreement cannot corroborate it. Its
            # corroboration is the independent multi-provider brand consensus,
            # which carries the agreement weight here.
            hypothesis.score = (
                0.35 * hypothesis.item.confidence
                + 0.55 * catalog_ratio
                + 0.10 * _lexicon_score(hypothesis.item)
            )
            continue
        agreement = 1.0 if hypothesis.independent_agreement else max(
            (
                _similarity(hypothesis.item, other.item)
                for other in independent
                if other.source != hypothesis.source
            ),
            default=0,
        )
        if agreement < 0.70:
            agreement = 0
        hypothesis.score = (
            0.35 * hypothesis.item.confidence
            + 0.25 * agreement
            + 0.30 * catalog_ratio
            + 0.10 * _lexicon_score(hypothesis.item)
        )


async def resolve_plan_autonomously(
    local_plan: CartPlan,
    cloud_plan: CartPlan | None,
    providers: dict[str, GroceryProvider],
    settings: Settings,
) -> CartPlan:
    """Accept strong readings and autonomously skip unresolved lines."""
    result = local_plan.model_copy(deep=True)
    cloud_by_id = {
        item.id: item for item in cloud_plan.items
    } if cloud_plan is not None else {}
    accepted = corrected = skipped = 0

    for index, local in enumerate(local_plan.items):
        # A confirmed line is authoritative: the user either typed it or checked
        # it. Adjudicating it against OCR hypotheses could only discard it.
        if not local.needs_review or local.confirmed:
            result.items[index].recognition_decision = "accepted"
            continue
        cloud = cloud_by_id.get(local.id)
        hypotheses = _build_hypotheses(local, cloud, settings)
        trusted_contextual_cloud = any(
            hypothesis.source == "cloud"
            and hypothesis.item.confidence >= 0.9
            and _lexicon_score(hypothesis.item) >= 0.8
            for hypothesis in hypotheses
        )
        search_results = (
            {}
            if trusted_contextual_cloud
            else await _search_hypotheses(providers, hypotheses, settings)
        )
        for hypothesis in hypotheses:
            hypothesis.catalog_hits = _catalog_hits(hypothesis, search_results)
        catalog_correction = _catalog_brand_correction(
            hypotheses, search_results, settings
        )
        if catalog_correction is not None:
            hypotheses.append(catalog_correction)
        _score_hypotheses(hypotheses, len(providers))
        best = max(hypotheses, key=lambda hypothesis: hypothesis.score)
        # Requiring two catalogue votes when the user selected one platform made
        # the catalogue-supported path mathematically impossible. Scale the
        # quorum to the providers actually available; the semantic matcher still
        # has to verify that provider's returned product.
        catalog_quorum = min(
            settings.autonomous_catalog_min_providers,
            max(1, len(providers)),
        )
        catalog_supported = (
            best.catalog_hits >= catalog_quorum
            and best.score >= settings.autonomous_catalog_confidence
        )
        # A partial local word such as "Ma" and a complete cloud read such as
        # "Maggi noodles" have low whole-string similarity even though they are
        # compatible. Permit the cloud hypothesis only when the visible local
        # prefix, grocery lexicon, and a real provider result all corroborate it.
        prefix_catalog_supported = (
            best.source == "cloud"
            and best.catalog_hits >= catalog_quorum
            and _lexicon_score(best.item) >= 0.5
            and _local_cloud_prefix_support(hypotheses)
        )
        contextual_cloud_supported = (
            best.source == "cloud"
            and best.item.confidence >= 0.9
            and _lexicon_score(best.item) >= 0.8
        )
        if (
            best.score >= settings.autonomous_accept_confidence
            or catalog_supported
            or prefix_catalog_supported
            or contextual_cloud_supported
        ):
            chosen_quantity = local.quantity
            chosen_unit = local.unit
            if (
                best.source == "cloud"
                and best.item.unit in {"g", "kg", "ml", "l"}
                and re.search(r"\d", local.raw_text)
            ):
                chosen_quantity = best.item.quantity
                chosen_unit = best.item.unit
            chosen = best.item.model_copy(
                deep=True,
                update={
                    "id": local.id,
                    "quantity": chosen_quantity,
                    "unit": chosen_unit,
                    "crop_box": list(local.crop_box),
                    "needs_review": False,
                    "confirmed": True,
                    "recognition_decision": (
                        "catalog_corrected"
                        if best.source == "catalog_correction"
                        else "accepted"
                    ),
                    "alternatives": list(
                        dict.fromkeys(
                            [
                                *local.alternatives,
                                local.raw_text,
                                *(
                                    [cloud.raw_text, cloud.search_term]
                                    if cloud is not None
                                    else []
                                ),
                            ]
                        )
                    )[:8],
                    "recognition_notes": [
                        *local.recognition_notes,
                        (
                            f"Autonomous recognition accepted this line at "
                            f"{best.score:.0%} confidence using {best.catalog_hits} "
                            "provider catalogue signal"
                            f"{'s' if best.catalog_hits != 1 else ''}."
                        ),
                    ],
                },
            )
            result.items[index] = chosen
            if best.source == "catalog_correction":
                corrected += 1
            else:
                accepted += 1
        else:
            result.items[index] = local.model_copy(
                deep=True,
                update={
                    "needs_review": True,
                    "confirmed": False,
                    "recognition_decision": "skipped",
                    "recognition_notes": [
                        *local.recognition_notes,
                        (
                            f"Autonomous recognition skipped this line because its "
                            f"best evidence score was {best.score:.0%}."
                        ),
                    ],
                },
            )
            skipped += 1

    result.processing_note = (
        f"{result.processing_note} Autonomous recognition accepted {accepted}, "
        f"catalogue-corrected {corrected}, and safely skipped {skipped} uncertain "
        "line"
        f"{'s' if skipped != 1 else ''} without requesting manual review."
    ).strip()
    return result
