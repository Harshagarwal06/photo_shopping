from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

from pydantic import ValidationError

from .config import Settings
from .constraints import parse_measurement, requested_measurement, units_for_candidate
from .llm import HFModelClient, ModelBackendError, NvidiaModelClient
from .models import CrossPlatformMatch, MatchDecision, PlannedItem, Product


MATCHER_SYSTEM = """You rank real grocery product candidates for one planned grocery item.
Return only a JSON object with product_id, units_to_add, and reason. Choose only an in-stock
candidate id from the supplied list. Respect the item's context and requested measurement.
Compute purchasable units, not loose quantity: 12 eggs means one 12-count tray, not 12 trays;
2 litres of milk can mean one 2 L pack or two 1 L packs. Prefer the closest adequate amount,
then context/relevance, then lower total price. Keep the reason to one short sentence.

Schema: {"product_id": "candidate id", "units_to_add": 1, "reason": "short reason"}
"""


def _hosted_client(settings: Settings):
    if settings.model_backend == "nvidia":
        return NvidiaModelClient(settings)
    return HFModelClient(settings)


TOKEN_RE = re.compile(r"[a-z0-9]+")
IGNORED_TOKENS = {
    "a",
    "an",
    "and",
    "for",
    "fresh",
    "bottle",
    "box",
    "can",
    "carton",
    "loaf",
    "of",
    "pack",
    "packet",
    "the",
    "with",
}
KNOWN_BRAND_PHRASES = {
    "aashirvaad", "amul", "britannia", "cadbury", "daawat", "fortune",
    "kelloggs", "knorr", "kurkure", "maggi", "mother dairy", "oreo",
    "pintola", "real", "rin", "tata",
}
MATCH_SYNONYMS = {
    "dishwashing": {"dishwash"},
    "dishwash": {"dishwashing"},
    "liquid": {"gel"},
    "gel": {"liquid"},
}
MATCH_TOKEN_ALIASES = {
    "cao": "cow",
    "mill": "milk",
}
REQUIRED_MODIFIER_GROUPS = {
    "boneless": {"boneless"},
    "brown": {"brown"},
    "decaf": {"decaf", "decaffeinated"},
    "fat free": {"fat free", "zero fat"},
    "gluten free": {"gluten free"},
    "lactose free": {"lactose free"},
    "low fat": {"low fat", "skimmed", "slim"},
    "no added sugar": {"no added sugar", "zero added sugar"},
    "organic": {"organic"},
    "skinless": {"skinless"},
    "sugar free": {"sugar free", "no sugar", "zero sugar"},
    "vegan": {"vegan"},
    "whole wheat": {"whole wheat", "100 whole wheat"},
}
ORDER_SENSITIVE_PHRASES = {
    # "chai masala" is a spice; "masala chai/tea" is the prepared tea product.
    "masala chai": {"masala chai", "masala tea"},
}


def _tokens(value: str) -> set[str]:
    return {
        MATCH_TOKEN_ALIASES.get(token, token)
        for token in TOKEN_RE.findall(value.casefold())
        if token not in IGNORED_TOKENS and not token.isdigit()
    }


def _token_similarity(query: str, candidate: str) -> float:
    if query == candidate or candidate in MATCH_SYNONYMS.get(query, set()):
        return 1.0
    return SequenceMatcher(None, query, candidate).ratio()


def _contains_phrase(value: str, phrase: str) -> bool:
    pattern = re.escape(phrase.casefold()).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", value.casefold()) is not None


