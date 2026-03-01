#!/usr/bin/env python3
"""
Apps-only QA smoke script for mcp-google-workspace.

Usage:
  uv run python scripts/qa_apps_smoke.py
  uv run python scripts/qa_apps_smoke.py --sse-url http://127.0.0.1:8001/sse
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_project_root() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    os.chdir(PROJECT_ROOT)


def _is_ok_response(payload: Any, required_keys: list[str]) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, f"payload is not a dict: {type(payload).__name__}"
    missing = [key for key in required_keys if key not in payload]
    if missing:
        return False, f"missing keys: {', '.join(missing)}"
    return True, ""


def _extract_data(result: Any) -> Any:
    return result.data if hasattr(result, "data") else result


async def _run_smoke(client: Client, session_id: str) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    tools = await client.list_tools()
    tool_names = {tool.name for tool in tools}
    required_tools = {
        "apps_get_state",
        "apps_today",
        "apps_get_dashboard",
        "apps_get_weekly_calendar_view",
        "apps_get_morning_briefing",
        "apps_find_meeting_slots",
        "apps_respond_to_event",
    }
    missing = sorted(required_tools - tool_names)
    checks.append(("tools:required_apps_tools_present", not missing, "" if not missing else str(missing)))
    if missing:
        return checks

    state = _extract_data(await client.call_tool("apps_get_state", {"session_id": session_id}))
    ok, msg = _is_ok_response(state, ["session_id", "view", "anchor_date", "timezone"])
    checks.append(("tool:apps_get_state", ok, msg))

    today_state = _extract_data(await client.call_tool("apps_today", {"session_id": session_id}))
    ok, msg = _is_ok_response(today_state, ["session_id", "anchor_date", "timezone"])
    checks.append(("tool:apps_today", ok, msg))

    dashboard = _extract_data(await client.call_tool("apps_get_dashboard", {"session_id": session_id}))
    ok, msg = _is_ok_response(dashboard, ["title", "state", "sections"])
    checks.append(("tool:apps_get_dashboard", ok, msg))

    weekly = _extract_data(
        await client.call_tool(
            "apps_get_weekly_calendar_view",
            {"session_id": session_id, "include_weekend": True},
        )
    )
    ok, msg = _is_ok_response(weekly, ["week_start", "week_end", "days", "total_events"])
    checks.append(("tool:apps_get_weekly_calendar_view", ok, msg))

    briefing = _extract_data(
        await client.call_tool(
            "apps_get_morning_briefing",
            {"request": {"session_id": session_id, "include_inbox": False}},
        )
    )
    ok, msg = _is_ok_response(briefing, ["date", "summary", "priorities", "fallback_text"])
    checks.append(("tool:apps_get_morning_briefing", ok, msg))

    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=8)
    slots = _extract_data(
        await client.call_tool(
            "apps_find_meeting_slots",
            {
                "request": {
                    "participants": ["primary"],
                    "time_min": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "time_max": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "slot_duration_minutes": 30,
                    "granularity_minutes": 15,
                    "max_results": 3,
                    "time_zone": "UTC",
                }
            },
        )
    )
    ok, msg = _is_ok_response(slots, ["participants", "suggested_slots", "total_suggestions"])
    checks.append(("tool:apps_find_meeting_slots", ok, msg))

    today_ymd = date.today().isoformat()
    resources = [
        "apps://apps/dashboard/current",
        f"apps://apps/dashboard/week/{today_ymd}",
        f"apps://apps/calendar/week/{today_ymd}",
        f"apps://apps/briefing/morning/{today_ymd}",
    ]
    for uri in resources:
        try:
            content = await client.read_resource(uri)
            text = str(getattr(content, "contents", None) or getattr(content, "data", None) or content)
            checks.append((f"resource:{uri}", len(text) > 0, "" if len(text) > 0 else "empty resource payload"))
        except Exception as exc:  # pragma: no cover - integration behavior
            checks.append((f"resource:{uri}", False, str(exc)))

    return checks


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Apps MCP smoke tests.")
    parser.add_argument("--sse-url", type=str, default=None, help="Optional SSE URL (example: http://127.0.0.1:8001/sse)")
    parser.add_argument("--session-id", type=str, default="qa-apps-smoke", help="Session identifier used for stateful app tools.")
    args = parser.parse_args()

    _ensure_project_root()

    # Ensure apps namespace is mounted for in-process mode.
    if not os.getenv("ENABLE_APPS_DASHBOARD"):
        os.environ["ENABLE_APPS_DASHBOARD"] = "true"

    if args.sse_url:
        client = Client(args.sse_url)
    else:
        from mcp_google_workspace.server import workspace_mcp

        client = Client(workspace_mcp)

    async with client:
        checks = await _run_smoke(client, args.session_id)

    passed = sum(1 for _, ok, _ in checks if ok)
    failed = sum(1 for _, ok, _ in checks if not ok)

    print("\n--- Apps smoke results ---")
    for name, ok, error in checks:
        status = "PASS" if ok else "FAIL"
        suffix = "" if not error else f" | {error}"
        print(f"{status:>4}  {name}{suffix}")
    print(f"\nTotal: {passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
