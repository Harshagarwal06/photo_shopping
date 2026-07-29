"""Measure how often the matcher picks the product a request asked for.

The corpus is a snapshot of real provider search results for the terms three
photographed grocery lists actually produced, misreads and all. Holding the
candidates still is the point: a ranking change can then be measured against
fixed data instead of against whatever the provider happens to return today.

    python tools/ranking_corpus.py report
    python tools/ranking_corpus.py report --before picks.json
    python tools/ranking_corpus.py refresh          # live provider searches

`report` scores the current matcher and writes its picks, so the same command
run before and after a change produces a comparable pair. `refresh` re-records
the candidates and needs a connected provider; it is never run by the tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.matcher import match_product
from app.models import PlannedItem, Product

CORPUS_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ranking_corpus.json"
# Long enough to stay under the rate limit that answers a rapid seventh search
# with an empty page. See SEARCH_RETRY_DELAYS_MS in app/blinkit.py.
REFRESH_PAUSE_S = 40
REFRESH_BATCH = 5


def load_cases() -> list[dict]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


def pick_for(case: dict) -> tuple[str, int]:
    """The product this matcher chooses for one case, and where it was listed."""
    products = [Product.model_validate(entry) for entry in case["products"]]
    item = PlannedItem(
        search_term=case["query"],
        context=case["context"],
        quantity=case["quantity"],
        unit=case["unit"],
    )
    decision = match_product(item, products, get_settings())
    for position, product in enumerate(products):
        if product.id == decision.product_id:
            return product.name, position
    return "—", -1


def is_expected(name: str, case: dict) -> bool:
    return any(word in name.lower() for word in case["expect"])


def report(before_path: Path | None) -> int:
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path else {}
    cases = load_cases()
    picks: dict[str, list] = {}
    correct = 0

    print(f"{'':4}{'query':<24}{'pos':>4}  product")
    for case in cases:
        name, position = pick_for(case)
        ok = is_expected(name, case)
        correct += ok
        picks[case["query"]] = [name, position, ok]
        moved = ""
        if case["query"] in before and before[case["query"]][0] != name:
            was_ok = before[case["query"]][2]
            moved = "  FIXED" if ok and not was_ok else "  BROKE" if was_ok and not ok else "  moved"
        print(f"{'OK ' if ok else 'BAD':4}{case['query']:<24}{position:>4}  {name[:44]}{moved}")

    print(f"\n{correct}/{len(cases)} correct")
    if before:
        was = sum(1 for entry in before.values() if entry[2])
        print(f"{was}/{len(before)} before this change")
    Path("ranking_picks.json").write_text(json.dumps(picks, indent=1), encoding="utf-8")
    print("picks written to ranking_picks.json")
    return 0


async def refresh() -> int:
    from app.blinkit import BlinkitClient

    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    client = BlinkitClient(get_settings())
    try:
        for index, case in enumerate(payload["cases"], start=1):
            try:
                products = await client.search(case["query"])
            except Exception as exc:  # a provider refusal must not lose the rest
                print(f"  {case['query']:<24} kept previous results: {str(exc)[:44]}")
                continue
            case["products"] = [product.model_dump(mode="json") for product in products]
            print(f"  {case['query']:<24} {len(products)} results")
            if index % REFRESH_BATCH == 0 and index < len(payload["cases"]):
                await asyncio.sleep(REFRESH_PAUSE_S)
    finally:
        await client.close()

    payload["captured"] = date.today().isoformat()
    CORPUS_PATH.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nrecaptured {CORPUS_PATH.name} on {payload['captured']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scored = commands.add_parser("report", help="score the matcher against the corpus")
    scored.add_argument(
        "--before",
        type=Path,
        help="a ranking_picks.json from an earlier run, to diff against",
    )
    commands.add_parser("refresh", help="re-record candidates from the live provider")

    args = parser.parse_args()
    if args.command == "refresh":
        return asyncio.run(refresh())
    return report(args.before)


if __name__ == "__main__":
    raise SystemExit(main())