def _match_is_reasonable(item: PlannedItem, product: Product) -> tuple[bool, str]:
    """Fail closed when provider search results have no credible request relation."""
    if item.needs_review and not item.confirmed:
        return False, "The transcription needs review before product matching."
    query_tokens = _tokens(item.search_term)
    name_tokens = _tokens(product.name)
    context = item.context.casefold()
    requested_brands = {
        brand for brand in KNOWN_BRAND_PHRASES if _contains_phrase(context, brand)
    }
    # Indian shopping lists commonly call a Rin detergent bar "Rin soap".
    # Keep this equivalence brand-scoped so a generic request for bathing soap
    # cannot silently select a laundry product.
    if (
        ("rin" in requested_brands or "rin" in query_tokens)
        and "soap" in query_tokens
        and {"detergent", "bar"} <= name_tokens
    ):
        name_tokens.add("soap")
    # Some provider titles use only a dairy brand/variant name ("Amul Taaza")
    # and rely on the search page to supply the missing category.
    if (
        query_tokens == {"milk"}
        and re.search(r"\b(?:ml|l|litre|liter)\b", product.pack_size.casefold())
        and name_tokens
        & {"amul", "chitale", "cow", "dairy", "mother", "taaza", "toned"}
    ):
        name_tokens.add("milk")
    if not query_tokens or not name_tokens:
        return False, "No meaningful product words overlap."

    request_text = f"{item.search_term} {item.context}".casefold()
    product_text = product.name.casefold()
    for requested_phrase, accepted_phrases in ORDER_SENSITIVE_PHRASES.items():
        if not _contains_phrase(request_text, requested_phrase):
            continue
        if not any(_contains_phrase(product_text, phrase) for phrase in accepted_phrases):
            return False, f"The requested phrase “{requested_phrase}” is not present."
    for modifier, accepted_phrases in REQUIRED_MODIFIER_GROUPS.items():
        if not _contains_phrase(request_text, modifier):
            continue
        if not any(_contains_phrase(product_text, phrase) for phrase in accepted_phrases):
            return False, f"The required modifier “{modifier}” is not present."

    matched = 0
    similarities: list[float] = []
    for query in query_tokens:
        best = max((_token_similarity(query, name) for name in name_tokens), default=0)
        similarities.append(best)
        threshold = 0.65 if len(query) <= 2 else 0.72 if len(query) <= 4 else 0.78
        if best >= threshold:
            matched += 1
    required = 1 if len(query_tokens) == 1 else math.ceil(len(query_tokens) * 2 / 3)
    if matched < required:
        return False, (
            f"Only {matched} of {len(query_tokens)} request words plausibly match "
            "this product."
        )

    product_name = product.name.casefold()
    missing_brand = next(
        (
            brand
            for brand in requested_brands
            if not _contains_phrase(product_name, brand)
        ),
        None,
    )
    if missing_brand:
        return False, f"The requested brand {missing_brand.title()} is not present."
    return True, ""


def _quantity_fit(item: PlannedItem, product: Product, units: int) -> tuple[float, str | None]:
    requested = requested_measurement(item)
    packed = parse_measurement(product.pack_size or product.name)
    if not requested or not packed or requested[1] != packed[1] or requested[0] <= 0:
        return 6.0, None
    delivered = packed[0] * units
    ratio = delivered / requested[0]
    # Exact quantities score 16. The score falls smoothly as over/under-supply grows.
    score = max(0.0, 16.0 - abs(math.log(max(ratio, 0.01), 2)) * 8.0)
    label = product.pack_size or product.name
    return score, f"{label} is the closest pack fit"


# The provider's own ordering knows things this scorer cannot: what people
# actually buy for these words, what is in stock nearby, how the query relates to
# the catalogue. It matters most exactly when our text signal is weakest — a
# photographed "Thumbs u" matches no product name at all, leaving price to choose
# between Thums Up and Coca-Cola. Kept below the previous-order boost (26),
# relevance (36) and pack fit (16) so it decides ties rather than overriding a
# genuinely better match.
POSITION_WEIGHT = 10.0


