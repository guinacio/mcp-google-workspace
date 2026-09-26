"""FastMCP tools for workspace dashboard and calendar views."""

from __future__ import annotations

import logging

import base64
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

import mcp.types as mt
import pytz
from dateutil.relativedelta import relativedelta
from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from pydantic import Field

from ..auth import build_calendar_service, build_gmail_service
from ..common.async_ops import run_blocking
from ..common.downloads import max_download_bytes
from ..common.errors import render_error_message
from ..common.output_schemas import VIEW_DESCRIPTOR_SCHEMA
from ..common.timezone import resolve_user_timezone, user_now
from ..gmail.mime_utils import decode_rfc2047, flatten_parts
from ..gmail.presentation import envelope as gmail_envelope
from .schemas import (
    AppError,
    DashboardState,
    DashboardStatePatch,
)
from .state import (
    HANDLE_MAX_LENGTH,
    VIEW_META_KEY,
    DashboardView,
    DashboardViewService,
    ViewHandleError,
    ViewStateConflict,
    dashboard_views,
    next_range,
    patch_state,
    prev_range,
)
from .view_models import (
    build_dashboard_view_model,
    build_email_detail_view_model,
    build_event_detail_view_model,
    build_weekly_calendar_view_model,
)

LOGGER = logging.getLogger(__name__)

_ATTACHMENT_EXTENSION_BY_MIME: dict[str, str] = {
    "application/json": ".json",
    "application/msword": ".doc",
    "application/octet-stream": ".bin",
    "application/pdf": ".pdf",
    "application/rtf": ".rtf",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/zip": ".zip",
    "audio/mpeg": ".mp3",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "text/calendar": ".ics",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/plain": ".txt",
}


def _compute_window(state: DashboardState) -> tuple[str, str]:
    tz = pytz.timezone(state.timezone)
    anchor_date = state.anchor_date
    if state.view == "week":
        if state.include_weekend:
            days_since_week_start = (anchor_date.weekday() + 1) % 7
            anchor_date = anchor_date - timedelta(days=days_since_week_start)
            duration_days = 7
        else:
            anchor_date = anchor_date - timedelta(days=anchor_date.weekday())
            duration_days = 5
    elif state.view in {"agenda", "day"}:
        duration_days = 1
    else:
        month_start = anchor_date.replace(day=1)
        next_month = month_start + relativedelta(months=1)
        start_local = tz.localize(datetime.combine(month_start, datetime.min.time()))
        end_local = tz.localize(datetime.combine(next_month, datetime.min.time()))
        return start_local.astimezone(pytz.UTC).isoformat(), end_local.astimezone(
            pytz.UTC
        ).isoformat()
    start_local = tz.localize(datetime.combine(anchor_date, datetime.min.time()))
    end_local = start_local + timedelta(days=duration_days)
    return start_local.astimezone(pytz.UTC).isoformat(), end_local.astimezone(
        pytz.UTC
    ).isoformat()


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
        )
        page = _execute_request(page)
        for item in page.get("items", []):
            enriched = dict(item)
            enriched["calendar_id"] = calendar_id
            events.append(enriched)
    events.sort(
        key=lambda event: (
            event.get("start", {}).get("dateTime")
            or event.get("start", {}).get("date")
            or ""
        )
    )
    return events


