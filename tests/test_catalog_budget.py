"""Catalog budget: the committed tool catalogs must stay under a token ceiling.

Measured from the committed snapshots (``tests/test_catalog_contract.py`` keeps
them equal to the live catalog) with the chars/4 estimate documented in
``tests/contracts/catalog_budget.py``. The ceilings sit about 10% above the
2.0.0 catalog, so ordinary edits pass and a change that grows the catalog
noticeably has to raise a ceiling here, in review.

2.1.x after documenting result fields (+4,118 default, all in output schemas):
13 tools whose inferred output schemas were untyped (event, file, message and
task items, connection and consent results) now type and describe each field,
for directory compatibility checks; input schemas are unchanged in size:
    default       52,412; without output schemas 39,958
    all optional  81,450; without output schemas 61,431
2.0.0 after the consent model (estimated tokens; -287 default): the
model-settable safety flags are gone, dry_run is new on the bulk and raw
batch-update tools, prepare_workspace_action drops its tool_name enum, and
descriptions say which calls ask:
    default       46,359; without output schemas 38,498
    all optional  75,404; without output schemas 59,979
2.0.0 measurement (estimated tokens; cl100k in parentheses), after the tool
consolidation: 142 default tools become 82 in the core tier (80 for the model
plus the two app-only upload callbacks); the all-optional snapshot is the
complete tier (143 tools, extended tools included):
    default       46,646 (40,892); without output schemas 38,745 (34,106)
    all optional  75,611 (65,738); without output schemas 60,146 (52,726)
2.0.0 after the response envelope (+5,318 default): the 37 former raw
passthrough tools return a normalized response and publish its open output
schema (+2,724), other schemas follow their new shapes (+427), descriptions
name the new fields (+949), and docs_read_document and drive_read_file are new
(+1,040):
    default       67,342 (58,746); without output schemas 55,367 (48,573)
    all optional  91,202 (79,279); without output schemas 72,768 (63,772)
2.0.0 before the response envelope:
    default       62,024 (53,907); without output schemas 53,528 (46,724)
    all optional  85,899 (74,453); without output schemas 70,930 (61,924)
1.0.1 for comparison:
    default       89,194 (75,444); without output schemas 42,830 (38,754)
    all optional 121,439 (102,264); without output schemas 57,949 (52,095)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.catalog_budget import estimate_tokens, measure  # noqa: E402

CEILINGS = {
    "catalog_default.json": {"tokens": 56_000, "tokens_without_output_schemas": 42_500},
    "catalog_all_optional.json": {"tokens": 87_000, "tokens_without_output_schemas": 66_000},
}


@pytest.mark.parametrize("snapshot", sorted(CEILINGS))
def test_catalog_stays_within_its_token_budget(snapshot: str) -> None:
    size = measure(snapshot)
    for key, ceiling in CEILINGS[snapshot].items():
        assert size[key] <= ceiling, (
            f"{snapshot} {key} is {size[key]:,}, above the {ceiling:,} ceiling; trim the change "
            "or raise the ceiling in tests/test_catalog_budget.py with a reason."
        )


def test_token_estimate_is_characters_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