def _score_candidate(
    item: PlannedItem,
    product: Product,
    *,
    units: int,
    lowest_total: float,
    position: int = 0,
) -> tuple[float, list[str]]:
    query_tokens = _tokens(f"{item.search_term} {item.context}")
    name_tokens = _tokens(product.name)
    overlap = len(query_tokens & name_tokens)
    relevance = overlap / max(1, len(query_tokens))
    score = relevance * 36.0
    reasons: list[str] = []
    if relevance >= 0.75:
        reasons.append("strong request match")

    prefer_lowest_price = bool(
        re.search(r"\b(?:cheapest|lowest|affordable)\b", item.context, re.IGNORECASE)
    )
    position_weight = POSITION_WEIGHT / 2 if prefer_lowest_price else POSITION_WEIGHT
    score += position_weight / (1 + position)
    if position == 0:
        reasons.append("the top result for this search")

    total = product.price * units
    if total > 0:
        price_weight = 30.0 if prefer_lowest_price else 15.0
        score += price_weight * min(1.0, lowest_total / total)
    reasons.append(f"₹{total:g} for {units} pack{'s' if units != 1 else ''}")

    quantity_score, quantity_reason = _quantity_fit(item, product, units)
    score += quantity_score
    if quantity_reason:
        reasons.append(quantity_reason)

    if product.past_order_count:
        score += min(26.0, 10.0 * math.log2(product.past_order_count + 1))
        reasons.insert(
            0,
            f"ordered {product.past_order_count} time{'s' if product.past_order_count != 1 else ''} before",
        )

    if product.rating is not None:
        confidence = min(1.0, math.log10(product.review_count + 10) / 3.0)
        score += (product.rating / 5.0) * 12.0 * confidence
        score += min(3.0, math.log10(product.review_count + 1))
        rating_reason = f"{product.rating:g}★"
        if product.review_count:
            rating_reason += f" from {product.review_count:,} reviews"
        reasons.append(rating_reason)

    if product.discount_percent:
        score += min(6.0, product.discount_percent / 10.0)
        reasons.append(f"{product.discount_percent:g}% off")
    if product.delivery_minutes is not None:
        score += max(0.0, 4.0 - max(0, product.delivery_minutes - 10) / 10.0)
        reasons.append(f"{product.delivery_minutes}-minute delivery")
    if product.sponsored:
        score -= 3.0
    return score, reasons


def _fallback_match(item: PlannedItem, candidates: list[Product]) -> MatchDecision:
    if item.needs_review and not item.confirmed:
        return MatchDecision(
            product_id=None,
            units_to_add=0,
            reason="Review this transcription before searching for a product.",
        )
    available = [
        candidate
        for candidate in candidates
        if candidate.in_stock and _match_is_reasonable(item, candidate)[0]
    ]
    if not available:
        return MatchDecision(
            product_id=None,
            units_to_add=0,
            reason="No confident product match found; edit or confirm the request.",
        )
    candidate_units = [(candidate, units_for_candidate(item, candidate)) for candidate in available]
    lowest_total = min(candidate.price * units for candidate, units in candidate_units)
    scored: list[tuple[float, float, int, Product, int, list[str]]] = []
    for index, candidate in enumerate(available):
        units = units_for_candidate(item, candidate)
        score, reasons = _score_candidate(
            item,
            candidate,
            units=units,
            lowest_total=lowest_total,
            position=index,
        )
        scored.append((score, -(candidate.price * units), -index, candidate, units, reasons))
    _, _, _, selected, units, reasons = max(scored, key=lambda entry: entry[:3])
    reason = "Selected automatically"
    if reasons:
        reason += ": " + "; ".join(reasons[:4])
    reason += "."
    return MatchDecision(
        product_id=selected.id,
        units_to_add=units,
        reason=reason,
    )


def match_product(
    item: PlannedItem,
    candidates: list[Product],
    settings: Settings,
) -> MatchDecision:
    if not candidates:
        return MatchDecision(
            product_id=None,
            units_to_add=0,
            reason="The selected grocery provider returned no results.",
        )
    if settings.demo_mode or settings.safety_lock or settings.model_backend == "local":
        return _fallback_match(item, candidates)

    candidate_payload = [
        {
            "id": product.id,
            "name": product.name,
            "pack_size": product.pack_size,
            "price": product.price,
            "mrp": product.mrp,
            "discount_percent": product.discount_percent,
            "delivery_minutes": product.delivery_minutes,
            "rating": product.rating,
            "review_count": product.review_count,
            "past_order_count": product.past_order_count,
            "sponsored": product.sponsored,
            "in_stock": product.in_stock,
        }
        for product in candidates
    ]
    prompt = (
        f"Planned item: {item.model_dump_json()}\n"
        f"Candidates: {candidate_payload}\n"
        "Pick the best candidate and units."
    )
    try:
        payload = _hosted_client(settings).complete_json(
            model=settings.matcher_model,
            system=MATCHER_SYSTEM,
            prompt=prompt,
            max_tokens=500,
        )
    except ModelBackendError:
        if settings.local_vision_fallback:
            return _fallback_match(item, candidates)
        raise
    try:
        decision = MatchDecision.model_validate(payload)
    except Exception as exc:
        raise ModelBackendError(f"The matcher returned an invalid decision: {exc}") from exc
    valid_ids = {candidate.id for candidate in candidates if candidate.in_stock}
    if decision.product_id not in valid_ids:
        raise ModelBackendError("The matcher selected an unavailable or unknown product.")
    selected = next(candidate for candidate in candidates if candidate.id == decision.product_id)
    reasonable, reason = _match_is_reasonable(item, selected)
    if not reasonable:
        return MatchDecision(
            product_id=None,
            units_to_add=0,
            reason=f"No confident product match found: {reason}",
        )
    return decision


