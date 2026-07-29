import json
from pathlib import Path

import pytest

from app.models import PlannedItem
from tools.evaluate_handwriting import evaluate_manifest, score_case


def test_evaluator_scores_structure_review_and_unsafe_false_accepts():
    expected = [
        {
            "raw_text": "Milk 2 L",
            "search_term": "milk",
            "context": "",
            "quantity": 2,
            "unit": "l",
        }
    ]
    predicted = [
        PlannedItem(
            search_term="milk",
            raw_text="Milk 2 L",
            quantity=2,
            unit="l",
            confirmed=True,
        ),
        PlannedItem(
            search_term="bleach",
            raw_text="illegible",
            confirmed=True,
        ),
    ]

    scores = score_case(expected, predicted)

    assert scores["exact_items"] == 1
    assert scores["exact_lines"] == 1
    assert scores["structured_fields_correct"] == 4
    assert scores["unsafe_false_accepts"] == 1


def test_evaluator_refuses_tuned_project_fixtures(tmp_path):
    manifest = tmp_path / "manifest.json"
    fixture = Path(__file__).parent / "fixtures" / "grocery_list_brands.jpeg"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "not-held-out",
                        "image": str(fixture),
                        "tuned_against": False,
                        "expected": [],
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="tuned project fixture"):
        evaluate_manifest(manifest)
