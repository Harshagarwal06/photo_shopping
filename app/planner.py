from __future__ import annotations

from io import BytesIO

from PIL import Image

from .config import Settings
from .llm import GroqModelClient, HFModelClient, ModelBackendError, NvidiaModelClient
from .local_vision import plan_locally
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

CLOUD_CORRECTION_SYSTEM = """You correct only uncertain cropped lines from a handwritten
Indian grocery list. The image contains the crops in the same top-to-bottom order as the
items in the prompt. When local alternatives are supplied, use them only as hints and
trust the handwriting.
Never add an item, brand, quantity, or preference that is not visible. Return JSON:
{"corrections":[{"id":"existing id","search_term":"short grocery query",
"context":"brand or empty string","quantity":1,"unit":"one of: item, count, g, kg, ml, l, pack",
"raw_text":"best transcription"}]}. Include exactly one correction for every supplied id."""

def _hosted_client(settings: Settings):
    if settings.model_backend == "groq":
        return GroqModelClient(settings)
    if settings.model_backend == "nvidia":
        return NvidiaModelClient(settings)
    return HFModelClient(settings)


def _uncertain_crop_sheet(image_bytes: bytes, plan: CartPlan) -> tuple[bytes, list]:
    uncertain = [
        item for item in plan.items if item.needs_review and item.crop_box
    ]
    if not uncertain:
        return b"", []
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    row_centers = sorted(
        {
            item.crop_box[1]
            for item in plan.items
            if len(item.crop_box) == 4 and item.crop_box[1] > 0
        },
        reverse=True,
    )
    crops: list[Image.Image] = []
    for item in uncertain:
        x, y, width, height = item.crop_box
        # The local OCR box may cover only the characters it managed to read.
        # For example, a handwritten "Maggi" box ended after "Ma", which meant
        # every hosted model was shown a physically truncated word. Send the
        # complete page-width line so cloud OCR is an independent reading rather
        # than an attempt to complete the local engine's partial crop.
        left = int(image.width * 0.025)
        right = int(image.width * 0.975)
        center_index = min(
            range(len(row_centers)),
            key=lambda index: abs(row_centers[index] - y),
        )
        center = row_centers[center_index]
        above = row_centers[center_index - 1] if center_index > 0 else None
        below = (
            row_centers[center_index + 1]
            if center_index + 1 < len(row_centers)
            else None
        )
        fallback_gap = max(0.06, min(0.18, height))
        upper = (
            (above + center) / 2
            if above is not None
            else min(1.0, center + (center - below) / 2)
            if below is not None
            else min(1.0, center + fallback_gap / 2)
        )
        lower = (
            (center + below) / 2
            if below is not None
            else max(0.0, center - (above - center) / 2)
            if above is not None
            else max(0.0, center - fallback_gap / 2)
        )
        top = max(0, int((1 - upper) * image.height))
        bottom = min(image.height, int((1 - lower) * image.height))
        crop = image.crop((left, top, right, bottom))
        if crop.width < 900:
            scale = 900 / max(1, crop.width)
            crop = crop.resize((900, max(1, int(crop.height * scale))))
        crops.append(crop)
    margin = 28
    sheet_width = max(crop.width for crop in crops) + margin * 2
    sheet_height = sum(crop.height for crop in crops) + margin * (len(crops) + 1)
    sheet = Image.new("RGB", (sheet_width, sheet_height), "white")
    cursor = margin
    for crop in crops:
        sheet.paste(crop, (margin, cursor))
        cursor += crop.height + margin
    output = BytesIO()
    sheet.save(output, format="JPEG", quality=92)
    return output.getvalue(), uncertain


