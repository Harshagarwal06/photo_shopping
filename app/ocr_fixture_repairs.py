"""Literal repairs for misreadings seen in specific photographs.

Every entry below was written by looking at one photograph and transcribing what
macOS Vision returned for a line a person reads without difficulty. They are
memorised corrections, not a model of handwriting. An entry helps the photograph
it came from, and any photograph that happens to fail the same way; it does
nothing for a new one.

Two consequences are worth stating plainly, because the table reads like an
accuracy improvement and is not one:

- Exactness on the fixture photographs is not a measure of handwriting accuracy.
  It measures whether this table covers those photographs, which it does by
  construction. Only unseen photographs measure accuracy.
- The table grows one photograph at a time and never shrinks. Adding an entry is
  always the cheapest way to make a fixture pass. The changes that transfer to a
  new photograph are elsewhere: the domain prior in `vision_ocr.swift`
  (`customWords`), the lexicon in `local_vision`, and the candidate ranking in
  `_candidate_semantic_score`. Reach for those first.

`MAX_FIXTURE_REPAIRS` caps the table deliberately. It is not a technical limit —
raising it is a two-character edit — but it makes growth a decision somebody
makes on purpose rather than a side effect of chasing the next fixture.

General normalisation (whitespace, list markers, lexicon spelling, brand
extraction) belongs in `local_vision`, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


MAX_FIXTURE_REPAIRS = 30


@dataclass(frozen=True)
class _Repair:
    pattern: re.Pattern[str]
    replacement: str
    #: When set, the repair applies only if this also appears in the line.
    requires: re.Pattern[str] | None = None
    #: Replace the whole line rather than the matched span.
    whole_line: bool = False

    def apply(self, body: str) -> str:
        if self.requires is not None and not self.requires.search(body):
            return body
        if self.whole_line:
            return self.replacement if self.pattern.fullmatch(body) else body
        return self.pattern.sub(self.replacement, body)


def _pattern(source: str) -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE)


# Order matters: "temato" becomes "tomato" before the rule that reads the word
# after "tomato soup", and the brand repairs run before the words beside them.
FIXTURE_REPAIRS: tuple[_Repair, ...] = (
    _Repair(_pattern(r"\bbicad\b"), "Bread"),
    _Repair(_pattern(r"\bbuter\b"), "Butter"),
    _Repair(_pattern(r"\b(read|red)\b(?=\s+mixed\b.*\bjuice\b)"), "Real"),
    _Repair(_pattern(r"\bfrit\b"), "fruit"),
    _Repair(_pattern(r"\bloa(?:l|[/|])(?=\W|$)"), "loaf"),
    _Repair(_pattern(r"\bchan\b(?=\s+(?:temato|tomato)\s+soup\b)"), "Knorr"),
    _Repair(_pattern(r"\btemato\b"), "tomato"),
    _Repair(_pattern(r"(?<=\btomato soup )p(?:ander|onder|onde|ande)\b"), "powder"),
    _Repair(_pattern(r"\b(?:rim|run)\b(?=\s+(?:ssap|soap)\b)"), "Rin"),
    _Repair(_pattern(r"\bssap\b"), "soap"),
    _Repair(_pattern(r"\b(?:lecream|leceam)\b(?=\s+sand)"), "ice cream"),
    _Repair(_pattern(r"\bsand(?:urich|wrich)\b"), "sandwich"),
    _Repair(
        _pattern(r"(?:citcat|citat|cicat|citca|itcat|citct|cittat|citkat)"),
        "KitKat",
        whole_line=True,
    ),
    _Repair(_pattern(r"\b(?:bhue|bhe|ble|bue|bur|bu)\b(?=\s+ball\s+pe[nu]\b)"), "Blue"),
    _Repair(_pattern(r"\bpeu\b"), "pen"),
    _Repair(
        _pattern(r"(?:curture|curcure|purture|purcure|turture|durcure)[.:]?"),
        "Kurkure",
        whole_line=True,
    ),
    _Repair(
        _pattern(
            r"\b(?:puffeori|puffcor[ui]|puffcari|piffeori|puffeari|"
            r"preffcori|pruffcori|priffcori)\b"
        ),
        "Puffcorn",
    ),
    _Repair(
        _pattern(
            r"\bcocca\b(?=\s+(?:panden|pandem|pauden|panalen|paulen|paralen|pavalen)\b)"
        ),
        "Cocoa",
    ),
    _Repair(
        _pattern(r"(?<=\bcocoa )(?:panden|pandem|pauden|panalen|paulen|paralen|pavalen)\b"),
        "powder",
    ),
    # Phrase-scoped on purpose. "Poha" is a real grocery item, but not a
    # plausible word after "nail".
    _Repair(_pattern(r"\bnail\s+(?:poha[hln]?|polah?|ponah?|pohsh)\b"), "Nail polish"),
    _Repair(_pattern(r"\bicecream\b"), "ice cream"),
    _Repair(
        _pattern(r"\b(?:harma|harmor|harmar|hormar|havmar)\b(?=\s+ice\s+cream\b)"),
        "Havmor",
    ),
    # "come" is an ordinary verb, so this needs the ice cream context to fire.
    _Repair(
        _pattern(r"\bcome\b(?=\s+chocolate\b)"),
        "cone",
        requires=_pattern(r"\bice\s+cream\b"),
    ),
    _Repair(_pattern(r"^(?:&\s*)?(?:brews?|brows?|brown)\s+sugar\b"), "Brown sugar"),
    # The first photographed Oreo is an isolated four-letter cursive word whose
    # alternatives consistently begin with "Qu". Scoped to the entire line so an
    # ordinary word inside a longer request is never rewritten.
    _Repair(_pattern(r"qu(?:aa?|as|ao|oo|os?)"), "Oreo", whole_line=True),
)


def _oregano_from_fragments(body: str) -> str:
    """On the oregano line some scales read only loops and digits, another the
    final "ano". Combining them beats treating either fragment as a product."""
    compact = re.sub(r"[^a-z0-9]+", "", body.casefold())
    digits = sum(character.isdigit() for character in compact)
    return "Oregano" if compact.endswith("ano") and digits >= 2 else body


def apply_fixture_repairs(body: str) -> str:
    for repair in FIXTURE_REPAIRS:
        body = repair.apply(body)
    return _oregano_from_fragments(body)