CROSS_MATCHER_SYSTEM = """You match one planned grocery item against candidates from
several Indian instant-delivery platforms at once. Prefer the SAME brand and pack size
on every platform so their prices are comparable. Only choose in-stock candidate ids
from that platform's own list. If a platform has no reasonable equivalent, set its
product_id to null and units_to_add to 0 — do not force a poor match.
Compute purchasable units, not loose quantity: 12 eggs is one 12-count tray.

Schema: {"picks": {"<provider>": {"product_id": "id or null", "units_to_add": 1,
"reason": "short reason"}}, "equivalence_note": "one short sentence"}
"""


def match_across_platforms(
    item: PlannedItem,
    candidates_by_provider: dict[str, list[Product]],
    settings: Settings,
) -> CrossPlatformMatch:
    """Pick a comparable product on each platform, or report no equivalent."""
    if not candidates_by_provider:
        return CrossPlatformMatch()

    if settings.demo_mode or settings.safety_lock or settings.model_backend == "local":
        return _fallback_cross_match(item, candidates_by_provider)

    payload = {
        provider: [
            {
                "id": product.id, "name": product.name, "pack_size": product.pack_size,
                "price": product.price, "in_stock": product.in_stock,
            }
            for product in candidates
        ]
        for provider, candidates in candidates_by_provider.items()
    }
    prompt = (
        f"Planned item: {item.model_dump_json()}\n"
        f"Candidates by platform: {payload}\n"
        "Pick the most comparable product on each platform."
    )
    try:
        raw = _hosted_client(settings).complete_json(
            model=settings.matcher_model,
            system=CROSS_MATCHER_SYSTEM,
            prompt=prompt,
            max_tokens=800,
        )
    except ModelBackendError:
        if not settings.local_vision_fallback:
            raise
        return _fallback_cross_match(item, candidates_by_provider)

    try:
        result = CrossPlatformMatch.model_validate(raw)
    except ValidationError:
        # A malformed shape is recoverable: fall back rather than fail the run.
        return _fallback_cross_match(item, candidates_by_provider)

    # Never trust the model's ids or provider keys: rebuild picks from scratch,
    # restricted strictly to the providers we actually asked about, and accept
    # only ids that are real in-stock candidates on that same provider's list.
    # This also drops any provider key the model invented that we never sent.
    verified_picks: dict[str, MatchDecision] = {}
    for provider, candidates in candidates_by_provider.items():
        valid = {p.id for p in candidates if p.in_stock}
        decision = result.picks.get(provider)
        if decision is not None and decision.product_id in valid:
            verified_picks[provider] = decision
        else:
            verified_picks[provider] = _fallback_match(item, candidates)
    result.picks = verified_picks
    return result


def _fallback_cross_match(
    item: PlannedItem,
    candidates_by_provider: dict[str, list[Product]],
) -> CrossPlatformMatch:
    picks = {
        provider: _fallback_match(item, candidates)
        for provider, candidates in candidates_by_provider.items()
    }
    missing = sorted(p for p, d in picks.items() if d.product_id is None)
    note = (
        f"No equivalent found on {', '.join(missing)}."
        if missing
        else "Matched independently on each platform."
    )
    return CrossPlatformMatch(picks=picks, equivalence_note=note)