def _fetch_inbox_summary(
    state: DashboardState,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    service = build_gmail_service()
    list_limit = 10
    unread_query = "is:unread in:inbox"
    if state.inbox_query:
        unread_query = f"{unread_query} {state.inbox_query}"
    unread_response = _execute_request(
        service.users().messages().list(userId="me", q=unread_query, maxResults=list_limit)
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
    latest = _execute_request(
        service.users().messages().list(userId="me", q=list_query, maxResults=list_limit)
    )
    latest_ids = [
        msg.get("id")
        for msg in latest.get("messages", [])
        if isinstance(msg.get("id"), str) and msg.get("id")
    ]

    items: list[dict[str, Any]] = []
    for message_id in latest_ids:
        full = _execute_request(
            service.users().messages().get(userId="me", id=message_id, format="metadata")
        )
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in full.get("payload", {}).get("headers", [])
        }
        message_envelope = gmail_envelope(full, account_timezone=state.timezone)
        items.append(
            {
                "id": full.get("id"),
                "subject": decode_rfc2047(headers.get("subject")),
                "from": decode_rfc2047(headers.get("from")),
                "date": message_envelope["date"],
                "date_timezone": message_envelope["date_timezone"],
                "snippet": full.get("snippet"),
                "label_ids": full.get("labelIds", []),
                "is_unread": "UNREAD" in (full.get("labelIds", []) or []),
            }
        )
    return unread, items, unread_ids


def _fetch_event_detail(
    calendar_id: str, event_id: str, account_timezone: str
) -> dict[str, Any]:
    service = build_calendar_service()
    event = _execute_request(
        service.events().get(
            calendarId=calendar_id,
            eventId=event_id,
            timeZone=account_timezone,
        )
    )
    return build_event_detail_view_model(event, calendar_id).model_dump(mode="json")


def _fetch_email_detail(message_id: str, account_timezone: str) -> dict[str, Any]:
    service = build_gmail_service()
    message = _execute_request(
        service.users().messages().get(userId="me", id=message_id, format="full")
    )
    payload = build_email_detail_view_model(message).model_dump(mode="json")
    normalized = gmail_envelope(message, account_timezone=account_timezone)
    payload["date"] = normalized["date"]
    payload["date_timezone"] = normalized["date_timezone"]
    payload["source_date"] = normalized["source_date"]
    return payload


def _fallback_attachment_filename(mime_type: str | None) -> str:
    normalized_mime_type = str(mime_type or "").split(";", 1)[0].strip().lower()
    extension = _ATTACHMENT_EXTENSION_BY_MIME.get(normalized_mime_type, "")
    return f"attachment{extension}"


def _fetch_email_attachment(message_id: str, attachment_id: str) -> dict[str, Any]:
    service = build_gmail_service()
    message = _execute_request(
        service.users().messages().get(userId="me", id=message_id, format="full")
    )
    payload = message.get("payload") or {}

    mime_type = "application/octet-stream"
    filename: str | None = None
    size = 0
    for part in flatten_parts(payload):
        body = part.get("body", {})
        if body.get("attachmentId") != attachment_id:
            continue
        part_filename = part.get("filename")
        if isinstance(part_filename, str) and part_filename.strip():
            filename = part_filename.strip()
        mime_type = part.get("mimeType") or mime_type
        size = body.get("size", 0) or 0
        break

    limit = max_download_bytes()
    if size > limit:
        raise ValueError(f"Attachment exceeds MCP_MAX_DOWNLOAD_BYTES ({limit} bytes).")
    attachment = _execute_request(
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
    )
    raw = attachment.get("data")
    if not raw:
        raise ValueError("Attachment content is empty.")
    # Normalize Gmail URL-safe base64 into standard base64 for host download APIs.
    decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
    if len(decoded) > limit:
        raise ValueError(f"Attachment exceeds MCP_MAX_DOWNLOAD_BYTES ({limit} bytes).")
    blob_base64 = base64.b64encode(decoded).decode("ascii")
    resolved_filename = filename or _fallback_attachment_filename(mime_type)
    return {
        "message_id": message_id,
        "attachment_id": attachment_id,
        "filename": resolved_filename,
        "mime_type": mime_type,
        "size": size,
        "blob_base64": blob_base64,
    }


def _fetch_error_payload(exc: Exception, **details: str) -> dict[str, Any]:
    """Wrap a detail-fetch failure in the AppError structured-error contract."""
    error = AppError(
        code="PROVIDER_ERROR",
        message=str(exc) or exc.__class__.__name__,
        retryable=False,
        details=dict(details),
    )
    return {"error": error.model_dump()}


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


async def build_dashboard_payload_with_progress(
    state: DashboardState, ctx: Context
) -> dict[str, Any]:
    await ctx.report_progress(5, 100, "Preparing dashboard state")
    section_errors: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    unread_count = 0
    messages: list[dict[str, Any]] = []
    unread_message_ids: list[str] = []
    week_state = state.model_copy(update={"view": "week"})

    try:
        await ctx.report_progress(20, 100, "Loading calendar events")
        events = await run_blocking(_fetch_calendar_events, week_state)
    except Exception as exc:  # pragma: no cover - external API
        section_errors["calendar"] = str(exc)

    try:
        await ctx.report_progress(55, 100, "Loading inbox summary")
        unread_count, messages, unread_message_ids = await run_blocking(
            _fetch_inbox_summary,
            state,
        )
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
        weekly_state = weekly_state.model_copy(
            update={"include_weekend": include_weekend_override}
        )
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
        weekly_state = weekly_state.model_copy(
            update={"include_weekend": include_weekend_override}
        )

    await ctx.report_progress(35, 100, "Loading weekly calendar events")
    events = await run_blocking(
        _fetch_calendar_events,
        weekly_state.model_copy(update={"view": "week"}),
    )

    await ctx.report_progress(80, 100, "Building weekly view model")
    model = build_weekly_calendar_view_model(
        anchor_date=weekly_state.anchor_date,
        timezone_name=weekly_state.timezone,
        events=events,
        include_weekend=weekly_state.include_weekend,
    )
    await ctx.report_progress(100, 100, "Weekly calendar ready")
    return model.model_dump(mode="json")


def _execute_request(request: Any) -> Any:
    return request.execute()


# --- Server-issued view handles -------------------------------------------------
#
# The dashboard never uses the transport session or a browser-minted id. A
# launch tool (get_dashboard / get_weekly_calendar_view) called without a handle
# mints a new, isolated view and returns its descriptor twice:
#
# * in the result ``_meta`` under VIEW_META_KEY — the canonical carrier. The MCP
#   Apps host forwards the complete CallToolResult (including ``_meta``) to the
#   view in ui/notifications/tool-result, and ``_meta`` is the spec's channel for
#   metadata that is not render data;
# * as ``view`` in structuredContent, so the model-visible state tools stay
#   usable by a model (which never sees ``_meta``) and hosts that only forward
#   structuredContent to the view still work.
#
# Every later callback passes ``view_handle``; the server resolves and
# authorizes it on each use (see apps.state).

VIEW_HANDLE_DESCRIPTION = (
    "Server-issued dashboard view handle (wsv_...) returned by apps_get_dashboard or "
    "apps_get_weekly_calendar_view in _meta['mcp-google-workspace/view'].handle and "
    "view.handle. Unknown, expired, malformed, or another user's handles fail with "
    "code view_handle_invalid; open a new view then."
)
LAUNCH_HANDLE_DESCRIPTION = (
    "Existing dashboard view handle to reopen. Omit it to open a new, independent "
    "view; the result returns the new handle in view.handle."
)
OPTIONAL_HANDLE_DESCRIPTION = (
    "Dashboard view handle of the calling view, if any. When supplied it is "
    "validated and its idle expiry is extended; detail reads do not change view state."
)
EXPECTED_REVISION_DESCRIPTION = (
    "view.revision the caller last observed. When set, the update is applied only if "
    "the view is still at that revision; otherwise nothing changes and the call fails "
    "with code view_state_conflict carrying the current state. Omit to apply the "
    "change on top of the latest state."
)

ViewHandleArg = Annotated[
    str, Field(description=VIEW_HANDLE_DESCRIPTION, max_length=HANDLE_MAX_LENGTH)
]
LaunchHandleArg = Annotated[
    str | None, Field(description=LAUNCH_HANDLE_DESCRIPTION, max_length=HANDLE_MAX_LENGTH)
]
OptionalHandleArg = Annotated[
    str | None, Field(description=OPTIONAL_HANDLE_DESCRIPTION, max_length=HANDLE_MAX_LENGTH)
]
ExpectedRevisionArg = Annotated[
    int | None, Field(description=EXPECTED_REVISION_DESCRIPTION, ge=1)
]

DASHBOARD_STATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Persisted state of one dashboard view.",
    "properties": {
        "view": {
            "type": "string",
            "enum": ["agenda", "day", "week", "month"],
            "description": "Calendar range shown by the view.",
        },
        "anchor_date": {
            "type": "string",
            "format": "date",
            "description": "Date the displayed range is anchored on (YYYY-MM-DD).",
        },
        "timezone": {"type": "string", "description": "IANA timezone of the view."},
        "selected_calendars": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Calendar IDs shown by the view.",
        },
        "inbox_query": {
            "type": ["string", "null"],
            "description": "Extra Gmail search terms for the inbox summary.",
        },
        "include_weekend": {
            "type": "boolean",
            "description": "Whether weekly ranges include Saturday and Sunday.",
        },
    },
    "required": [
        "view",
        "anchor_date",
        "timezone",
        "selected_calendars",
        "inbox_query",
        "include_weekend",
    ],
    "additionalProperties": False,
}

