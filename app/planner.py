from __future__ import annotations

from .config import Settings
from .local_vision import plan_locally
from .llm import HFModelClient, ModelBackendError
from .models import CartPlan


PLANNER_SYSTEM = """You are the planning stage of a personal Indian grocery-cart assistant.
Return only valid JSON. Read English, Hindi (Devanagari), and Hinglish. Convert grocery
terms to short English Blinkit search queries while preserving the original phrase in
raw_text. Expand named dishes into sensible ingredient items for the stated serving count.
Never invent an explicit budget, cap, brand, dietary preference, or quantity.

Schema:
{
  "items": [{
    "search_term": "short retrieval query",
    "context": "selection context, dish use, preference, or empty string",
    "quantity": 1,
    "unit": "item|count|g|kg|ml|l|pack",
    "raw_text": "original wording",
    "source": "text|photo|both|expanded from: <dish>"
  }],
  "constraints": {
    "cart_budget": null,
    "item_caps": {"search term or raw phrase": 100},
    "preferences": ["cheapest", "brand: ...", "dietary: ..."]
  }
}

For dish expansion, tag every ingredient source exactly "expanded from: <dish>".
Quantities must be positive numbers. If quantity is unstated, use 1 item or 1 pack.
Separate retrieval language from context: search "tomatoes", not "tomatoes for salad".
"""


def _demo_plan(text: str) -> CartPlan:
    # Explicit demo data keeps local UI testing possible without sending data anywhere.
    lowered = text.lower()
    budget = 800 if "800" in lowered else None
    payload = {
        "items": [
            {
                "search_term": "milk",
                "context": "regular dairy milk",
                "quantity": 2,
                "unit": "l",
                "raw_text": "doodh 2L",
                "source": "text",
            },
            {
                "search_term": "eggs",
                "context": "chicken eggs",
                "quantity": 12,
                "unit": "count",
                "raw_text": "12 ande",
                "source": "text",
            },
            {
                "search_term": "dishwashing liquid",
                "context": "prefer lowest total price",
                "quantity": 1,
                "unit": "pack",
                "raw_text": "dish soap",
                "source": "text",
            },
        ],
        "constraints": {
            "cart_budget": budget,
            "item_caps": {},
            "preferences": ["cheapest dish soap"],
        },
    }
    return CartPlan.model_validate(payload)


def plan_cart(
    *,
    text: str,
    image_bytes: bytes | None,
    image_media_type: str,
    settings: Settings,
) -> CartPlan:
    if not text.strip() and not image_bytes:
        raise ValueError("Add a photo, a typed request, or both.")
    if settings.demo_mode:
        return _demo_plan(text)
    if settings.model_backend == "local":
        return plan_locally(
            text=text,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
        )

    origin = "both" if text.strip() and image_bytes else "text" if text.strip() else "photo"
    prompt = (
        f"Input source: {origin}.\n"
        f"Typed request: {text.strip() or '[none]'}\n"
        "Create the cart plan now. Return the JSON object only."
    )
    try:
        payload = HFModelClient(settings).complete_json(
            model=settings.planner_model,
            system=PLANNER_SYSTEM,
            prompt=prompt,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            max_tokens=2400,
        )
    except ModelBackendError:
        if not settings.local_vision_fallback:
            raise
        return plan_locally(
            text=text,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
        )
    try:
        plan = CartPlan.model_validate(payload)
    except Exception as exc:
        raise ModelBackendError(f"The planner returned an invalid cart plan: {exc}") from exc
    for item in plan.items:
        if not item.raw_text:
            item.raw_text = item.search_term
        if item.source == "direct":
            item.source = origin
    return plan
