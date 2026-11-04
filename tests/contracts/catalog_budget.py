"""Size of the committed tool catalogs as a client receives them.

Each tool is serialized the way ``tools/list`` carries it (name, title,
description, inputSchema, outputSchema, annotations, _meta) as compact JSON,
and tokens are estimated as ceil(characters / 4); no tokenizer is installed.
The estimate runs about 15 to 20 percent above the cl100k count of the same
JSON, so it is conservative for budgeting. ``tests/test_catalog_budget.py``
holds the committed ceilings; ``scripts/measure_tokens.py`` prints the numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).resolve().parent


def _wire_tool(tool: dict[str, Any], *, output_schema: bool) -> str:
    entry: dict[str, Any] = {
        "name": tool["name"],
        "title": tool["title"],
        "description": tool["description"],
        "inputSchema": tool["inputSchema"],
    }
    if output_schema and tool.get("outputSchema") is not None:
        entry["outputSchema"] = tool["outputSchema"]
    if tool.get("annotations"):
        entry["annotations"] = tool["annotations"]
    if tool.get("meta"):
        entry["_meta"] = tool["meta"]
    return json.dumps(entry, separators=(",", ":"), ensure_ascii=False)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def measure(snapshot_name: str) -> dict[str, int]:
    """Tool count, characters and estimated tokens for one committed snapshot."""
    tools = json.loads((SNAPSHOT_DIR / snapshot_name).read_text(encoding="utf-8"))["tools"]
    with_schemas = "".join(_wire_tool(tool, output_schema=True) for tool in tools)
    without_schemas = "".join(_wire_tool(tool, output_schema=False) for tool in tools)
    return {
        "tools": len(tools),
        "tools_with_output_schema": sum(1 for tool in tools if tool.get("outputSchema") is not None),
        "chars": len(with_schemas),
        "tokens": estimate_tokens(with_schemas),
        "chars_without_output_schemas": len(without_schemas),
        "tokens_without_output_schemas": estimate_tokens(without_schemas),
    }