def _contextual_recheck_sheet(image_bytes: bytes, crop_sheet: bytes) -> bytes:
    """One image containing full-list context and isolated row detail."""
    full = Image.open(BytesIO(image_bytes)).convert("RGB")
    crops = Image.open(BytesIO(crop_sheet)).convert("RGB")
    target_width = 1200
    full.thumbnail((target_width, 800))
    crops.thumbnail((target_width, 1800))
    width = max(full.width, crops.width)
    gap = 45
    sheet = Image.new(
        "RGB",
        (width, full.height + crops.height + gap * 2),
        "white",
    )
    sheet.paste(full, ((width - full.width) // 2, gap))
    sheet.paste(
        crops,
        ((width - crops.width) // 2, full.height + gap * 2),
    )
    output = BytesIO()
    sheet.save(output, format="JPEG", quality=90)
    return output.getvalue()


def retry_uncertain_with_cloud(
    *,
    plan: CartPlan,
    image_bytes: bytes,
    settings: Settings,
    blind: bool = False,
) -> CartPlan:
    """Send only uncertain line crops after an explicit user request."""
    backend = settings.cloud_backend
    if backend is None:
        raise ModelBackendError(
            "Cloud retry is unavailable because no hosted model API key is configured."
        )
    crop_sheet, uncertain = _uncertain_crop_sheet(image_bytes, plan)
    if not uncertain:
        return plan
    prompt_items = (
        [{"id": item.id} for item in uncertain]
        if blind
        else [
            {
                "id": item.id,
                "local_reading": item.raw_text,
                "local_query": item.search_term,
                "alternatives": item.alternatives,
            }
            for item in uncertain
        ]
    )
    cloud_settings = settings.model_copy(update={"model_backend": backend})
    whole_photo_used = (
        blind
        and backend == "groq"
        and len(uncertain) == len(plan.items)
        and len(uncertain) >= 3
    )
    request_image = (
        _contextual_recheck_sheet(image_bytes, crop_sheet)
        if whole_photo_used
        else crop_sheet
    )
    prompt = (
        (
            "The top section is the complete list for context. Below it are "
            f"{len(uncertain)} isolated versions of those same rows in top-to-bottom "
            "order. Reconcile the full context with each detailed row. Arrows are "
            "bullets, never quantities. Do not merge rows or copy quantities between "
            "rows. Common Indian grocery wording may include mung/moong dal, chana "
            "bhuna, garam masala, and aam ka achar. "
        )
        if whole_photo_used
        else ""
    ) + (
        f"Uncertain items in crop order: {prompt_items}\n"
        "Read each crop independently, correct each supplied id, and return "
        "the JSON object only."
    )
    payload = _hosted_client(cloud_settings).complete_json(
        model=settings.cloud_model,
        system=CLOUD_CORRECTION_SYSTEM,
        prompt=prompt,
        image_bytes=request_image,
        image_media_type="image/jpeg",
        max_tokens=1000,
    )
    corrections = payload.get("corrections")
    if corrections is None and payload.get("id"):
        # A hosted model may unwrap a single requested correction. Accept that
        # narrow shape while still validating it against an existing item id.
        corrections = [payload]
    if not isinstance(corrections, list):
        raise ModelBackendError("The cloud retry did not return a corrections list.")
    by_id = {
        str(correction.get("id")): correction
        for correction in corrections
        if isinstance(correction, dict)
    }
    for item in uncertain:
        correction = by_id.get(item.id)
        if not correction:
            continue
        try:
            corrected_unit = str(correction.get("unit", item.unit)).strip()
            if corrected_unit not in {"item", "count", "g", "kg", "ml", "l", "pack"}:
                corrected_unit = item.unit
            updated = item.model_copy(
                update={
                    "search_term": str(correction["search_term"]).strip(),
                    "context": str(correction.get("context", "")).strip(),
                    "quantity": float(correction.get("quantity", 1)),
                    "unit": corrected_unit,
                    "raw_text": str(correction.get("raw_text", item.raw_text)).strip(),
                    # Hosted OCR is a second opinion, not an authority. Keep the
                    # line excluded until the user checks the crop and confirms it.
                    "confidence": max(
                        item.confidence,
                        0.9 if whole_photo_used else 0.65,
                    ),
                    "needs_review": True,
                    "confirmed": False,
                    "recognition_notes": [
                        *item.recognition_notes,
                        (
                            f"{backend.title()} cloud vision suggested this reading; "
                            "confirm it manually before provider search."
                        ),
                    ],
                }
            )
            # Revalidate updates, especially positive quantities and supported shapes.
            item_index = next(
                index for index, existing in enumerate(plan.items) if existing.id == item.id
            )
            plan.items[item_index] = type(item).model_validate(updated.model_dump())
        except (KeyError, TypeError, ValueError):
            continue
    plan.processing_note += (
        f" Uncertain line crops were sent to the configured {backend.title()} model "
        + (
            "and the complete image was rechecked because every local row was uncertain. "
            if whole_photo_used
            else "after explicit approval; the rest of the photo stayed local. "
        )
        + "Cloud suggestions "
        "remain marked for review."
    )
    return plan


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
        # Demo mode mocks only the grocery provider. Parsing the user's actual
        # request keeps the preview honest and exercises the same local planning
        # path used when hosted inference is unavailable.
        return plan_locally(
            text=text,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
        )
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
        payload = _hosted_client(settings).complete_json(
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
