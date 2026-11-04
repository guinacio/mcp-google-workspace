"""Before/after size of the first ``clean`` page for every corpus message.

Tokens are estimated as ``ceil(characters / 4)``: no tokenizer is a
dependency of this project, and the estimate is only used to compare the
1.0.1 cleaner with the current one on the same text, where the ratio matters
more than the absolute count.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from mcp_google_workspace.gmail.mime_utils import extract_message_bodies
from mcp_google_workspace.gmail.presentation import clean_message_content, message_snippet

from . import CORPUS
from . import legacy_cleaner

METRICS_PATH = Path(__file__).with_name("metrics.json")
TOKEN_ESTIMATE = "ceil(characters / 4)"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def compute_metrics() -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for item in CORPUS:
        bodies = extract_message_bodies(item.message["payload"])
        before = legacy_cleaner.clean_message_content(item.message)
        after = clean_message_content(item.message)
        messages.append(
            {
                "name": item.name,
                "html_heavy": item.html_heavy,
                "html_chars": len(bodies["html"]),
                "text_part_chars": len(bodies["text"]),
                "before": {
                    "first_page_chars": len(before["body"]),
                    "first_page_tokens": estimate_tokens(before["body"]),
                    "truncated": before["truncated"],
                    "snippet": legacy_cleaner.snippet(item.message),
                },
                "after": {
                    "first_page_chars": len(after["body"]),
                    "first_page_tokens": estimate_tokens(after["body"]),
                    "truncated": after["truncated"],
                    "body_chars": after["body_chars"],
                    "snippet": message_snippet(item.message),
                },
            }
        )

    def total(side: str, key: str) -> int:
        return sum(int(message[side][key]) for message in messages)

    return {
        "token_estimate": TOKEN_ESTIMATE,
        "page_limit_chars": 8_000,
        "messages": messages,
        "totals": {
            side: {
                "first_page_chars": total(side, "first_page_chars"),
                "first_page_tokens": total(side, "first_page_tokens"),
                "truncated_messages": total(side, "truncated"),
            }
            for side in ("before", "after")
        },
    }


def render(metrics: dict[str, Any]) -> str:
    return json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
