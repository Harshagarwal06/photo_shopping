from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .llm import ModelBackendError
from .models import CartConstraints, CartPlan, PlannedItem


VISION_SCRIPT = Path(__file__).with_name("vision_ocr.swift")
BUDGET_RE = re.compile(
    r"\b(?:under|below|budget(?:\s+of)?|up\s*to|upto)\s*₹?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
LEADING_QUANTITY_RE = re.compile(
    r"^(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|gm|l|ltr|ml|pcs?|pieces?|count|packs?)?\s+(?P<name>.+)$",
    re.IGNORECASE,
)
TRAILING_QUANTITY_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|gm|l|ltr|ml|pcs?|pieces?|count|packs?)$",
    re.IGNORECASE,
)
UNIT_ALIASES = {
    "gm": "g",
    "ltr": "l",
    "pc": "count",
    "pcs": "count",
    "piece": "count",
    "pieces": "count",
    "pack": "pack",
    "packs": "pack",
}
TERM_ALIASES = {
    "doodh": "milk",
    "dudh": "milk",
    "ande": "eggs",
    "anda": "eggs",
}


def recognize_text(image_bytes: bytes, image_media_type: str) -> str:
    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/webp": ".webp",
    }.get(image_media_type.lower(), ".img")
    with tempfile.NamedTemporaryFile(suffix=suffix) as image_file:
        image_file.write(image_bytes)
        image_file.flush()
        try:
            result = subprocess.run(
                ["/usr/bin/swift", str(VISION_SCRIPT), image_file.name],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise ModelBackendError(
                f"On-device handwriting recognition failed: {detail.strip()}"
            ) from exc
    return result.stdout.strip()


def _normalise_unit(unit: str | None, *, had_quantity: bool) -> str:
    if not unit:
        return "count" if had_quantity else "item"
    lowered = unit.lower()
    return UNIT_ALIASES.get(lowered, lowered)


def _clean_name(name: str) -> str:
    cleaned = re.sub(r"\b(?:please|get|buy|need|add)\b", " ", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    return TERM_ALIASES.get(cleaned.lower(), cleaned)


def _parse_item(raw: str, source: str) -> PlannedItem | None:
    value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
    value = BUDGET_RE.sub("", value).strip(" ,.-")
    if not value:
        return None

    match = TRAILING_QUANTITY_RE.match(value) or LEADING_QUANTITY_RE.match(value)
    if match:
        quantity = float(match.group("quantity"))
        unit = _normalise_unit(match.group("unit"), had_quantity=True)
        name = _clean_name(match.group("name"))
    else:
        quantity = 1
        unit = "item"
        name = _clean_name(value)
    if not name:
        return None
    return PlannedItem(
        search_term=name,
        quantity=quantity,
        unit=unit,
        raw_text=raw.strip(),
        source=source,
    )


def plan_locally(
    *,
    text: str,
    image_bytes: bytes | None,
    image_media_type: str,
) -> CartPlan:
    photo_text = recognize_text(image_bytes, image_media_type) if image_bytes else ""
    origin = "both" if text.strip() and photo_text else "photo" if photo_text else "text"
    combined = "\n".join(part for part in (text.strip(), photo_text) if part)
    budget_match = BUDGET_RE.search(combined)
    budget = float(budget_match.group(1).replace(",", "")) if budget_match else None

    fragments = re.split(r"[\n,;]+|\s+\band\b\s+", combined, flags=re.IGNORECASE)
    items: list[PlannedItem] = []
    seen: set[str] = set()
    for fragment in fragments:
        item = _parse_item(fragment, origin)
        if item and item.search_term.lower() not in seen:
            items.append(item)
            seen.add(item.search_term.lower())
    if not items:
        raise ModelBackendError(
            "The hosted model is unavailable and on-device recognition could not find "
            "grocery items. Try a clearer, well-lit photo or add a typed request."
        )

    note = (
        "Processed locally on this Mac. The handwriting photo was not sent to an "
        "external model provider."
        if image_bytes
        else "Processed locally on this Mac."
    )
    return CartPlan(
        items=items,
        constraints=CartConstraints(cart_budget=budget),
        processing_note=note,
    )
