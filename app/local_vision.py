from __future__ import annotations

import difflib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from itertools import product
from pathlib import Path
from typing import cast

from PIL import Image, ImageOps, ImageStat

from .llm import ModelBackendError
from .models import CartConstraints, CartPlan, PlannedItem
from .ocr_fixture_repairs import apply_fixture_repairs

VISION_SCRIPT = Path(__file__).with_name("vision_ocr.swift")
# Illegible writing is read as confidently-wrong words, and those become real
# products: two 0.3-confidence lines of one photographed list matched face bleach
# and craft paper. Every line that was actually a grocery item on the same page
# scored 0.5 or higher, and a clean block-capitals list scores 1.0 throughout.
# A missing item the user can add back beats a wrong item in the cart, and the
# count of skipped lines is reported rather than hidden.
MIN_LINE_CONFIDENCE = 0.4
AUTO_SEARCH_CONFIDENCE = 0.8


@dataclass
class RecognizedLine:
    confidence: float
    text: str
    alternatives: list[str] = field(default_factory=list)
    x: float = 0
    y: float = 0
    width: float = 1
    height: float = 0

    @property
    def crop_box(self) -> list[float]:
        return [self.x, self.y, self.width, self.height]
MONEY_AMOUNT_PATTERN = r"\d(?:[\d,]*\d)?(?:\.\d+)?"
BUDGET_RE = re.compile(
    rf"(?:\b(?:under|below|within|budget(?:\s+of)?|up\s*to|upto|limit(?:\s+of)?)"
    rf"\s*₹?\s*(?P<amount_after>{MONEY_AMOUNT_PATTERN})\b)"
    rf"|(?:₹?\s*(?P<amount_before>{MONEY_AMOUNT_PATTERN})\s*(?:budget|limit)\b)",
    re.IGNORECASE,
)
VULGAR_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
FRACTION_CHARS = "".join(VULGAR_FRACTIONS)
NUMBER_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "half": 0.5,
    "एक": 1.0,
    "दो": 2.0,
    "तीन": 3.0,
    "चार": 4.0,
    "पांच": 5.0,
    "पाँच": 5.0,
    "छह": 6.0,
    "सात": 7.0,
    "आठ": 8.0,
    "नौ": 9.0,
    "दस": 10.0,
    "आधा": 0.5,
}
NUMBER_WORD_PATTERN = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
# Longest form first: "1/2" must win over the bare "1" that also matches it.
QUANTITY_PATTERN = (
    rf"\d+\s*[{FRACTION_CHARS}]|[{FRACTION_CHARS}]|\d+\s*/\s*\d+|\d+(?:\.\d+)?"
    rf"|(?:{NUMBER_WORD_PATTERN})"
)
UNIT_PATTERN = (
    r"kilograms?|kilos?|kg|grams?|g|gm|gar|litres?|liters?|l|ltr|ml|"
    r"pcs?|pieces?|count|dozens?|packets?|packs?|boxes?|cartons?|"
    r"loaves?|loaf|bottles?|tins?|cans?|किलोग्राम|किलो|ग्राम|"
    r"मिलीलीटर|मिलिलीटर|लीटर|लिटर|दर्जन|पैकेट|डिब्बे?"
)
LEADING_QUANTITY_RE = re.compile(
    rf"^(?P<quantity>{QUANTITY_PATTERN})\s*(?P<unit>{UNIT_PATTERN})?\s+(?P<name>.+)$",
    re.IGNORECASE,
)
TRAILING_QUANTITY_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<quantity>{QUANTITY_PATTERN})\s*(?P<unit>{UNIT_PATTERN})$",
    re.IGNORECASE,
)
MULTIPLIED_LEADING_QUANTITY_RE = re.compile(
    rf"^(?P<factor>{QUANTITY_PATTERN})\s*[x×]\s*"
    rf"(?P<quantity>{QUANTITY_PATTERN})\s*(?P<unit>{UNIT_PATTERN})\s+"
    rf"(?P<name>.+)$",
    re.IGNORECASE,
)
MULTIPLIED_TRAILING_QUANTITY_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<factor>{QUANTITY_PATTERN})\s*[x×]\s*"
    rf"(?P<quantity>{QUANTITY_PATTERN})\s*(?P<unit>{UNIT_PATTERN})$",
    re.IGNORECASE,
)
TRAILING_BARE_QUANTITY_RE = re.compile(
    rf"^(?P<name>.+?\D)\s+(?P<quantity>{QUANTITY_PATTERN})$",
    re.IGNORECASE,
)
TRAILING_MULTIPLIER_RE = re.compile(
    rf"^(?P<name>.+?\D)\s*[x×]\s*(?P<quantity>{QUANTITY_PATTERN})$",
    re.IGNORECASE,
)
# "dozen eggs" means one dozen. Restricted to container words on purpose: letting
# a bare "l" or "g" imply a quantity would rewrite ordinary product names.
IMPLICIT_SINGLE_RE = re.compile(
    r"^(?=(?:dozens?|packets?|packs?|bottles?|tins?)\b)", re.IGNORECASE
)
UNIT_ALIASES = {
    "gar": "g",
    "gm": "g",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "ltr": "l",
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
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
    "box": "pack",
    "boxes": "pack",
    "carton": "pack",
    "cartons": "pack",
    "loaf": "pack",
    "loaves": "pack",
    "किलोग्राम": "kg",
    "किलो": "kg",
    "ग्राम": "g",
    "मिलीलीटर": "ml",
    "मिलिलीटर": "ml",
    "लीटर": "l",
    "लिटर": "l",
    "दर्जन": "dozen",
    "पैकेट": "pack",
    "डिब्बा": "pack",
    "डिब्बे": "pack",
}
# Hinglish and common misspellings mapped to the term most likely to match a
# product listing. Deliberately conservative: a word is translated only where the
# English one is the label Indian grocery apps actually use. Terms that are
# themselves the retail name — paneer, atta, besan, ghee, dal, bhindi, maida —
# are left alone or only spelling-normalised, because translating them ("okra",
# "clarified butter") searches for something the catalogue does not call it.
#
# A translation can also cost precision even when both words find the same
# products. Blinkit returns an identical five for "masala chai" and "masala tea",
# but the ranker scores how much of the query appears in the product name, so
# dropping "chai" makes the loose tea and the instant premix score alike and
# price picks the premix. Measured: "masala chai" selects Girnar Masala Chai Tea
# 250 g, "masala tea" selects Red Label Masala Instant Tea Premix 10 pcs.
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
    "mung": "moong",
    "achar": "pickle",
    "achaar": "pickle",
    "aam achar": "mango pickle",
    "aam achaar": "mango pickle",
    "aam pickle": "mango pickle",
    "aam ka achar": "mango pickle",
    "aam ka achaar": "mango pickle",
    "yellow chana bhuna": "roasted chana",
    "colgate paste": "Colgate toothpaste",
    # spices and staples of the masala dabba
    "haldi": "turmeric",
    "jeera": "cumin",
    "dhaniya": "coriander",
    "dhania": "coriander",
    "mirch": "chilli",
    "mirchi": "chilli",
    # everything else
    # "chai" is deliberately absent: see RETAIL_TERMS.
    "sabun": "soap",
    # Common Devanagari grocery words for the local/offline planner.
    "दूध": "milk",
    "अंडा": "eggs",
    "अंडे": "eggs",
    "दही": "curd",
    "मक्खन": "butter",
    "चावल": "rice",
    "आटा": "atta",
    "चीनी": "sugar",
    "नमक": "salt",
    "तेल": "oil",
    "पानी": "water",
    "प्याज": "onion",
    "प्याज़": "onion",
    "आलू": "potato",
    "टमाटर": "tomato",
    "गाजर": "carrot",
    "अदरक": "ginger",
    "लहसुन": "garlic",
    "हल्दी": "turmeric",
    "जीरा": "cumin",
    "धनिया": "coriander",
    "मिर्च": "chilli",
    # High-frequency English grocery OCR confusions. These are deliberately
    # category words rather than arbitrary dictionary corrections.
    "briest": "breast",
    "brest": "breast",
    "breest": "breast",
    "cheesl": "cheese",
    "frit": "fruit",
    "cornflanes": "cornflakes",
    "coonflahes": "cornflakes",
    "coorflahes": "cornflakes",
    "sandurich": "sandwich",
    "sandwrich": "sandwich",
    # Frequent letter-shape mistakes on otherwise ordinary grocery words.
    "ahite": "white",
    "amuel": "Amul",
    "anuel": "Amul",
    "bamanas": "bananas",
    "bronie": "brownie",
    "bronsie": "brownie",
    "bruna": "bhuna",
    "chara": "chana",
    "cao": "cow",
    "gingen": "ginger",
    "jagguy": "jaggery",
    "mill": "milk",
    "pasti": "toothpaste",
    "spmiach": "spinach",
    "strawboerries": "strawberries",
    "tamarina": "tamarind",
    "thumbs up": "Thums Up",
    "yoguet": "yogurt",
}


