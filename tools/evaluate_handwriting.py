"""Evaluate handwriting recognition on photographs that were never tuned against.

Usage:
    python tools/evaluate_handwriting.py held-out-manifest.json
    python tools/evaluate_handwriting.py held-out-manifest.json --output report.json

The manifest must explicitly mark every case ``"tuned_against": false``. Images
under ``tests/fixtures`` are rejected because the project contains literal
repairs derived from those photographs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.local_vision import plan_locally
from app.models import PlannedItem

ROOT = Path(__file__).resolve().parent.parent
TUNED_FIXTURE_DIR = (ROOT / "tests" / "fixtures").resolve()


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def score_case(
    expected: list[dict[str, Any]],
    predicted: list[PlannedItem],
) -> dict[str, int]:
    unmatched = set(range(len(predicted)))
    exact_items = 0
    exact_lines = 0
    fields_correct = 0
    fields_total = 0
    matched_predictions: set[int] = set()

    for wanted in expected:
        wanted_term = normalized(wanted.get("search_term"))
        match_index = next(
            (
                index
                for index in unmatched
                if normalized(predicted[index].search_term) == wanted_term
            ),
            None,
        )
        fields = [
            field
            for field in ("search_term", "context", "quantity", "unit")
            if field in wanted
        ]
        fields_total += len(fields)
        if match_index is None:
            continue
        unmatched.remove(match_index)
        matched_predictions.add(match_index)
        item = predicted[match_index]
        correct = 0
        for field in fields:
            actual = getattr(item, field)
            target = wanted[field]
            if field == "quantity":
                is_correct = abs(float(actual) - float(target)) < 0.001
            else:
                is_correct = normalized(actual) == normalized(target)
            correct += is_correct
        fields_correct += correct
        exact_items += correct == len(fields)
        if "raw_text" in wanted:
            exact_lines += normalized(item.raw_text) == normalized(wanted["raw_text"])

    unsafe_false_accepts = sum(
        not item.needs_review and item.confirmed
        for index, item in enumerate(predicted)
        if index not in matched_predictions
    )
    return {
        "expected_items": len(expected),
        "predicted_items": len(predicted),
        "exact_items": exact_items,
        "expected_lines": sum("raw_text" in item for item in expected),
        "exact_lines": exact_lines,
        "structured_fields": fields_total,
        "structured_fields_correct": fields_correct,
        "review_items": sum(item.needs_review for item in predicted),
        "unsafe_false_accepts": unsafe_false_accepts,
        "missed_items": len(expected) - len(matched_predictions),
    }


def evaluate_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("The manifest must contain a non-empty cases list.")

    results = []
    totals: dict[str, int] = {}
    for case in cases:
        if case.get("tuned_against") is not False:
            raise ValueError(
                f"{case.get('id', 'Unnamed case')} must declare tuned_against=false."
            )
        image_path = (path.parent / case["image"]).resolve()
        if image_path == TUNED_FIXTURE_DIR or TUNED_FIXTURE_DIR in image_path.parents:
            raise ValueError(
                f"{image_path.name} is a tuned project fixture, not held-out evidence."
            )
        if not image_path.is_file():
            raise ValueError(f"Held-out image does not exist: {image_path}")
        plan = plan_locally(
            text="",
            image_bytes=image_path.read_bytes(),
            image_media_type=case.get("media_type", "image/jpeg"),
        )
        scores = score_case(case["expected"], plan.items)
        results.append({"id": case["id"], **scores})
        for key, value in scores.items():
            totals[key] = totals.get(key, 0) + value

    exact_item_rate = totals["exact_items"] / max(1, totals["expected_items"])
    exact_line_rate = totals["exact_lines"] / max(1, totals["expected_lines"])
    structured_accuracy = totals["structured_fields_correct"] / max(
        1, totals["structured_fields"]
    )
    return {
        "manifest": str(path),
        "held_out": True,
        "case_count": len(results),
        "summary": {
            **totals,
            "exact_item_rate": round(exact_item_rate, 4),
            "exact_line_rate": round(exact_line_rate, 4),
            "structured_accuracy": round(structured_accuracy, 4),
            "review_rate": round(
                totals["review_items"] / max(1, totals["predicted_items"]), 4
            ),
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_manifest(args.manifest.resolve())
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 1 if report["summary"]["unsafe_false_accepts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
