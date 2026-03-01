"""FastMCP tools for workspace dashboard and morning briefing."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytz
from fastmcp import Context, FastMCP

from ..auth import build_calendar_service, build_gmail_service
from ..gmail.mime_utils import decode_rfc2047
from .actions import (
    cancel_meeting,
    create_meeting_from_slot,
    find_meeting_slots,
    reschedule_meeting,
)
from .schemas import (
    CancelMeetingRequest,
    CreateMeetingFromSlotRequest,
    DashboardState,
    DashboardStatePatch,
    FindMeetingSlotsRequest,
    MorningBriefingRequest,
    RescheduleMeetingRequest,
)
from .state import get_state, next_range, patch_state, prev_range, set_state, today
from .view_models import (
    build_dashboard_view_model,
    build_morning_briefing_view_model,
    build_weekly_calendar_view_model,
)


def _resolve_session_id(candidate: str | None, ctx: Context | None = None) -> str:
    if candidate:
        return candidate
    if ctx is not None:
        for attr_name in ("session_id", "conversation_id", "request_id"):
            attr_value = getattr(ctx, attr_name, None)
            if isinstance(attr_value, str) and attr_value:
                return attr_value
    return "default"


def _compute_window(state: DashboardState) -> tuple[str, str]:
    tz = pytz.timezone(state.timezone)
    start_local = tz.localize(datetime.combine(state.anchor_date, datetime.min.time()))
    if state.view in {"agenda", "day"}:
        end_local = start_local + timedelta(days=1)
    elif state.view == "week":
        end_local = start_local + timedelta(days=7)
    else:
        end_local = start_local + timedelta(days=30)
    return start_local.astimezone(pytz.UTC).isoformat(), end_local.astimezone(pytz.UTC).isoformat()


def _fetch_calendar_events(state: DashboardState) -> list[dict[str, Any]]:
    service = build_calendar_service()
    time_min, time_max = _compute_window(state)
    events: list[dict[str, Any]] = []
    for calendar_id in state.selected_calendars:
        page = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            )
            .execute()
        )
        for item in page.get("items", []):
            enriched = dict(item)
            enriched["calendar_id"] = calendar_id
            events.append(enriched)
    events.sort(key=lambda event: (event.get("start", {}).get("dateTime") or event.get("start", {}).get("date") or ""))
    return events


def _fetch_inbox_summary(state: DashboardState) -> tuple[int, list[dict[str, Any]]]:
    service = build_gmail_service()
    unread_query = "is:unread in:inbox"
    if state.inbox_query:
        unread_query = f"{unread_query} {state.inbox_query}"
    unread = (
        service.users()
        .messages()
        .list(userId="me", q=unread_query, maxResults=25)
        .execute()
        .get("resultSizeEstimate", 0)
    )
    list_query = "in:inbox"
    if state.inbox_query:
        list_query = f"{list_query} {state.inbox_query}"
    latest = service.users().messages().list(userId="me", q=list_query, maxResults=10).execute()
    items: list[dict[str, Any]] = []
    for msg in latest.get("messages", []):
        full = service.users().messages().get(userId="me", id=msg["id"], format="metadata").execute()
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in full.get("payload", {}).get("headers", [])
        }
        items.append(
            {
                "id": full.get("id"),
                "subject": decode_rfc2047(headers.get("subject")),
                "from": decode_rfc2047(headers.get("from")),
                "date": headers.get("date"),
            }
        )
    return unread, items


def build_dashboard_payload(state: DashboardState) -> dict[str, Any]:
    section_errors: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    unread_count = 0
    messages: list[dict[str, Any]] = []
    try:
        events = _fetch_calendar_events(state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["calendar"] = str(exc)
    try:
        unread_count, messages = _fetch_inbox_summary(state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["inbox"] = str(exc)
    model = build_dashboard_view_model(
        state=state,
        calendar_events=events,
        unread_count=unread_count,
        inbox_messages=messages,
        section_errors=section_errors,
    )
    return model.model_dump(mode="json")


async def build_dashboard_payload_with_progress(state: DashboardState, ctx: Context) -> dict[str, Any]:
    await ctx.report_progress(5, 100, "Preparing dashboard state")
    section_errors: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    unread_count = 0
    messages: list[dict[str, Any]] = []

    try:
        await ctx.report_progress(20, 100, "Loading calendar events")
        events = _fetch_calendar_events(state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["calendar"] = str(exc)

    try:
        await ctx.report_progress(55, 100, "Loading inbox summary")
        unread_count, messages = _fetch_inbox_summary(state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["inbox"] = str(exc)

    await ctx.report_progress(85, 100, "Building dashboard view model")
    model = build_dashboard_view_model(
        state=state,
        calendar_events=events,
        unread_count=unread_count,
        inbox_messages=messages,
        section_errors=section_errors,
    )
    await ctx.report_progress(100, 100, "Dashboard ready")
    return model.model_dump(mode="json")


def build_morning_briefing_payload(state: DashboardState, request: MorningBriefingRequest) -> dict[str, Any]:
    briefing_state = state
    if request.date is not None:
        briefing_state = state.model_copy(update={"anchor_date": request.date})
    if request.timezone:
        briefing_state = briefing_state.model_copy(update={"timezone": request.timezone})
    events = _fetch_calendar_events(briefing_state)
    unread_count = 0
    messages: list[dict[str, Any]] = []
    if request.include_inbox:
        unread_count, messages = _fetch_inbox_summary(briefing_state)
    model = build_morning_briefing_view_model(
        briefing_date=briefing_state.anchor_date,
        timezone=briefing_state.timezone,
        events=events,
        unread_count=unread_count,
        inbox_messages=messages,
        max_priorities=request.max_priorities,
        max_quick_wins=request.max_quick_wins,
    )
    return model.model_dump(mode="json")


async def build_morning_briefing_payload_with_progress(
    state: DashboardState,
    request: MorningBriefingRequest,
    ctx: Context,
) -> dict[str, Any]:
    await ctx.report_progress(5, 100, "Preparing morning briefing")
    briefing_state = state
    if request.date is not None:
        briefing_state = state.model_copy(update={"anchor_date": request.date})
    if request.timezone:
        briefing_state = briefing_state.model_copy(update={"timezone": request.timezone})

    await ctx.report_progress(30, 100, "Loading calendar events")
    events = _fetch_calendar_events(briefing_state)

    unread_count = 0
    messages: list[dict[str, Any]] = []
    if request.include_inbox:
        await ctx.report_progress(60, 100, "Loading inbox summary")
        unread_count, messages = _fetch_inbox_summary(briefing_state)

    await ctx.report_progress(85, 100, "Building briefing view model")
    model = build_morning_briefing_view_model(
        briefing_date=briefing_state.anchor_date,
        timezone=briefing_state.timezone,
        events=events,
        unread_count=unread_count,
        inbox_messages=messages,
        max_priorities=request.max_priorities,
        max_quick_wins=request.max_quick_wins,
    )
    await ctx.report_progress(100, 100, "Morning briefing ready")
    return model.model_dump(mode="json")


def build_weekly_calendar_payload(
    state: DashboardState,
    *,
    date_override: date | None = None,
    include_weekend_override: bool | None = None,
) -> dict[str, Any]:
    weekly_state = state
    if date_override is not None:
        weekly_state = weekly_state.model_copy(update={"anchor_date": date_override})
    if include_weekend_override is not None:
        weekly_state = weekly_state.model_copy(update={"include_weekend": include_weekend_override})
    events = _fetch_calendar_events(weekly_state.model_copy(update={"view": "week"}))
    model = build_weekly_calendar_view_model(
        anchor_date=weekly_state.anchor_date,
        timezone_name=weekly_state.timezone,
        events=events,
        include_weekend=weekly_state.include_weekend,
    )
    return model.model_dump(mode="json")


async def build_weekly_calendar_payload_with_progress(
    state: DashboardState,
    *,
    ctx: Context,
    date_override: date | None = None,
    include_weekend_override: bool | None = None,
) -> dict[str, Any]:
    await ctx.report_progress(5, 100, "Preparing weekly calendar view")
    weekly_state = state
    if date_override is not None:
        weekly_state = weekly_state.model_copy(update={"anchor_date": date_override})
    if include_weekend_override is not None:
        weekly_state = weekly_state.model_copy(update={"include_weekend": include_weekend_override})

    await ctx.report_progress(35, 100, "Loading weekly calendar events")
    events = _fetch_calendar_events(weekly_state.model_copy(update={"view": "week"}))

    await ctx.report_progress(80, 100, "Building weekly view model")
    model = build_weekly_calendar_view_model(
        anchor_date=weekly_state.anchor_date,
        timezone_name=weekly_state.timezone,
        events=events,
        include_weekend=weekly_state.include_weekend,
    )
    await ctx.report_progress(100, 100, "Weekly calendar ready")
    return model.model_dump(mode="json")


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_state")
    async def apps_get_state(session_id: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        """Get current dashboard state for the caller session."""
        sid = _resolve_session_id(session_id)
        state = get_state(sid, timezone=timezone)
        return state.model_dump(mode="json")

    @server.tool(name="set_state")
    async def apps_set_state(request: DashboardState, ctx: Context) -> dict[str, Any]:
        """Replace dashboard state for the caller session."""
        sid = _resolve_session_id(request.session_id, ctx)
        updated = set_state(sid, request)
        await ctx.info(f"Dashboard state replaced for session {sid}.")
        return updated.model_dump(mode="json")

    @server.tool(name="patch_state")
    async def apps_patch_state(request: DashboardStatePatch, session_id: str | None = None) -> dict[str, Any]:
        """Patch selected dashboard state fields for the caller session."""
        sid = _resolve_session_id(session_id)
        updated = patch_state(sid, request)
        return updated.model_dump(mode="json")

    @server.tool(name="next_range")
    async def apps_next_range(session_id: str | None = None) -> dict[str, Any]:
        """Move dashboard anchor date to the next range based on current view."""
        sid = _resolve_session_id(session_id)
        updated = next_range(sid)
        return updated.model_dump(mode="json")

    @server.tool(name="prev_range")
    async def apps_prev_range(session_id: str | None = None) -> dict[str, Any]:
        """Move dashboard anchor date to the previous range based on current view."""
        sid = _resolve_session_id(session_id)
        updated = prev_range(sid)
        return updated.model_dump(mode="json")

    @server.tool(name="today")
    async def apps_today(session_id: str | None = None) -> dict[str, Any]:
        """Reset dashboard anchor date to today for this session."""
        sid = _resolve_session_id(session_id)
        updated = today(sid)
        return updated.model_dump(mode="json")

    @server.tool(
        name="get_dashboard",
        annotations={
            "_meta": {
                "ui": {
                    "resourceUri": "ui://apps/dashboard-ui",
                },
            },
        },
        meta={"ui/resourceUri": "ui://apps/dashboard-ui"},
    )
    async def apps_get_dashboard(
        session_id: str | None = None,
        date_override: date | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Build workspace dashboard view model from calendar and inbox data."""
        sid = _resolve_session_id(session_id)
        state = get_state(sid)
        if date_override is not None:
            state = patch_state(sid, DashboardStatePatch(anchor_date=date_override))
        if ctx is not None:
            return await build_dashboard_payload_with_progress(state, ctx)
        return build_dashboard_payload(state)

    @server.tool(
        name="get_weekly_calendar_view",
        annotations={
            "_meta": {
                "ui": {
                    "resourceUri": "ui://apps/dashboard-ui",
                },
            },
        },
        meta={"ui/resourceUri": "ui://apps/dashboard-ui"},
    )
    async def apps_get_weekly_calendar_view(
        session_id: str | None = None,
        date_override: date | None = None,
        include_weekend: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return a Google Calendar-like weekly view model (columns per day)."""
        sid = _resolve_session_id(session_id)
        state = get_state(sid)
        if ctx is not None:
            return await build_weekly_calendar_payload_with_progress(
                state,
                ctx=ctx,
                date_override=date_override,
                include_weekend_override=include_weekend,
            )
        return build_weekly_calendar_payload(
            state,
            date_override=date_override,
            include_weekend_override=include_weekend,
        )

    @server.tool(
        name="get_morning_briefing",
        annotations={
            "_meta": {
                "ui": {
                    "resourceUri": "ui://apps/dashboard-ui",
                },
            },
        },
        meta={"ui/resourceUri": "ui://apps/dashboard-ui"},
    )
    async def apps_get_morning_briefing(
        session_id: str | None = None,
        date_override: date | None = None,
        timezone: str | None = None,
        max_priorities: int = 5,
        max_quick_wins: int = 5,
        include_inbox: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Build deterministic morning briefing with priorities, risks, and actions."""
        request = MorningBriefingRequest(
            session_id=session_id,
            date=date_override,
            timezone=timezone,
            max_priorities=max_priorities,
            max_quick_wins=max_quick_wins,
            include_inbox=include_inbox,
        )
        sid = _resolve_session_id(request.session_id, ctx)
        state = get_state(sid, timezone=request.timezone)
        payload = await build_morning_briefing_payload_with_progress(state, request, ctx)
        await ctx.info(f"Morning briefing generated for {payload.get('date')}.")
        return payload

    @server.tool(name="find_meeting_slots")
    async def apps_find_meeting_slots(request: FindMeetingSlotsRequest) -> dict[str, Any]:
        """Find common free slots for participants in a window."""
        return find_meeting_slots(request)

    @server.tool(name="create_meeting_from_slot")
    async def apps_create_meeting_from_slot(request: CreateMeetingFromSlotRequest, ctx: Context) -> dict[str, Any]:
        """Create a meeting from an explicit slot with idempotency support."""
        sid = _resolve_session_id(request.session_id, ctx)
        result = create_meeting_from_slot(sid, request)
        return result.model_dump(mode="json")

    @server.tool(name="reschedule_meeting")
    async def apps_reschedule_meeting(request: RescheduleMeetingRequest, ctx: Context) -> dict[str, Any]:
        """Reschedule an existing meeting with idempotency support."""
        sid = _resolve_session_id(request.session_id, ctx)
        result = reschedule_meeting(sid, request)
        return result.model_dump(mode="json")

    @server.tool(name="cancel_meeting")
    async def apps_cancel_meeting(request: CancelMeetingRequest, ctx: Context) -> dict[str, Any]:
        """Cancel an existing meeting with explicit confirmation."""
        sid = _resolve_session_id(request.session_id, ctx)
        result = cancel_meeting(sid, request)
        return result.model_dump(mode="json")
