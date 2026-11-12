"""Every response normalizer, run over a realistic Google payload, matches its golden output.

Inputs are ``tests/fixtures/google_payloads/*.json``; expected outputs are
``tests/fixtures/google_payloads/golden/<case>.json``. A changed normalizer
shows up here as a reviewable diff: rerun with ``UPDATE_GOLDENS=1`` to rewrite
the goldens, then read the diff before committing it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fixtures.google_payloads import GOLDEN_DIR, Case, all_cases, client_view  # noqa: E402

CASES = all_cases()
_CAMEL = re.compile(r"[a-z][A-Z]")
# Values a normalized response must never carry: account-wide context repeated
# per item, and raw timestamps that duplicate their local-time field.
_REDUNDANT_KEYS = {"timezone", "date_timezone", "source_date", "source_created_at", "source_modified_at"}


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _keys(value: Any, opaque: tuple[str, ...], path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append((f"{path}.{key}", key))
            if key not in opaque:
                found.extend(_keys(child, opaque, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_keys(child, opaque, f"{path}[{index}]"))
    return found


def test_cases_are_registered() -> None:
    assert len(CASES) >= 30, [case.name for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_normalizer_matches_golden(case: Case) -> None:
    rendered = _render(client_view(case))
    golden = GOLDEN_DIR / f"{case.name}.json"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.write_text(rendered, encoding="utf-8", newline="\n")
    assert golden.exists(), f"missing {golden.name}; run with UPDATE_GOLDENS=1 and review it"
    assert rendered == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_normalized_keys_are_snake_case_without_redundant_fields(case: Case) -> None:
    keys = _keys(client_view(case), case.opaque_keys)
    camel = [path for path, key in keys if _CAMEL.search(key)]
    assert camel == [], f"camelCase keys leaked from the Google payload: {camel}"
    redundant = [path for path, key in keys if key in _REDUNDANT_KEYS]
    assert redundant == [], redundant


def test_no_orphan_goldens() -> None:
    expected = {f"{case.name}.json" for case in CASES}
    assert sorted(path.name for path in GOLDEN_DIR.glob("*.json") if path.name not in expected) == []


# ---------------------------------------------------------------------------
# Size: per-item token ceilings and the committed metrics
# ---------------------------------------------------------------------------

from fixtures.google_payloads import metrics as response_metrics  # noqa: E402

# Estimated tokens (characters / 4) of one list item as a client receives it,
# resource handle included. 1.1.0 for comparison, same fixtures: a Drive file
# 252, a Gmail search hit 183, a calendar list entry 125, a calendar event 176.
ITEM_CEILINGS = {
    "drive_list_files": 200,
    "gmail_search_emails": 150,
    "calendar_list_calendars": 55,
    "calendar_search_events": 145,
    "people_list_contacts": 120,
    "sheets_get_spreadsheet": 60,
    "slides_get_presentation": 160,
    # Normalized in 2.1.x (#57), measured on their fixtures: a shared drive 99,
    # a sharing grant 56, a Gmail label 40.
    "drive_list_drives": 110,
    "drive_list_permissions": 65,
    "gmail_list_labels": 45,
}
# Tools that used to return Google's resource unchanged: the normalized
# response must be well under the payload it replaces.
SHRINK_RATIO = {
    # 0.4 until 2.1.x added each calendar's selected flag (#68): 165 / 410.
    "calendar_list_calendars": 0.45,
    "docs_get_document": 0.3,
    "docs_get_document_tabs": 0.35,
    "people_list_contacts": 0.5,
    "sheets_get_spreadsheet": 0.4,
    "slides_get_presentation": 0.15,
    "slides_get_slide_page": 0.2,
}
_METRICS = {case["name"]: case for case in response_metrics.compute_metrics()["cases"]}


@pytest.mark.parametrize(("name", "ceiling"), sorted(ITEM_CEILINGS.items()))
def test_list_item_stays_under_its_token_ceiling(name: str, ceiling: int) -> None:
    assert _METRICS[name]["tokens_per_item"] <= ceiling, _METRICS[name]


@pytest.mark.parametrize(("name", "ratio"), sorted(SHRINK_RATIO.items()))
def test_normalized_response_is_much_smaller_than_the_google_payload(name: str, ratio: float) -> None:
    case = _METRICS[name]
    assert case["response_tokens"] <= case["payload_tokens"] * ratio, case


def test_document_read_page_costs_about_two_thousand_tokens() -> None:
    """One 8,000-character page plus the document's identity, whatever the document's size."""
    for name in ("docs_read_document_text", "docs_read_document_markdown", "docs_read_document_markdown_page_2"):
        assert _METRICS[name]["response_tokens"] <= 2_300, _METRICS[name]
        assert _METRICS[name]["payload_tokens"] > 10 * _METRICS[name]["response_tokens"]


def test_committed_response_metrics_are_current() -> None:
    current = response_metrics.render(response_metrics.compute_metrics())
    assert response_metrics.METRICS_PATH.read_text(encoding="utf-8") == current, (
        "tests/fixtures/google_payloads/metrics.json is stale; run scripts/measure_tokens.py --write"
    )
