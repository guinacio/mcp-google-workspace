"""FastMCP tools for workspace dashboard and calendar views."""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta
from typing import Any

import pytz
from fastmcp import Context, FastMCP

from ..auth import build_calendar_service, build_gmail_service
from ..gmail.mime_utils import decode_rfc2047, flatten_parts
from .actions import (
    cancel_meeting,
    create_meeting_from_slot,
    find_meeting_slots,
    respond_to_event,
    reschedule_meeting,
)
from .schemas import (
    CancelMeetingRequest,
    CreateMeetingFromSlotRequest,
    DashboardState,
    DashboardStatePatch,
    FindMeetingSlotsRequest,
    GetEmailDetailRequest,
    GetEventDetailRequest,
    RespondToEventRequest,
    RescheduleMeetingRequest,
)
from .state import get_state, next_range, patch_state, prev_range, set_state, today
from .view_models import (
    build_dashboard_view_model,
    build_email_detail_view_model,
    build_event_detail_view_model,
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


def _fetch_inbox_summary(state: DashboardState) -> tuple[int, list[dict[str, Any]], list[str]]:
    service = build_gmail_service()
    list_limit = 10
    unread_query = "is:unread in:inbox"
    if state.inbox_query:
        unread_query = f"{unread_query} {state.inbox_query}"
    unread_response = (
        service.users()
        .messages()
        .list(userId="me", q=unread_query, maxResults=list_limit)
        .execute()
    )
    unread = unread_response.get("resultSizeEstimate", 0)
    unread_ids = [
        msg.get("id")
        for msg in unread_response.get("messages", [])
        if isinstance(msg.get("id"), str) and msg.get("id")
    ]
    list_query = "in:inbox"
    if state.inbox_query:
        list_query = f"{list_query} {state.inbox_query}"
    latest = service.users().messages().list(userId="me", q=list_query, maxResults=list_limit).execute()
    latest_ids = [
        msg.get("id")
        for msg in latest.get("messages", [])
        if isinstance(msg.get("id"), str) and msg.get("id")
    ]

    items: list[dict[str, Any]] = []
    for message_id in latest_ids:
        full = service.users().messages().get(userId="me", id=message_id, format="metadata").execute()
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
                "snippet": full.get("snippet"),
                "label_ids": full.get("labelIds", []),
                "is_unread": "UNREAD" in (full.get("labelIds", []) or []),
            }
        )
    return unread, items, unread_ids


def _fetch_event_detail(calendar_id: str, event_id: str) -> dict[str, Any]:
    service = build_calendar_service()
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    return build_event_detail_view_model(event, calendar_id).model_dump(mode="json")


def _fetch_email_detail(message_id: str) -> dict[str, Any]:
    service = build_gmail_service()
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return build_email_detail_view_model(message).model_dump(mode="json")


def _fetch_email_attachment(message_id: str, attachment_id: str) -> dict[str, Any]:
    service = build_gmail_service()
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = message.get("payload") or {}

    filename = attachment_id
    mime_type = "application/octet-stream"
    size = 0
    for part in flatten_parts(payload):
        body = part.get("body", {})
        if body.get("attachmentId") != attachment_id:
            continue
        filename = part.get("filename") or filename
        mime_type = part.get("mimeType") or mime_type
        size = body.get("size", 0) or 0
        break

    attachment = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    raw = attachment.get("data")
    if not raw:
        raise ValueError("Attachment content is empty.")
    # Normalize Gmail URL-safe base64 into standard base64 for host download APIs.
    decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
    blob_base64 = base64.b64encode(decoded).decode("ascii")
    return {
        "message_id": message_id,
        "attachment_id": attachment_id,
        "filename": filename,
        "mime_type": mime_type,
        "size": size,
        "blob_base64": blob_base64,
    }