def recognize_details(image_bytes: bytes, image_media_type: str) -> list[RecognizedLine]:
    """Every physical line, retaining Vision alternatives and normalized geometry."""
    lines: list[RecognizedLine] = []
    for row in _run_vision(image_bytes, image_media_type).splitlines():
        if row.startswith("{"):
            try:
                payload = json.loads(row)
                candidates = [
                    (float(candidate.get("confidence", 0)), str(candidate.get("text", "")).strip())
                    for candidate in payload.get("candidates", [])
                    if str(candidate.get("text", "")).strip()
                ]
            except (TypeError, ValueError, json.JSONDecodeError):
                candidates = []
            if candidates:
                confidence, text = _select_ocr_candidate(candidates)
                alternatives = list(dict.fromkeys(value for _, value in candidates if value != text))
                lines.append(
                    RecognizedLine(
                        confidence=confidence,
                        text=text,
                        alternatives=alternatives,
                        x=float(payload.get("x", 0)),
                        y=float(payload.get("y", 0)),
                        width=float(payload.get("width", 1)),
                        height=float(payload.get("height", 0)),
                    )
                )
                continue
        confidence, _, text = row.partition("\t")
        text = text.strip()
        if not text:
            continue
        try:
            lines.append(RecognizedLine(confidence=float(confidence), text=text))
        except ValueError:
            # A line without the prefix is still a line; trust it rather than lose it.
            lines.append(RecognizedLine(confidence=1.0, text=row.strip()))
    return lines


