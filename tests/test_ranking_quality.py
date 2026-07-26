"""Guard the matcher's hit rate against a fixed snapshot of real results.

Every other matcher test states a rule. This one states an outcome: given the
searches three photographed lists actually produced, how often does the ranker
choose the product the list asked for. It exists because ranking changes are
easy to argue about and hard to judge without a number.

Refresh the snapshot with `python tools/ranking_corpus.py refresh`, which needs
a connected provider. Nothing here touches the network.
"""

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.matcher import match_product
from app.models import PlannedItem, Product


CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "ranking_corpus.json").read_text(
        encoding="utf-8"
    )
)
CASES = CORPUS["cases"]
# 15 of 19 before the provider's result order was scored, 17 after. Raise this
# when a change earns it; a drop means a change cost more than it gained.
MINIMUM_CORRECT = 17


def chosen_product(case: dict) -> str:
    products = [Product.model_validate(entry) for entry in case["products"]]
    item = PlannedItem(
        search_term=case["query"],
        context=case["context"],
        quantity=case["quantity"],
        unit=case["unit"],
    )
    decision = match_product(item, products, Settings(safety_lock=True))
    pick = next((p for p in products if p.id == decision.product_id), None)
    return pick.name if pick else ""


def test_the_matcher_still_finds_what_the_lists_asked_for():
    correct = [case for case in CASES if any(
        word in chosen_product(case).lower() for word in case["expect"]
    )]
    missed = [case["query"] for case in CASES if case not in correct]

    assert len(correct) >= MINIMUM_CORRECT, (
        f"{len(correct)}/{len(CASES)} correct, below {MINIMUM_CORRECT}. Missed: {missed}"
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # Both were chosen on price alone until the provider's own ordering was
        # scored, and both are the kind of miss a user notices immediately.
        ("Thumbs u", "thums up"),
        ("Modern nhite bread", "modern white"),
    ],
)
def test_named_regressions_stay_fixed(query, expected):
    case = next(entry for entry in CASES if entry["query"] == query)

    assert expected in chosen_product(case).lower()
