"""Recorded-shape Google API payloads and the normalizers they feed.

Each ``*.json`` file here is one Google API response shaped like the API
reference (field names, nesting, types), with identifiers and personal data
replaced. They are the inputs of the response normalizers; the expected
normalized output of each case is committed beside them under ``golden/``
(``tests/test_response_goldens.py`` compares the two, and
``UPDATE_GOLDENS=1`` rewrites them for review).

Cases live in ``cases_<namespace>.py`` modules, each exposing ``CASES``. A
case's golden output is what a client receives as ``structuredContent``,
resource handles included (the server's middleware adds them after the tool
returns); the list fields the pagination wrapper adds (``has_more``,
``fetched_at``) are not part of it.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = FIXTURE_DIR / "golden"
# Every case normalizes in this account time zone at this instant, so the
# local-time fields and relative flags (is_overdue) are reproducible.
TIMEZONE = "America/Sao_Paulo"
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Case:
    """One normalizer run over one fixture.

    ``name`` is the golden file stem and starts with the tool's namespace
    (``docs_...``), which selects the resource-handle kinds. ``normalize``
    turns the loaded fixture into the tool result. ``items_key`` names the
    list whose per-item size the token metrics report. Keys in
    ``opaque_keys`` hold Google's own objects on purpose (a batchUpdate's
    ``replies``) and are exempt from the snake_case check.
    """

    name: str
    fixture: str
    normalize: Callable[[Any], dict[str, Any]]
    items_key: str | None = None
    opaque_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def namespace(self) -> str:
        return self.name.split("_", 1)[0]


def load(name: str) -> Any:
    """A fresh copy of one fixture."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def all_cases() -> list[Case]:
    cases: list[Case] = []
    for module in sorted(pkgutil.iter_modules([str(FIXTURE_DIR)]), key=lambda item: item.name):
        if module.name.startswith("cases_"):
            cases.extend(importlib.import_module(f"{__name__}.{module.name}").CASES)
    names = [case.name for case in cases]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate golden case names: {duplicates}")
    return cases


def client_view(case: Case) -> dict[str, Any]:
    """The normalized result as a client receives it: resource handles attached."""
    from mcp_google_workspace.common.resources import add_resource_handles

    result = case.normalize(load(case.fixture))
    add_resource_handles(result, case.namespace)
    return result