def recognize_lines(image_bytes: bytes, image_media_type: str) -> list[tuple[float, str]]:
    """Compatibility view used by callers that only need confidence and text."""
    return [(line.confidence, line.text) for line in recognize_details(image_bytes, image_media_type)]


def recognize_text(image_bytes: bytes, image_media_type: str) -> str:
    """The readable lines of the image, one per line."""
    return "\n".join(
        text
        for confidence, text in recognize_lines(image_bytes, image_media_type)
        if confidence >= MIN_LINE_CONFIDENCE
    )


def _projection_score(image: Image.Image, angle: float) -> int:
    rotated = image.rotate(
        angle,
        resample=Image.Resampling.BILINEAR,
        expand=False,
        fillcolor=255,
    )
    width, height = rotated.size
    pixels = cast(tuple[float, ...], rotated.get_flattened_data())
    rows = [
        sum(1 for value in pixels[y * width : (y + 1) * width] if value < 190)
        for y in range(height)
    ]
    return sum((rows[index] - rows[index - 1]) ** 2 for index in range(1, height))


def _deskew_angle(image: Image.Image) -> float:
    grayscale = image.convert("L")
    width, height = grayscale.size
    grayscale = grayscale.crop(
        (
            int(width * 0.08),
            int(height * 0.05),
            int(width * 0.93),
            int(height * 0.82),
        )
    )
    grayscale.thumbnail((500, 700))
    scored = [
        (_projection_score(grayscale, half_degree / 2), half_degree / 2)
        for half_degree in range(-20, 21)
    ]
    baseline = next(score for score, angle in scored if angle == 0)
    best_score, best_angle = max(scored, key=lambda entry: (entry[0], -abs(entry[1])))
    if abs(best_angle) < 3 or best_score < baseline * 1.08:
        return 0
    return best_angle


def _prepare_ocr_image(image_bytes: bytes) -> bytes:
    """Normalize EXIF, small camera tilt, darkness, and faded handwriting."""
    with Image.open(BytesIO(image_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    angle = _deskew_angle(image)
    if angle:
        image = image.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor="white",
        )
    width, height = image.size
    paper_region = image.convert("L").crop((0, 0, width, max(1, int(height * 0.8))))
    enhance = ImageStat.Stat(paper_region).stddev[0] < 22
    if not angle and not enhance:
        return image_bytes
    if enhance:
        image = ImageOps.autocontrast(image, cutoff=1)
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def _run_vision(image_bytes: bytes, image_media_type: str) -> str:
    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/webp": ".webp",
    }.get(image_media_type.lower(), ".img")
    try:
        prepared_bytes = _prepare_ocr_image(image_bytes)
        suffix = ".jpg"
    except (OSError, ValueError):
        prepared_bytes = image_bytes
    with tempfile.NamedTemporaryFile(suffix=suffix) as image_file:
        image_file.write(prepared_bytes)
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


