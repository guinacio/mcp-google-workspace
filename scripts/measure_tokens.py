#!/usr/bin/env python3
"""Measure the Gmail body cleaner on the synthetic corpus, before and after.

Prints, for every message in ``tests/fixtures/gmail_corpus``, the size of the
first ``gmail_read_emails format=clean`` page under the 1.0.1 cleaner (a
frozen copy kept in the corpus package) and under the current one, whether
each was truncated, and the totals. Then prints the size of the committed tool
catalogs (``tests/contracts/catalog_*.json``) as a client receives them; their
ceilings live in ``tests/test_catalog_budget.py``.

    uv run python scripts/measure_tokens.py           # print the table
    uv run python scripts/measure_tokens.py --write   # also rewrite metrics.json
    uv run python scripts/measure_tokens.py --check   # exit 1 if metrics.json is stale

Tokens are estimated as ceil(characters / 4); no tokenizer is installed. This
is a development tool: it is not part of the package or the MCPB bundle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from contracts.catalog_budget import measure as measure_catalog  # noqa: E402
from fixtures.gmail_corpus.metrics import METRICS_PATH, compute_metrics, render  # noqa: E402


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite the committed metrics.json")
    mode.add_argument("--check", action="store_true", help="fail when metrics.json differs from a fresh run")
    args = parser.parse_args()

    metrics = compute_metrics()
    header = f"{'message':<36} {'html KB':>7} {'before tok':>10} {'trunc':>5} {'after tok':>9} {'trunc':>5} {'change':>7}"
    print(header)
    print("-" * len(header))
    for item in metrics["messages"]:
        before, after = item["before"], item["after"]
        change = (after["first_page_tokens"] - before["first_page_tokens"]) / max(1, before["first_page_tokens"])
        print(
            f"{item['name']:<36} {item['html_chars'] / 1024:>7.1f} {before['first_page_tokens']:>10} "
            f"{_yes_no(before['truncated']):>5} {after['first_page_tokens']:>9} {_yes_no(after['truncated']):>5} "
            f"{change:>+7.0%}"
        )
    totals = metrics["totals"]
    change = (totals["after"]["first_page_tokens"] - totals["before"]["first_page_tokens"]) / max(
        1, totals["before"]["first_page_tokens"]
    )
    print("-" * len(header))
    print(
        f"{'total':<36} {'':>7} {totals['before']['first_page_tokens']:>10} "
        f"{totals['before']['truncated_messages']:>5} {totals['after']['first_page_tokens']:>9} "
        f"{totals['after']['truncated_messages']:>5} {change:>+7.0%}"
    )
    print(f"\ntokens: {metrics['token_estimate']}; trunc columns in the total row count truncated messages")

    print(f"\n{'catalog':<28} {'tools':>5} {'with schema':>11} {'tokens':>8} {'tokens w/o output schemas':>26}")
    for snapshot in ("catalog_default.json", "catalog_all_optional.json"):
        size = measure_catalog(snapshot)
        print(
            f"{snapshot:<28} {size['tools']:>5} {size['tools_with_output_schema']:>11} "
            f"{size['tokens']:>8} {size['tokens_without_output_schemas']:>26}"
        )

    rendered = render(metrics)
    if args.write:
        METRICS_PATH.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"wrote {METRICS_PATH.relative_to(PROJECT_ROOT)}")
    elif args.check and METRICS_PATH.read_text(encoding="utf-8") != rendered:
        print(f"{METRICS_PATH.relative_to(PROJECT_ROOT)} is stale; rerun with --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