def build_dashboard_payload(state: DashboardState) -> dict[str, Any]:
    section_errors: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    unread_count = 0
    messages: list[dict[str, Any]] = []
    unread_message_ids: list[str] = []
    week_state = state.model_copy(update={"view": "week"})
    try:
        events = _fetch_calendar_events(week_state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["calendar"] = str(exc)
    try:
        unread_count, messages, unread_message_ids = _fetch_inbox_summary(state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["inbox"] = str(exc)
    model = build_dashboard_view_model(
        state=state,
        calendar_events=events,
        unread_count=unread_count,
        inbox_messages=messages,
        unread_message_ids=unread_message_ids,
        section_errors=section_errors,
    )
    # Include weekly calendar view so the UI displays the proper week grid.
    weekly_model = build_weekly_calendar_view_model(
        anchor_date=week_state.anchor_date,
        timezone_name=week_state.timezone,
        events=events,
        include_weekend=week_state.include_weekend,
    )
    payload = model.model_dump(mode="json")
    payload["weekly_calendar"] = weekly_model.model_dump(mode="json")
    return payload


async def build_dashboard_payload_with_progress(state: DashboardState, ctx: Context) -> dict[str, Any]:
    await ctx.report_progress(5, 100, "Preparing dashboard state")
    section_errors: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    unread_count = 0
    messages: list[dict[str, Any]] = []
    unread_message_ids: list[str] = []
    week_state = state.model_copy(update={"view": "week"})

    try:
        await ctx.report_progress(20, 100, "Loading calendar events")
        events = _fetch_calendar_events(week_state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["calendar"] = str(exc)

    try:
        await ctx.report_progress(55, 100, "Loading inbox summary")
        unread_count, messages, unread_message_ids = _fetch_inbox_summary(state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["inbox"] = str(exc)

    await ctx.report_progress(85, 100, "Building dashboard view model")
    model = build_dashboard_view_model(
        state=state,
        calendar_events=events,
        unread_count=unread_count,
        inbox_messages=messages,
        unread_message_ids=unread_message_ids,
        section_errors=section_errors,
    )
    weekly_model = build_weekly_calendar_view_model(
        anchor_date=week_state.anchor_date,
        timezone_name=week_state.timezone,
        events=events,
        include_weekend=week_state.include_weekend,
    )
    await ctx.report_progress(100, 100, "Dashboard ready")
    payload = model.model_dump(mode="json")
    payload["weekly_calendar"] = weekly_model.model_dump(mode="json")
    return payload


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
    async def apps_set_state(
        session_id: str | None = None,
        view: str | None = None,
        anchor_date: date | None = None,
        timezone: str | None = None,
        selected_calendars: list[str] | None = None,
        inbox_query: str | None = None,
        include_weekend: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Replace dashboard state for the caller session."""
        fields: dict[str, Any] = {}
        if session_id is not None:
            fields["session_id"] = session_id
        if view is not None:
            fields["view"] = view
        if anchor_date is not None:
            fields["anchor_date"] = anchor_date
        if timezone is not None:
            fields["timezone"] = timezone
        if selected_calendars is not None:
            fields["selected_calendars"] = selected_calendars
        if inbox_query is not None:
            fields["inbox_query"] = inbox_query
        if include_weekend is not None:
            fields["include_weekend"] = include_weekend
        request = DashboardState(**fields)
        sid = _resolve_session_id(request.session_id, ctx)
        updated = set_state(sid, request)
        if ctx is not None:
            await ctx.info(f"Dashboard state replaced for session {sid}.")
        return updated.model_dump(mode="json")

    @server.tool(name="patch_state")
    async def apps_patch_state(
        session_id: str | None = None,
        view: str | None = None,
        anchor_date: date | None = None,
        timezone: str | None = None,
        selected_calendars: list[str] | None = None,
        inbox_query: str | None = None,
        include_weekend: bool | None = None,
    ) -> dict[str, Any]:
        """Patch selected dashboard state fields for the caller session."""
        request = DashboardStatePatch(
            view=view,
            anchor_date=anchor_date,
            timezone=timezone,
            selected_calendars=selected_calendars,
            inbox_query=inbox_query,
            include_weekend=include_weekend,
        )
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

    @server.tool(name="get_event_detail")
    async def apps_get_event_detail(
        event_id: str,
        calendar_id: str = "primary",
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return full event details (attendees, location, description, conference)."""
        if ctx is not None:
            await ctx.report_progress(20, 100, "Loading event details")
        payload = _fetch_event_detail(calendar_id, event_id)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Event details ready")
        return payload

    @server.tool(name="get_email_detail")
    async def apps_get_email_detail(
        message_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return full email details (headers + body)."""
        if ctx is not None:
            await ctx.report_progress(20, 100, "Loading email details")
        payload = _fetch_email_detail(message_id)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Email details ready")
        return payload

    @server.tool(name="get_email_attachment")
    async def apps_get_email_attachment(
        message_id: str,
        attachment_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return attachment content (base64) for one Gmail message attachment."""
        if ctx is not None:
            await ctx.report_progress(20, 100, "Loading attachment data")
        payload = _fetch_email_attachment(message_id, attachment_id)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Attachment ready")
        return payload

    @server.tool(name="find_meeting_slots")
    async def apps_find_meeting_slots(
        time_min: str,
        time_max: str,
        participants: list[str] | str | None = None,
        slot_duration_minutes: int = 30,
        meeting_duration: int | None = None,
        granularity_minutes: int = 15,
        max_results: int = 10,
        time_zone: str = "UTC",
        working_hours_start: str = "08:00",
        working_hours_end: str = "17:00",
    ) -> dict[str, Any]:
        """Find common free slots for participants in a window.

        Notes:
        - `participants` may be sent as an array, JSON-stringified array, or comma-separated string.
        - `meeting_duration` is accepted as a legacy alias for `slot_duration_minutes`.
        """
        effective_duration = meeting_duration if meeting_duration is not None else slot_duration_minutes
        request = FindMeetingSlotsRequest(
            participants=participants or ["primary"],
            time_min=time_min,
            time_max=time_max,
            slot_duration_minutes=effective_duration,
            granularity_minutes=granularity_minutes,
            max_results=max_results,
            time_zone=time_zone,
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
        )
        return find_meeting_slots(request)

    @server.tool(name="create_meeting_from_slot")
    async def apps_create_meeting_from_slot(
        title: str,
        start: str,
        end: str,
        idempotency_key: str,
        session_id: str | None = None,
        calendar_id: str = "primary",
        timezone: str = "UTC",
        description: str | None = None,
        attendees: list[str] | None = None,
        create_conference: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a meeting from an explicit slot with idempotency support."""
        request = CreateMeetingFromSlotRequest(
            session_id=session_id,
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            timezone=timezone,
            description=description,
            attendees=attendees or [],
            create_conference=create_conference,
            idempotency_key=idempotency_key,
        )
        sid = _resolve_session_id(request.session_id, ctx)
        result = create_meeting_from_slot(sid, request)
        return result.model_dump(mode="json")

    @server.tool(name="reschedule_meeting")
    async def apps_reschedule_meeting(
        event_id: str,
        start: str,
        end: str,
        idempotency_key: str,
        session_id: str | None = None,
        calendar_id: str = "primary",
        timezone: str = "UTC",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Reschedule an existing meeting with idempotency support."""
        request = RescheduleMeetingRequest(
            session_id=session_id,
            calendar_id=calendar_id,
            event_id=event_id,
            start=start,
            end=end,
            timezone=timezone,
            idempotency_key=idempotency_key,
        )
        sid = _resolve_session_id(request.session_id, ctx)
        result = reschedule_meeting(sid, request)
        return result.model_dump(mode="json")

    @server.tool(name="cancel_meeting")
    async def apps_cancel_meeting(
        event_id: str,
        idempotency_key: str,
        session_id: str | None = None,
        calendar_id: str = "primary",
        confirm: bool = False,
        send_updates: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Cancel an existing meeting with explicit confirmation."""
        request = CancelMeetingRequest(
            session_id=session_id,
            calendar_id=calendar_id,
            event_id=event_id,
            confirm=confirm,
            send_updates=send_updates,
            idempotency_key=idempotency_key,
        )
        sid = _resolve_session_id(request.session_id, ctx)
        result = cancel_meeting(sid, request)
        return result.model_dump(mode="json")

    @server.tool(name="respond_to_event")
    async def apps_respond_to_event(
        event_id: str,
        response_status: str,
        idempotency_key: str,
        session_id: str | None = None,
        calendar_id: str = "primary",
        send_updates: str | None = "all",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Set RSVP status for an event attendee and return updated action status."""
        request = RespondToEventRequest(
            session_id=session_id,
            calendar_id=calendar_id,
            event_id=event_id,
            response_status=response_status,
            send_updates=send_updates,
            idempotency_key=idempotency_key,
        )
        sid = _resolve_session_id(request.session_id, ctx)
        result = respond_to_event(sid, request)
        return result.model_dump(mode="json")
