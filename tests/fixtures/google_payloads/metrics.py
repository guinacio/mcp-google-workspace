"""Size of every normalized response against the Google payload it came from.

For each golden case: the estimated tokens of the Google payload (what the
tool returned before 2.0.0 for the raw passthrough tools) and of the
normalized result a client receives, and, for list cases, the average size of
one item. Tokens are estimated as ``ceil(characters / 4)`` of compact JSON,
the same estimate the Gmail corpus and catalog budget use.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from . import all_cases, client_view, load

METRICS_PATH = Path(__file__).with_name("metrics.json")
TOKEN_ESTIMATE = "ceil(characters / 4) of compact JSON"


def tokens(value: Any) -> int:
    return math.ceil(len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) / 4)


def compute_metrics() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in all_cases():
        result = client_view(case)
        entry: dict[str, Any] = {
            "name": case.name,
            "fixture": case.fixture,
            "payload_tokens": tokens(load(case.fixture)),
            "response_tokens": tokens(result),
        }
        items = result.get(case.items_key) if case.items_key else None
        if isinstance(items, list) and items:
            entry["items"] = len(items)
            entry["tokens_per_item"] = math.ceil(sum(tokens(item) for item in items) / len(items))
        cases.append(entry)
    return {"token_estimate": TOKEN_ESTIMATE, "cases": cases}


def render(metrics: dict[str, Any]) -> str:
    return json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
