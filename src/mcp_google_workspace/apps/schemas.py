"""Pydantic schemas for apps dashboard and briefing contracts."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

DashboardView = Literal["agenda", "day", "week", "month"]


class ToolRequestModel(BaseModel):
    """Base input model for MCP tools expecting object payloads."""

    model_config = {
        "json_schema_extra": {
            "description": (
                "Pass this as a JSON object payload to the tool. "
                "Do not pass a raw string for the full request."
            )
        }
    }


class DashboardState(BaseModel):
    session_id: str = Field(default_factory=lambda: f"session-{uuid4()}")
    view: DashboardView = Field(default="week")
    anchor_date: dt.date = Field(default_factory=dt.date.today)
    timezone: str = Field(default="UTC")
    selected_calendars: list[str] = Field(default_factory=lambda: ["primary"])
    inbox_query: str | None = Field(default=None)
    include_weekend: bool = Field(default=True)


class DashboardStatePatch(ToolRequestModel):
    view: DashboardView | None = None
    anchor_date: dt.date | None = None
    timezone: str | None = None
    selected_calendars: list[str] | None = None
    inbox_query: str | None = None
    include_weekend: bool | None = None


class DashboardCardAction(BaseModel):
    id: str
    label: str
    tool_name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DashboardCard(BaseModel):
    id: str
    title: str
    card_type: Literal["calendar", "inbox", "prep", "briefing", "meta", "error"]
    summary: str
    fallback_text: str
    data: dict[str, Any] = Field(default_factory=dict)
    actions: list[DashboardCardAction] = Field(default_factory=list)


class DashboardSection(BaseModel):
    id: str
    title: str
    cards: list[DashboardCard] = Field(default_factory=list)
    fallback_text: str = ""


class DashboardViewModel(BaseModel):
    title: str
    generated_at_utc: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    state: DashboardState
    sections: list[DashboardSection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    section_errors: dict[str, str] = Field(default_factory=dict)


class MorningBriefingRequest(ToolRequestModel):
    session_id: str | None = Field(default=None, description="Optional apps session ID for state continuity.")
    date: dt.date | None = Field(default=None, description="Briefing date (YYYY-MM-DD), defaults to today.")
    timezone: str | None = Field(default=None, description="IANA timezone override, defaults to state/user timezone.")
    max_priorities: int = Field(default=5, ge=1, le=10, description="Maximum priority items to include.")
    max_quick_wins: int = Field(default=5, ge=1, le=10, description="Maximum quick-win actions to include.")
    include_inbox: bool = Field(default=True, description="Whether to include inbox-driven briefing items.")


class BriefingPriority(BaseModel):
    title: str
    reason: str
    priority: Literal["high", "medium", "low"] = "medium"


class BriefingRisk(BaseModel):
    title: str
    detail: str
    severity: Literal["high", "medium", "low"] = "low"


class BriefingAction(BaseModel):
    title: str
    detail: str
    tool_name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MorningBriefingViewModel(BaseModel):
    date: dt.date
    timezone: str
    summary: str
    priorities: list[BriefingPriority] = Field(default_factory=list)
    conflicts: list[BriefingRisk] = Field(default_factory=list)
    prep_actions: list[BriefingAction] = Field(default_factory=list)
    quick_wins: list[BriefingAction] = Field(default_factory=list)
    fallback_text: str


class WeeklyCalendarEvent(BaseModel):
    event_id: str | None = None
    calendar_id: str | None = None
    title: str
    start: str
    end: str
    all_day: bool = False
    status: str = "confirmed"


class WeeklyCalendarDay(BaseModel):
    date: dt.date
    day_label: str
    is_today: bool = False
    all_day_events: list[WeeklyCalendarEvent] = Field(default_factory=list)
    timed_events: list[WeeklyCalendarEvent] = Field(default_factory=list)


class WeeklyCalendarViewModel(BaseModel):
    week_start: dt.date
    week_end: dt.date
    timezone: str
    total_events: int
    days: list[WeeklyCalendarDay] = Field(default_factory=list)
    fallback_text: str


class AppError(BaseModel):
    code: Literal[
        "AUTH_REQUIRED",
        "FORBIDDEN",
        "NOT_FOUND",
        "VALIDATION_ERROR",
        "CONFLICT",
        "RATE_LIMITED",
        "PROVIDER_ERROR",
    ]
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class FindMeetingSlotsRequest(ToolRequestModel):
    participants: list[str] = Field(default_factory=lambda: ["primary"], description="Calendar IDs/emails to include in availability intersection.")
    time_min: str = Field(description="RFC3339 start datetime for search window.")
    time_max: str = Field(description="RFC3339 end datetime for search window.")
    slot_duration_minutes: int = Field(default=30, ge=5, le=480, description="Desired meeting slot duration in minutes.")
    granularity_minutes: int = Field(default=15, ge=5, le=240, description="Step size between candidate slot starts in minutes.")
    max_results: int = Field(default=10, ge=1, le=100, description="Maximum number of slot suggestions to return.")
    time_zone: str = Field(default="UTC", description="IANA timezone for rendering/normalizing slots.")
    working_hours_start: str = Field(default="08:00", description="Daily working-hours start in HH:MM 24h format.")
    working_hours_end: str = Field(default="17:00", description="Daily working-hours end in HH:MM 24h format.")

    @field_validator("working_hours_start", "working_hours_end")
    @classmethod
    def _validate_hhmm(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("must be HH:MM format")
        hours, minutes = value.split(":")
        if not (hours.isdigit() and minutes.isdigit()):
            raise ValueError("must be HH:MM format")
        hh = int(hours)
        mm = int(minutes)
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            raise ValueError("must be valid 24h time")
        return value


class CreateMeetingFromSlotRequest(ToolRequestModel):
    session_id: str | None = Field(default=None, description="Optional apps session ID for state continuity.")
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    title: str = Field(description="Meeting title/summary.")
    start: str = Field(description="RFC3339 start datetime.")
    end: str = Field(description="RFC3339 end datetime.")
    timezone: str = Field(default="UTC", description="IANA timezone for start/end rendering.")
    description: str | None = Field(default=None, description="Optional meeting description.")
    attendees: list[str] = Field(default_factory=list, description="Attendee email list.")
    create_conference: bool = Field(default=True, description="Whether to request conference data (e.g., Meet).")
    idempotency_key: str = Field(description="Stable key to prevent duplicate creates on retry.")


class RescheduleMeetingRequest(ToolRequestModel):
    session_id: str | None = Field(default=None, description="Optional apps session ID for state continuity.")
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    event_id: str = Field(description="Existing calendar event ID to reschedule.")
    start: str = Field(description="New RFC3339 start datetime.")
    end: str = Field(description="New RFC3339 end datetime.")
    timezone: str = Field(default="UTC", description="IANA timezone for start/end rendering.")
    idempotency_key: str = Field(description="Stable key to prevent duplicate reschedules on retry.")


class CancelMeetingRequest(ToolRequestModel):
    session_id: str | None = Field(default=None, description="Optional apps session ID for state continuity.")
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    event_id: str = Field(description="Existing calendar event ID to cancel.")
    confirm: bool = Field(default=False, description="Interactive confirmation flag for destructive action.")
    send_updates: str | None = Field(default=None, description="Guest update mode (all, externalOnly, none).")
    idempotency_key: str = Field(description="Stable key to prevent duplicate cancels on retry.")


class AppActionResult(BaseModel):
    status: Literal["ok", "cancelled", "conflict", "error"]
    message: str
    event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
