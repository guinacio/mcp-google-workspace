"""Pydantic schemas for apps dashboard contracts."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..common.request_model import ToolRequestModel

DashboardView = Literal["agenda", "day", "week", "month"]


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
    card_type: Literal["calendar", "inbox", "prep", "meta", "error"]
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


class WeeklyCalendarEvent(BaseModel):
    event_id: str | None = None
    calendar_id: str | None = None
    title: str
    start: str
    end: str
    all_day: bool = False
    status: str = "confirmed"
    attendee_response_status: Literal["needsAction", "declined", "tentative", "accepted"] | None = None
    location: str | None = None
    description_snippet: str | None = None
    attendee_count: int | None = None
    has_conference: bool = False
    color_id: str | None = None


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


class EventDetailAttendee(BaseModel):
    email: str
    display_name: str | None = None
    optional: bool = False
    organizer: bool = False
    self: bool = False
    response_status: str | None = None


class EventDetailAttachment(BaseModel):
    title: str
    file_url: str | None = None
    file_id: str | None = None
    mime_type: str | None = None
    icon_link: str | None = None


class EventDetailViewModel(BaseModel):
    event_id: str
    calendar_id: str
    title: str
    start: str
    end: str
    timezone: str | None = None
    status: str = "confirmed"
    location: str | None = None
    description: str | None = None
    conference_link: str | None = None
    conference_provider: str | None = None
    organizer_email: str | None = None
    organizer_name: str | None = None
    self_response_status: Literal["needsAction", "declined", "tentative", "accepted"] | None = None
    attendees: list[EventDetailAttendee] = Field(default_factory=list)
    attachments: list[EventDetailAttachment] = Field(default_factory=list)


class EmailDetailViewModel(BaseModel):
    message_id: str
    thread_id: str | None = None
    subject: str
    from_value: str
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    date: str | None = None
    snippet: str | None = None
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    is_unread: bool = False


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