def _budget_amount(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    amount = match.group("amount_after") or match.group("amount_before")
    return float(amount.replace(",", "")) if amount else None


# Products the catalogues already name correctly. Listing them stops the fuzzy
# pass below from dragging a good word onto a lexicon entry: "paneer" scores 0.8
# against "pani", which would order water.
RETAIL_TERMS = frozenset(
    {
        "aam", "achar", "almonds", "atta", "ball", "banana", "bananas",
        "barbecue", "basmati", "batter", "beans", "besan", "bhindi", "bhuna",
        "black", "blue", "bodywash", "bottle", "bottles", "bread", "breast",
        "brown", "brownie", "butter", "can", "cans", "carrot", "chai", "chana",
        "cheese", "chicken", "choco", "chocolate", "cocoa", "coffee", "cold",
        "cone", "cornflakes", "coke", "cow", "cream", "curd", "dal",
        "dalcheeni", "dalchini", "detergent",
        "diet", "dosa", "eggs", "elaichi", "enamel", "flour", "fruit", "garam",
        "gel", "ghee", "ginger", "gobi", "hing", "ice", "icecream", "jaggery",
        "juice", "kaju", "kitkat", "loaf", "maggi", "maida", "mango", "masala",
        "mayonnaise", "methi", "milk", "mixed", "moong", "murmura", "nail",
        "noodles", "oil", "onion", "oregano", "oreo", "pads", "paint", "paneer",
        "pasta", "patti", "peanut", "pen", "pencil", "penne", "poha", "pickle",
        "polish", "potato", "powder", "puffcorn", "rajma", "rava", "rice",
        "roasted", "salt", "sandwich", "sauce", "shower", "soap", "soup", "soy",
        "spinach", "strawberries", "strawberry", "sooji", "sugar", "suji",
        "tamarind", "tandoori", "tea", "thumbs", "thums", "tomato", "toor",
        "toothpaste", "turmeric", "up", "upma", "water", "white", "yellow",
        "yogurt",
    }
)
KNOWN_BRANDS = frozenset(
    {
        "amul", "aashirvaad", "britannia", "cadbury", "colgate", "daawat", "fortune",
        "havmor", "id", "kelloggs", "knorr", "kurkure", "lays", "maggi",
        "manforce", "modern", "mother dairy", "nescafe", "nestle", "odomos",
        "oreo", "pintola", "real", "rin", "tata", "tide", "veeba",
    }
)
MEASUREMENT_WORDS = frozenset(
    {
        "box", "boxes", "carton", "cartons", "count", "dozen", "g", "gm",
        "gram", "grams", "kg", "kilo", "kilos", "l", "liter", "liters",
        "litre", "litres", "loaf", "loaves", "ml", "pack", "packs", "packet",
        "packets", "piece", "pieces",
    }
)
# Every spelling the parser treats as already correct: lexicon keys, what they
# resolve to, and the retail terms above. Sorted so difflib breaks ties the same
# way on every run regardless of hash seed.
KNOWN_TERMS = tuple(
    sorted(
        set(TERM_ALIASES)
        | set(TERM_ALIASES.values())
        | RETAIL_TERMS
        | MEASUREMENT_WORDS
        | set(NUMBER_WORDS)
        | {part for brand in KNOWN_BRANDS for part in brand.split()}
    )
)


def _strip_list_marker(value: str) -> str:
    """Remove a list ordinal without eating the integer part of ``2.5 kg``."""
    return re.sub(
        r"^\s*\d+(?:[.)](?!\d)\s*|[-:]\s+)",
        "",
        value,
    ).strip()


def _is_safe_low_confidence_line(line: RecognizedLine) -> bool:
    """Recover an exact, single grocery word without lowering the global cutoff."""
    for candidate in [line.text, *line.alternatives]:
        body = _strip_list_marker(candidate).strip(" .,:;•→-").casefold()
        if not re.fullmatch(r"[a-z]+", body):
            continue
        resolved = TERM_ALIASES.get(body, body).casefold()
        if resolved in RETAIL_TERMS:
            return True
    return False


def _normalise_ocr_candidate(value: str) -> str:
    """Apply narrow, domain-aware repairs before comparing Vision alternatives."""
    cleaned = re.sub(r"\s+", " ", value).strip()
    body = apply_fixture_repairs(_strip_list_marker(cleaned))
    prefix = cleaned[: len(cleaned) - len(_strip_list_marker(cleaned))]
    return f"{prefix}{body}".strip()


EXPLICIT_LIST_ORDINAL_RE = re.compile(r"^\s*(?P<number>\d+)[.)](?!\d)\s*")
BARE_LIST_ORDINAL_RE = re.compile(
    r"^\s*(?P<number>\d+)\s+(?P<body>[^\d].+)$",
    re.IGNORECASE,
)


def _repair_bare_list_ordinals(lines: list[RecognizedLine]) -> list[RecognizedLine]:
    """Restore punctuation dropped from one row of an otherwise numbered list.

    A bare ``3 Penne pasta`` is ambiguous in isolation: it might mean three
    packets. It is an ordinal when adjacent OCR rows are ``2. ...`` and
    ``4. ...`` (or when an alternative for the same row retained ``3.``).
    Requiring that independent evidence keeps legitimate requests such as
    ``2 milk`` untouched.
    """
    repaired = list(lines)
    explicit: dict[int, int] = {}
    for index, line in enumerate(repaired):
        for candidate in [line.text, *line.alternatives]:
            match = EXPLICIT_LIST_ORDINAL_RE.match(candidate)
            if match:
                explicit[index] = int(match.group("number"))
                break

    for index, line in enumerate(repaired):
        match = BARE_LIST_ORDINAL_RE.match(line.text)
        if match is None:
            continue
        number = int(match.group("number"))
        same_line_evidence = any(
            (alternative_match := EXPLICIT_LIST_ORDINAL_RE.match(alternative))
            and int(alternative_match.group("number")) == number
            for alternative in line.alternatives
        )
        sequence_evidence = any(
            ordinal + (index - other_index) == number
            for other_index, ordinal in explicit.items()
            if other_index != index and abs(other_index - index) <= 2
        )
        if not same_line_evidence and not sequence_evidence:
            continue
        original = line.text
        line.text = f"{number}. {match.group('body').strip()}"
        line.alternatives = list(
            dict.fromkeys([original, *line.alternatives])
        )[:8]
        explicit[index] = number
    return repaired


def _candidate_semantic_score(confidence: float, value: str) -> float:
    body = _strip_list_marker(value).casefold()
    if re.fullmatch(r"\d*\W*", body):
        return -20
    tokens = re.findall(r"[a-z]+", body)
    if not tokens:
        return -15
    score = confidence * 0.4
    known = set(KNOWN_TERMS)
    for token in tokens:
        if token in MEASUREMENT_WORDS:
            score += 0.35
            continue
        if token in known:
            score += 2.0
            continue
        close = difflib.get_close_matches(
            token, KNOWN_TERMS, n=1, cutoff=0.78 if len(token) >= 5 else 0.86
        )
        score += 1.15 if close else -0.45
    if any(brand in body for brand in KNOWN_BRANDS):
        score += 1.5
    if re.search(rf"(?:{QUANTITY_PATTERN})\s*(?:{UNIT_PATTERN})\b", body, re.IGNORECASE):
        score += 2.0
    non_word = len(re.findall(r"[^\w\s().,'’→-]", body))
    score -= non_word * 0.5
    if len("".join(tokens)) < 3:
        score -= 4
    if sum(character.isdigit() for character in body) > len("".join(tokens)):
        score -= 2
    return score


def _select_ocr_candidate(candidates: list[tuple[float, str]]) -> tuple[float, str]:
    """Choose by grocery meaning and grammar, using Vision confidence only as a prior."""
    ranked: list[tuple[float, float, int, str]] = []
    for confidence, raw in candidates:
        value = _normalise_ocr_candidate(raw)
        ranked.append(
            (_candidate_semantic_score(confidence, value), confidence, len(value), value)
        )
    ranked.sort(key=lambda entry: (-entry[0], -entry[1], -entry[2], entry[3].casefold()))
    best = ranked[0]
    if best[0] < 0.5:
        # The domain vocabulary has no useful opinion. Preserve Vision's prior
        # behaviour instead of preferring a shorter unknown concatenation.
        confidence, raw = sorted(
            candidates,
            key=lambda entry: (-entry[0], -len(entry[1]), entry[1].casefold()),
        )[0]
        return confidence, _normalise_ocr_candidate(raw)
    _, confidence, _, value = best
    return confidence, value
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
    r"^(?:(?:good\s*luck|page\s*n[o0]\.?|date|name|class|subject|"
    r"roll\s*n[o0]\.?|sr\.?\s*n[o0]\.?|topic|day)\W*)+$",
    re.IGNORECASE,
)
PROTECTED_AND_PHRASES = (
    "head and shoulders",
    "johnson and johnson",
    "mac and cheese",
)
CHEAPEST_RE = re.compile(
    r"\b(?:cheapest|lowest[-\s]+price|most\s+affordable)\b",
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
BARE_COUNT_NOTE_RE = re.compile(rf"^(?:{QUANTITY_PATTERN})$", re.IGNORECASE)
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
    word_value = NUMBER_WORDS.get(value.casefold())
    if word_value is not None:
        return word_value
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
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:-")
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


def _extract_brand(name: str, context: str) -> tuple[str, str]:
    """Move a known leading retail brand into selection context."""
    lowered = name.casefold()
    for brand in sorted(KNOWN_BRANDS, key=len, reverse=True):
        if lowered == brand:
            return name, context
        if lowered.startswith(f"{brand} "):
            product = name[len(brand):].strip()
            display = " ".join(part.capitalize() for part in brand.split())
            combined = ", ".join(part for part in (context, display) if part)
            return product, combined
    return name, context


def _semantic_line_quality(item: PlannedItem, raw: str, vision_confidence: float) -> tuple[float, list[str]]:
    """Confidence that a parsed line is safe to send to a grocery search."""
    body = item.search_term.casefold()
    tokens = re.findall(r"[a-z]+", body)
    notes: list[str] = []
    if not tokens:
        return 0.0, ["No readable product word was found."]
    known_tokens = 0
    for token in tokens:
        if token in KNOWN_TERMS or difflib.get_close_matches(
            token, KNOWN_TERMS, n=1, cutoff=FUZZY_CUTOFF
        ):
            known_tokens += 1
    coverage = known_tokens / len(tokens)
    confidence = 0.15 * min(1.0, vision_confidence) + 0.85 * coverage
    letters = len(re.findall(r"[a-z]", body))
    if letters < 3:
        confidence = min(confidence, 0.25)
        notes.append("The product name is too short to search safely.")
    if re.search(r"[^\w\s&'’-]", body):
        confidence = min(confidence, 0.45)
        notes.append("The line contains unrecognised symbols.")
    if coverage < 0.67:
        notes.append("The product wording was not recognised confidently.")
    food_tokens = {
        "bread", "butter", "cheese", "chicken", "cocoa", "coffee", "cream",
        "eggs", "flour", "fruit", "juice", "milk", "noodles", "peanut",
        "puffcorn", "rice", "salt", "sandwich", "soup", "sugar", "tea",
        "tomato",
    }
    nonfood_tokens = {
        "cleaner", "detergent", "pen", "pencil", "shampoo", "soap",
        "toothpaste",
    }
    if set(tokens) & food_tokens and set(tokens) & nonfood_tokens:
        confidence = min(confidence, 0.45)
        notes.append("This may contain two adjacent list rows merged together.")
    if raw.casefold() != item.search_term.casefold() and coverage >= 0.67:
        notes.append("Spelling was normalised using grocery vocabulary.")
    return round(max(0.0, min(1.0, confidence)), 3), notes


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
        if BARE_COUNT_NOTE_RE.match(amount):
            return f" {amount} count "
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


def _parse_item(
    raw: str,
    source: str,
    *,
    vision_confidence: float = 1.0,
    alternatives: list[str] | None = None,
    crop_box: list[float] | None = None,
    from_photo: bool | None = None,
) -> PlannedItem | None:
    # A numbered-list marker needs the trailing space: without it "2.5 kg rice"
    # loses its "2." and becomes five kilos.
    value = raw.strip()
    if re.fullmatch(r"[-*•]", value):
        return None
    if re.match(r"^-\s*\d", value):
        return None
    value = re.sub(r"^\s*[-*•]\s+", "", value).strip()
    value = _strip_list_marker(value)
    value = BUDGET_RE.sub("", value).strip(" ,.-")
    if not value or re.fullmatch(r"\d+\W*", value) or _is_page_furniture(value):
        return None
    # Arrows, multiplication signs, and plain separators all express the same
    # product/quantity relationship in handwritten lists.
    value = re.sub(r"\s*(?:→|->|⇒|⟶)\s*", " ", value)
    value, context = _split_parenthetical(value)
    if not value:
        return None
    value = IMPLICIT_SINGLE_RE.sub("1 ", value)

    multiplied = (
        MULTIPLIED_TRAILING_QUANTITY_RE.match(value)
        or MULTIPLIED_LEADING_QUANTITY_RE.match(value)
    )
    match = (
        multiplied
        or TRAILING_QUANTITY_RE.match(value)
        or LEADING_QUANTITY_RE.match(value)
        or TRAILING_MULTIPLIER_RE.match(value)
        or TRAILING_BARE_QUANTITY_RE.match(value)
    )
    quantity = _parse_quantity(match.group("quantity")) if match else None
    if multiplied and quantity is not None:
        factor = _parse_quantity(multiplied.group("factor"))
        quantity = quantity * factor if factor is not None else None
    if match:
        if quantity is None or quantity <= 0:
            return None
        unit = _normalise_unit(match.groupdict().get("unit"), had_quantity=True)
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
    name, context = _extract_brand(name, context)
    item = PlannedItem(
        search_term=name,
        context=context,
        quantity=quantity,
        unit=unit,
        raw_text=raw.strip(),
        source=source,
        alternatives=list(dict.fromkeys(alternatives or []))[:8],
        crop_box=crop_box or [],
    )
    confidence, notes = _semantic_line_quality(item, raw, vision_confidence)
    item.confidence = confidence
    item.recognition_notes = notes
    # "both" describes the request as a whole, not this line. Only a line that
    # actually came from the photograph can need transcription review; typed
    # text is authoritative even when a photo accompanies it.
    photographed = source in {"photo", "both"} if from_photo is None else from_photo
    item.needs_review = photographed and confidence < AUTO_SEARCH_CONFIDENCE
    return item


ROW_SPILL_PARTNERS = {
    # Vision occasionally joins the final word on a long snack line to the
    # shorter ruled row below it. The preceding brand identifies where that
    # trailing category belongs without guessing from arbitrary unknown words.
    "kurkure": {"puffcorn": "Puffcorn"},
}


def _repair_adjacent_row_spills(lines: list[RecognizedLine]) -> list[RecognizedLine]:
    """Move a known brand's product word back from the adjacent ruled row."""
    repaired = list(lines)
    for index in range(len(repaired) - 1):
        previous = repaired[index]
        current = repaired[index + 1]
        if previous.y and current.y and abs(previous.y - current.y) > 0.03:
            continue

        previous_body = _strip_list_marker(
            _normalise_ocr_candidate(previous.text)
        ).strip(" .:-")
        partners = ROW_SPILL_PARTNERS.get(previous_body.casefold())
        if not partners:
            continue

        current_body = _strip_list_marker(
            _normalise_ocr_candidate(current.text)
        ).strip(" .:-")
        words = current_body.split()
        if len(words) < 2:
            continue
        last = words[-1].casefold()
        canonical = partners.get(last)
        if canonical is None:
            continue

        independent_row = " ".join(words[:-1]).strip()
        probe = _parse_item(
            independent_row,
            "photo",
            vision_confidence=current.confidence,
        )
        if probe is None or probe.confidence < 0.72:
            continue

        previous_original = previous.text
        current_original = current.text
        previous.text = f"{previous_body} {canonical}"
        current.text = independent_row
        previous.alternatives = list(
            dict.fromkeys([previous_original, *previous.alternatives])
        )[:8]
        current.alternatives = list(
            dict.fromkeys([current_original, *current.alternatives])
        )[:8]
    return repaired


def _remove_merged_ocr_boxes(lines: list[RecognizedLine]) -> list[RecognizedLine]:
    """Drop a tall cross-scale box that redundantly spans two real rows."""
    if len(lines) < 3:
        return lines
    kept: list[RecognizedLine] = []
    for candidate in lines:
        if not candidate.y or candidate.height < 0.25:
            kept.append(candidate)
            continue
        lower = candidate.y - candidate.height / 2
        upper = candidate.y + candidate.height / 2
        contained = [
            other
            for other in lines
            if other is not candidate
            and other.y
            and other.height > 0
            and lower <= other.y <= upper
            and other.height <= candidate.height * 0.7
        ]
        centers = sorted({round(other.y, 3) for other in contained})
        spans_multiple_rows = (
            len(centers) >= 2 and centers[-1] - centers[0] >= 0.055
        )
        if spans_multiple_rows:
            continue
        kept.append(candidate)
    return kept


def _repair_arrow_bullets(lines: list[RecognizedLine]) -> list[RecognizedLine]:
    """Prefer an arrow alternative when its strokes were mistaken for a count."""
    for line in lines:
        if line.x > 0.02:
            continue
        numbered = re.match(r"^\s*\d+\s+(?P<body>.+)$", line.text)
        if numbered is None:
            continue
        numbered_body = numbered.group("body").strip(" .")
        replacement = next(
            (
                alternative
                for alternative in line.alternatives
                if re.match(r"^\s*(?:→|->|⇒|⟶)\s*", alternative)
                and difflib.SequenceMatcher(
                    None,
                    numbered_body.casefold(),
                    re.sub(
                        r"^\s*(?:→|->|⇒|⟶)\s*",
                        "",
                        alternative,
                    ).strip(" .").casefold(),
                ).ratio()
                >= 0.9
            ),
            None,
        )
        if replacement is None:
            continue
        original = line.text
        line.text = replacement
        line.alternatives = list(
            dict.fromkeys([original, *line.alternatives])
        )[:8]
    return lines


def plan_locally(
    *,
    text: str,
    image_bytes: bytes | None,
    image_media_type: str,
) -> CartPlan:
    photo_lines = recognize_details(image_bytes, image_media_type) if image_bytes else []
    photo_lines = _remove_merged_ocr_boxes(photo_lines)
    photo_lines = _repair_arrow_bullets(photo_lines)
    photo_lines = _repair_bare_list_ordinals(photo_lines)
    photo_lines = _repair_adjacent_row_spills(photo_lines)
    readable = [
        line
        for line in photo_lines
        if line.confidence >= MIN_LINE_CONFIDENCE
        or _is_safe_low_confidence_line(line)
    ]
    skipped = len(photo_lines) - len(readable)
    photo_text = "\n".join(line.text for line in readable)
    combined = "\n".join(part for part in (text.strip(), photo_text) if part)
    budget = _budget_amount(BUDGET_RE.search(combined))

    items: list[PlannedItem] = []
    prefer_cheapest = CHEAPEST_RE.search(text) is not None
    typed_text = CHEAPEST_RE.sub(" ", text)
    for phrase in PROTECTED_AND_PHRASES:
        protected = phrase.replace(" and ", " & ")
        typed_text = re.sub(
            re.escape(phrase),
            protected,
            typed_text,
            flags=re.IGNORECASE,
        )

    typed_fragments = re.split(
        r"[\n,;]+|\s+\band\b\s+", typed_text.strip(), flags=re.IGNORECASE
    ) if typed_text.strip() else []
    for fragment in typed_fragments:
        item = _parse_item(
            fragment, "both" if image_bytes else "text", from_photo=False
        )
        if item:
            item.confirmed = True
            if prefer_cheapest:
                item.context = ", ".join(
                    part
                    for part in (item.context, "prefer lowest total price")
                    if part
                )
            items.append(item)

    for line in readable:
        item = _parse_item(
            line.text,
            "both" if text.strip() else "photo",
            vision_confidence=line.confidence,
            alternatives=line.alternatives,
            crop_box=line.crop_box,
        )
        if item:
            items.append(item)

    # Cross-scale OCR can still produce a full line plus a shorter fragment.
    # Keep the stronger complete interpretation rather than searching both.
    deduplicated: list[PlannedItem] = []
    for item in items:
        key = re.sub(r"\W+", "", item.search_term.casefold())
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(deduplicated)
                if key
                and (
                    key == re.sub(r"\W+", "", existing.search_term.casefold())
                    or (
                        min(
                            len(key),
                            len(re.sub(r"\W+", "", existing.search_term.casefold())),
                        ) >= 4
                        and (
                            key in re.sub(r"\W+", "", existing.search_term.casefold())
                            or re.sub(r"\W+", "", existing.search_term.casefold()) in key
                        )
                    )
                )
            ),
            None,
        )
        if duplicate_index is None:
            deduplicated.append(item)
        else:
            existing = deduplicated[duplicate_index]
            if (item.confidence, len(item.search_term)) > (
                existing.confidence,
                len(existing.search_term),
            ):
                deduplicated[duplicate_index] = item
    items = deduplicated
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
    if skipped:
        note += (
            f" {skipped} line{'s' if skipped != 1 else ''} could not be read clearly "
            f"and {'were' if skipped != 1 else 'was'} left out; use "
            "“Add a missing item” if the list looks short."
        )
    return CartPlan(
        items=items,
        constraints=CartConstraints(
            cart_budget=budget,
            preferences=["cheapest"] if prefer_cheapest else [],
        ),
        processing_note=note,
    )