VIEW_STATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Dashboard view state response",
    "description": "Current state of one dashboard view and its handle/revision.",
    "properties": {"state": DASHBOARD_STATE_SCHEMA, "view": VIEW_DESCRIPTOR_SCHEMA},
    "required": ["state", "view"],
    "additionalProperties": False,
}


def _error_result(envelope: dict[str, Any], meta: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(
        content=[mt.TextContent(type="text", text=render_error_message(envelope))],
        structured_content=envelope,
        meta=meta,
        is_error=True,
    )


def view_error_result(error: ViewHandleError | ViewStateConflict) -> ToolResult:
    """Deterministic isError tool result for handle and revision failures."""
    if isinstance(error, ViewStateConflict):
        current = error.current
        descriptor = current.descriptor()
        return _error_result(
            {
                "code": error.code,
                "message": str(error),
                "retryable": False,
                "retry_after": None,
                "required_action": {
                    "action": "refresh_view",
                    "tool": "apps_get_state",
                    "arguments": {"view_handle": current.handle},
                },
                "provider_status": None,
                "field_errors": [
                    {"field": "expected_revision", "reason": "stale_revision"}
                ],
                "details": {
                    "expected_revision": error.expected_revision,
                    "current_revision": current.revision,
                },
                "state": current.state.model_dump(mode="json"),
                "view": descriptor,
            },
            meta={VIEW_META_KEY: descriptor},
        )
    return _error_result(
        {
            "code": error.code,
            "message": str(error),
            "retryable": False,
            "retry_after": None,
            "required_action": {
                "action": "open_new_view",
                "tool": "apps_get_dashboard",
                "arguments": {},
            },
            "provider_status": None,
            "field_errors": [{"field": "view_handle", "reason": error.reason}],
            "details": {"reason": error.reason},
        }
    )


def view_result(payload: dict[str, Any], view: DashboardView) -> ToolResult:
    """Successful view result: descriptor in structuredContent.view and _meta."""
    descriptor = view.descriptor()
    return ToolResult(
        structured_content={**payload, "view": descriptor},
        meta={VIEW_META_KEY: descriptor},
    )


def _state_result(view: DashboardView) -> ToolResult:
    return view_result({"state": view.state.model_dump(mode="json")}, view)


async def _guarded(operation: Callable[[], Awaitable[ToolResult]]) -> ToolResult:
    try:
        return await operation()
    except (ViewHandleError, ViewStateConflict) as error:
        return view_error_result(error)


def register_tools(server: FastMCP, views: DashboardViewService | None = None) -> None:
    """Register the dashboard tools on ``server`` over one view service."""
    service = views or dashboard_views()

    async def open_view(view_handle: str | None) -> DashboardView:
        if view_handle is not None:
            return await service.resolve(view_handle)
        timezone_name = await resolve_user_timezone()
        return await service.create(
            DashboardState(
                timezone=timezone_name,
                anchor_date=user_now(timezone_name).date(),
            )
        )

    @server.tool(name="get_state", output_schema=VIEW_STATE_OUTPUT_SCHEMA)
    async def apps_get_state(view_handle: ViewHandleArg) -> ToolResult:
        """Get the current state and revision of one dashboard view."""

        async def run() -> ToolResult:
            return _state_result(await service.resolve(view_handle))

        return await _guarded(run)

    @server.tool(name="set_state", output_schema=VIEW_STATE_OUTPUT_SCHEMA)
    async def apps_set_state(
        view_handle: ViewHandleArg,
        expected_revision: ExpectedRevisionArg = None,
        view: Literal["agenda", "day", "week", "month"] | None = None,
        anchor_date: date | None = None,
        timezone: str | None = None,
        selected_calendars: list[str] | None = None,
        inbox_query: str | None = None,
        include_weekend: bool | None = None,
    ) -> ToolResult:
        """Replace the state of one dashboard view; omitted fields reset to defaults."""

        async def run() -> ToolResult:
            current = await service.resolve(view_handle)
            effective_timezone = timezone or current.state.timezone
            fields: dict[str, Any] = {
                "timezone": effective_timezone,
                "anchor_date": anchor_date or user_now(effective_timezone).date(),
            }
            if view is not None:
                fields["view"] = view
            if selected_calendars is not None:
                fields["selected_calendars"] = selected_calendars
            if inbox_query is not None:
                fields["inbox_query"] = inbox_query
            if include_weekend is not None:
                fields["include_weekend"] = include_weekend
            replacement = DashboardState(**fields)
            updated = await service.update(
                view_handle,
                lambda _current: replacement,
                expected_revision=expected_revision,
            )
            LOGGER.debug("Dashboard view state replaced (revision %s).", updated.revision)
            return _state_result(updated)

        return await _guarded(run)

    @server.tool(name="patch_state", output_schema=VIEW_STATE_OUTPUT_SCHEMA)
    async def apps_patch_state(
        view_handle: ViewHandleArg,
        expected_revision: ExpectedRevisionArg = None,
        view: Literal["agenda", "day", "week", "month"] | None = None,
        anchor_date: date | None = None,
        timezone: str | None = None,
        selected_calendars: list[str] | None = None,
        inbox_query: str | None = None,
        include_weekend: bool | None = None,
    ) -> ToolResult:
        """Patch selected state fields of one dashboard view."""
        request = DashboardStatePatch(
            view=view,
            anchor_date=anchor_date,
            timezone=timezone,
            selected_calendars=selected_calendars,
            inbox_query=inbox_query,
            include_weekend=include_weekend,
        )

        async def run() -> ToolResult:
            return _state_result(
                await service.update(
                    view_handle,
                    lambda current: patch_state(current, request),
                    expected_revision=expected_revision,
                )
            )

        return await _guarded(run)

    @server.tool(name="next_range", output_schema=VIEW_STATE_OUTPUT_SCHEMA)
    async def apps_next_range(
        view_handle: ViewHandleArg,
        expected_revision: ExpectedRevisionArg = None,
    ) -> ToolResult:
        """Move one dashboard view's anchor date to the next range of its current view."""

        async def run() -> ToolResult:
            return _state_result(
                await service.update(view_handle, next_range, expected_revision=expected_revision)
            )

        return await _guarded(run)

    @server.tool(name="prev_range", output_schema=VIEW_STATE_OUTPUT_SCHEMA)
    async def apps_prev_range(
        view_handle: ViewHandleArg,
        expected_revision: ExpectedRevisionArg = None,
    ) -> ToolResult:
        """Move one dashboard view's anchor date to the previous range of its current view."""

        async def run() -> ToolResult:
            return _state_result(
                await service.update(view_handle, prev_range, expected_revision=expected_revision)
            )

        return await _guarded(run)

    @server.tool(name="today", output_schema=VIEW_STATE_OUTPUT_SCHEMA)
    async def apps_today(
        view_handle: ViewHandleArg,
        expected_revision: ExpectedRevisionArg = None,
    ) -> ToolResult:
        """Reset one dashboard view's anchor date to today in the view's timezone."""

        async def run() -> ToolResult:
            return _state_result(
                await service.update(
                    view_handle,
                    lambda current: current.model_copy(
                        update={"anchor_date": user_now(current.timezone).date()}
                    ),
                    expected_revision=expected_revision,
                )
            )

        return await _guarded(run)

    @server.tool(
        name="get_dashboard",
        meta={
            "ui": {"resourceUri": "ui://apps/dashboard-ui"},
            "ui/resourceUri": "ui://apps/dashboard-ui",
        },
    )
    async def apps_get_dashboard(
        view_handle: LaunchHandleArg = None,
        date_override: date | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | ToolResult:
        """Open (or refresh) a workspace dashboard view from calendar and inbox data."""

        async def run() -> ToolResult:
            view = await open_view(view_handle)
            state = view.state
            if date_override is not None:
                state = state.model_copy(update={"anchor_date": date_override})
            if ctx is not None:
                payload = await build_dashboard_payload_with_progress(state, ctx)
            else:
                payload = await run_blocking(build_dashboard_payload, state)
            return view_result(payload, view)

        return await _guarded(run)

    @server.tool(
        name="get_weekly_calendar_view",
        meta={
            "ui": {"resourceUri": "ui://apps/dashboard-ui"},
            "ui/resourceUri": "ui://apps/dashboard-ui",
        },
    )
    async def apps_get_weekly_calendar_view(
        view_handle: LaunchHandleArg = None,
        date_override: date | None = None,
        include_weekend: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | ToolResult:
        """Open (or refresh) a Google Calendar-like weekly view (columns per day)."""

        async def run() -> ToolResult:
            view = await open_view(view_handle)
            if ctx is not None:
                payload = await build_weekly_calendar_payload_with_progress(
                    view.state,
                    ctx=ctx,
                    date_override=date_override,
                    include_weekend_override=include_weekend,
                )
            else:
                payload = await run_blocking(
                    build_weekly_calendar_payload,
                    view.state,
                    date_override=date_override,
                    include_weekend_override=include_weekend,
                )
            return view_result(payload, view)

        return await _guarded(run)

    async def touch(view_handle: str | None) -> ToolResult | None:
        if view_handle is None:
            return None
        try:
            await service.resolve(view_handle)
        except ViewHandleError as error:
            return view_error_result(error)
        return None

    @server.tool(name="get_event_detail")
    async def apps_get_event_detail(
        event_id: str,
        calendar_id: str = "primary",
        view_handle: OptionalHandleArg = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | ToolResult:
        """Return full event details (attendees, location, description, conference)."""
        if (rejected := await touch(view_handle)) is not None:
            return rejected
        account_timezone = await resolve_user_timezone()
        if ctx is not None:
            await ctx.report_progress(20, 100, "Loading event details")
        try:
            payload = await run_blocking(
                _fetch_event_detail,
                calendar_id,
                event_id,
                account_timezone,
            )
        except Exception as exc:
            return _fetch_error_payload(exc, event_id=event_id, calendar_id=calendar_id)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Event details ready")
        return payload

    @server.tool(name="get_email_detail")
    async def apps_get_email_detail(
        message_id: str,
        view_handle: OptionalHandleArg = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | ToolResult:
        """Return full email details (headers + body)."""
        if (rejected := await touch(view_handle)) is not None:
            return rejected
        account_timezone = await resolve_user_timezone()
        if ctx is not None:
            await ctx.report_progress(20, 100, "Loading email details")
        try:
            payload = await run_blocking(_fetch_email_detail, message_id, account_timezone)
        except Exception as exc:
            return _fetch_error_payload(exc, message_id=message_id)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Email details ready")
        return payload

    @server.tool(
        name="get_email_attachment",
        meta={"ui": {"visibility": ["app"]}},
    )
    async def apps_get_email_attachment(
        message_id: str,
        attachment_id: str,
        view_handle: OptionalHandleArg = None,
        ctx: Context | None = None,
    ) -> dict[str, Any] | ToolResult:
        """Return attachment content (base64) for one Gmail message attachment."""
        if (rejected := await touch(view_handle)) is not None:
            return rejected
        if ctx is not None:
            await ctx.report_progress(20, 100, "Loading attachment data")
        try:
            payload = await run_blocking(_fetch_email_attachment, message_id, attachment_id)
        except Exception as exc:
            return _fetch_error_payload(exc, message_id=message_id, attachment_id=attachment_id)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Attachment ready")
        return payload

