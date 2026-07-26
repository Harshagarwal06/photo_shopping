from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
from itertools import product
from pathlib import Path

from .llm import ModelBackendError
from .models import CartConstraints, CartPlan, PlannedItem


VISION_SCRIPT = Path(__file__).with_name("vision_ocr.swift")
BUDGET_RE = re.compile(
    r"\b(?:under|below|budget(?:\s+of)?|up\s*to|upto)\s*₹?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
VULGAR_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
FRACTION_CHARS = "".join(VULGAR_FRACTIONS)
# Longest form first: "1/2" must win over the bare "1" that also matches it.
QUANTITY_PATTERN = (
    rf"\d+\s*[{FRACTION_CHARS}]|[{FRACTION_CHARS}]|\d+\s*/\s*\d+|\d+(?:\.\d+)?"
)
UNIT_PATTERN = (
    r"kg|g|gm|l|ltr|ml|pcs?|pieces?|count|dozens?|packets?|packs?"
    r"|bottles?|tins?|cans?"
)
LEADING_QUANTITY_RE = re.compile(
    rf"^(?P<quantity>{QUANTITY_PATTERN})\s*(?P<unit>{UNIT_PATTERN})?\s+(?P<name>.+)$",
    re.IGNORECASE,
)
TRAILING_QUANTITY_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<quantity>{QUANTITY_PATTERN})\s*(?P<unit>{UNIT_PATTERN})$",
    re.IGNORECASE,
)
# "dozen eggs" means one dozen. Restricted to container words on purpose: letting
# a bare "l" or "g" imply a quantity would rewrite ordinary product names.
IMPLICIT_SINGLE_RE = re.compile(
    r"^(?=(?:dozens?|packets?|packs?|bottles?|tins?)\b)", re.IGNORECASE
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
    "packet": "pack",
    "packets": "pack",
    "dozens": "dozen",
    # Containers all normalise to "pack": constraints.units_for_candidate only
    # multiplies the requested count for "pack", so a unit name of its own would
    # silently turn "2 bottles" into one.
    "bottle": "pack",
    "bottles": "pack",
    "tin": "pack",
    "tins": "pack",
    "can": "pack",
    "cans": "pack",
}
# Hinglish and common misspellings mapped to the term most likely to match a
# product listing. Deliberately conservative: a word is translated only where the
# English one is the label Indian grocery apps actually use. Terms that are
# themselves the retail name — paneer, atta, besan, ghee, dal, bhindi, maida —
# are left alone or only spelling-normalised, because translating them ("okra",
# "clarified butter") searches for something the catalogue does not call it.
TERM_ALIASES = {
    # dairy and eggs
    "doodh": "milk",
    "dudh": "milk",
    "ande": "eggs",
    "anda": "eggs",
    "andey": "eggs",
    "dahi": "curd",
    "makhan": "butter",
    "makkhan": "butter",
    # staples
    "chawal": "rice",
    "chaval": "rice",
    "aata": "atta",
    "sooji": "suji",
    "cheeni": "sugar",
    "chini": "sugar",
    "shakkar": "sugar",
    "namak": "salt",
    "tel": "oil",
    "paani": "water",
    "pani": "water",
    # vegetables
    "pyaz": "onion",
    "pyaaz": "onion",
    "kanda": "onion",
    "aloo": "potato",
    "alu": "potato",
    "tamatar": "tomato",
    "tamaatar": "tomato",
    "gajar": "carrot",
    "adrak": "ginger",
    "lehsun": "garlic",
    "lahsun": "garlic",
    # spices and staples of the masala dabba
    "haldi": "turmeric",
    "jeera": "cumin",
    "dhaniya": "coriander",
    "dhania": "coriander",
    "mirch": "chilli",
    "mirchi": "chilli",
    # everything else
    "chai": "tea",
    "chai patti": "tea",
    "sabun": "soap",
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


# Products the catalogues already name correctly. Listing them stops the fuzzy
# pass below from dragging a good word onto a lexicon entry: "paneer" scores 0.8
# against "pani", which would order water.
RETAIL_TERMS = frozenset(
    {
        "atta", "besan", "bhindi", "chana", "coffee", "dal", "ghee", "gobi",
        "jaggery", "kaju", "maida", "maggi", "methi", "moong", "murmura",
        "paneer", "poha", "rajma", "rava", "sooji", "suji", "toor", "upma",
    }
)
# Every spelling the parser treats as already correct: lexicon keys, what they
# resolve to, and the retail terms above. Sorted so difflib breaks ties the same
# way on every run regardless of hash seed.
KNOWN_TERMS = tuple(sorted(set(TERM_ALIASES) | set(TERM_ALIASES.values()) | RETAIL_TERMS))
# Characters Vision most often confuses on handwriting, each mapped to what it
# was probably meant to be. Substituting and testing for an exact match resolves
# "0nion" without the loose fuzzy threshold that would also break "paneer".
OCR_CONFUSIONS = {
    "0": ("o",),
    "1": ("l", "i"),
    "5": ("s",),
    "8": ("b",),
    "|": ("l", "i"),
}
# Letter pairs handwriting blurs: "Jeera" read as "Jecra", "Paneer" as "Panees".
# Applied one at a time, unlike the digit swaps above, because a word with five
# confusable letters would otherwise generate dozens of spellings. One misread
# letter per word covers the overwhelming majority and keeps the search exact.
LETTER_CONFUSIONS = {
    "c": "e", "e": "c", "s": "r", "r": "s", "n": "h", "h": "n",
    "u": "v", "v": "u", "o": "a", "a": "o", "i": "l", "l": "i",
    "g": "q", "q": "g", "m": "n",
}
# A photographed list often starts with a title. Without this it is searched for
# as though it were a product, and Blinkit obligingly sells something.
HEADER_RE = re.compile(r"^(?:\w+\W+){0,2}lists?\W*$", re.IGNORECASE)
# Ruled notebooks carry their own printed words, and OCR reads them alongside the
# writing. None is ever a grocery item, and "Date" or "Page No." would otherwise
# be searched for and matched against something.
STATIONERY_RE = re.compile(
    r"^(?:good\s*luck|page\s*n[o0]\.?|date|name|class|subject|roll\s*n[o0]\.?|"
    r"sr\.?\s*n[o0]\.?|topic|day)\W*$",
    re.IGNORECASE,
)
# The heading is often the worst-read line on the page: "Grocery list" has come
# back as "Grocery hist" and "Grocery ust". Recognise it by its first word rather
# than its last, because "ust" is no closer to "list" than "salt" is, while
# "Crocery" is unmistakably "Grocery". Only these two words: "sabzi" or "market"
# would swallow real products such as "sabzi masala".
HEADING_WORDS = ("grocery", "shopping")
# "Paneer (Amul)" is a product and a brand, not a product called "paneer (amul)".
# The brand moves to the item's context, which the ranker already scores against.
PARENTHETICAL_RE = re.compile(r"\(([^)]*)\)")
# ... unless the brackets hold the amount, as in "Atta (5 kg)", in which case the
# text belongs back on the line for the quantity parsing to read.
QUANTITY_ONLY_RE = re.compile(
    rf"^(?:{QUANTITY_PATTERN})\s*(?:{UNIT_PATTERN})$", re.IGNORECASE
)
# "(Amul - 500ml)" is a brand and a size together: the size belongs on the line,
# the brand in the context.
MEASUREMENT_IN_NOTE_RE = re.compile(
    rf"(?:{QUANTITY_PATTERN})\s*(?:{UNIT_PATTERN})\b", re.IGNORECASE
)
# An opening bracket in cursive is read as a "C" often enough to matter: the list
# that read "CAmul - 500ml)" lost both the brand and the size. Only repaired when
# the line closes a bracket it never opened.
# The lookahead forbids a further "C" so that the repair lands on the "C" nearest
# the bracket: in "Cao mill CAmul - 500ml)" the first one is part of the product.
UNOPENED_BRACKET_RE = re.compile(r"\bC(?=\w[^()C]*\))")
MAX_CONFUSION_VARIANTS = 8
# 0.85 is deliberately tight. At 0.8, "paneer"/"pani" and "0nion"/"onion" score
# identically, so no threshold separates them — hence OCR_CONFUSIONS.
FUZZY_CUTOFF = 0.85
# Short words collide too easily: "tea" and "tel" (oil) are one edit apart.
MIN_FUZZY_LENGTH = 5


def _confusion_variants(token: str) -> list[str]:
    """Spellings of a token with likely OCR character confusions undone."""
    variants: list[str] = []
    if "rn" in token:
        variants.append(token.replace("rn", "m"))
    choices = [OCR_CONFUSIONS.get(char, (char,)) for char in token]
    total = 1
    for choice in choices:
        total *= len(choice)
    # No lower bound: an unambiguous swap such as "0" -> "o" yields exactly one
    # combination, and the identity spelling is filtered out on the way back.
    if total <= MAX_CONFUSION_VARIANTS:
        variants.extend("".join(combination) for combination in product(*choices))
    for index, char in enumerate(token):
        swap = LETTER_CONFUSIONS.get(char)
        if swap:
            variants.append(f"{token[:index]}{swap}{token[index + 1:]}")
    return [variant for variant in variants if variant != token]


def _resolve_term(token: str) -> str:
    """Map one word onto a searchable term, undoing misreads where it is safe."""
    if token in TERM_ALIASES:
        return TERM_ALIASES[token]
    if token in KNOWN_TERMS:
        return token
    for variant in _confusion_variants(token):
        if variant in TERM_ALIASES:
            return TERM_ALIASES[variant]
        if variant in KNOWN_TERMS:
            return variant
    if len(token) >= MIN_FUZZY_LENGTH:
        close = difflib.get_close_matches(token, KNOWN_TERMS, n=1, cutoff=FUZZY_CUTOFF)
        if close:
            return TERM_ALIASES.get(close[0], close[0])
    return token


def _parse_quantity(raw: str) -> float | None:
    """Read "2", "2.5", "1/2", "½", or "1½". None when the text cannot be a count."""
    value = raw.strip()
    for symbol, fraction in VULGAR_FRACTIONS.items():
        if value.endswith(symbol):
            whole = value[: -len(symbol)].strip()
            return (float(whole) if whole else 0.0) + fraction
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        divisor = float(denominator.strip())
        if divisor == 0:
            return None
        return float(numerator.strip()) / divisor
    return float(value)


def _clean_name(name: str) -> str:
    cleaned = re.sub(r"\b(?:please|get|buy|need|add)\b", " ", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    # The whole phrase first, so multi-word entries such as "chai patti" resolve
    # as one term rather than word by word.
    whole = TERM_ALIASES.get(cleaned.lower())
    if whole is not None:
        return whole
    words = cleaned.split()
    resolved = [_resolve_term(word.lower()) for word in words]
    # Casing is the user's until a word is actually rewritten.
    return " ".join(
        original if resolved_word == original.lower() else resolved_word
        for original, resolved_word in zip(words, resolved)
    )


def _is_page_furniture(value: str) -> bool:
    """True for a list heading or a notebook's own printed labels."""
    if HEADER_RE.match(value) or STATIONERY_RE.match(value):
        return True
    words = re.findall(r"\w+", value)
    # A heading is short. Anything longer is a product line that happens to start
    # with the word.
    if not words or len(words) > 3:
        return False
    return bool(
        difflib.get_close_matches(words[0].casefold(), HEADING_WORDS, n=1, cutoff=0.8)
    )


def _split_parenthetical(value: str) -> tuple[str, str]:
    """Separate a bracketed brand or note from the product itself."""
    notes: list[str] = []

    def capture(match: re.Match[str]) -> str:
        note = match.group(1).strip()
        # "(dozen)" is an amount too, the same way "dozen eggs" is.
        amount = IMPLICIT_SINGLE_RE.sub("1 ", note)
        if QUANTITY_ONLY_RE.match(amount):
            return f" {amount} "
        measurement = MEASUREMENT_IN_NOTE_RE.search(note)
        if measurement:
            rest = (note[: measurement.start()] + note[measurement.end() :]).strip(" -–—,;")
            if rest:
                notes.append(rest)
            return f" {measurement.group(0)} "
        if note:
            notes.append(note)
        return " "

    if ")" in value and "(" not in value:
        value = UNOPENED_BRACKET_RE.sub("(", value, count=1)
    remainder = re.sub(r"\s+", " ", PARENTHETICAL_RE.sub(capture, value)).strip(" ,.-")
    if not remainder and notes:
        # The line was only a bracketed word, so that word is the product.
        return notes[0], ""
    return remainder, ", ".join(notes)


def _parse_item(raw: str, source: str) -> PlannedItem | None:
    # A numbered-list marker needs the trailing space: without it "2.5 kg rice"
    # loses its "2." and becomes five kilos.
    value = re.sub(r"^\s*(?:[-*•]\s*|\d+[.)]\s+)", "", raw).strip()
    value = BUDGET_RE.sub("", value).strip(" ,.-")
    if not value or _is_page_furniture(value):
        return None
    value, context = _split_parenthetical(value)
    if not value:
        return None
    value = IMPLICIT_SINGLE_RE.sub("1 ", value)

    match = TRAILING_QUANTITY_RE.match(value) or LEADING_QUANTITY_RE.match(value)
    quantity = _parse_quantity(match.group("quantity")) if match else None
    if match and quantity:
        unit = _normalise_unit(match.group("unit"), had_quantity=True)
        if unit == "dozen":
            quantity *= 12
            unit = "count"
        name = _clean_name(match.group("name"))
    else:
        quantity = 1
        unit = "item"
        name = _clean_name(value)
    if not name:
        return None
    return PlannedItem(
        search_term=name,
        context=context,
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
