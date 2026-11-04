"""Pydantic models for Calendar tools."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import EmailStr, Field, model_validator

from ..common.request_model import ToolRequestModel

# Calendar API events.* sendUpdates values.
SendUpdates = Literal["all", "externalOnly", "none"]

# Google Calendar accepts reminders from 0 minutes to 4 weeks before the start
# and at most 5 reminder overrides per event.
ReminderMinutes = Annotated[int, Field(ge=0, le=40320)]
MAX_REMINDERS = 5


class ListEventsRequest(ToolRequestModel):
    calendar_id: str = Field(
        default="primary",
        description="Calendar ID, usually 'primary'.",
    )
    time_min: str | None = Field(
        default=None,
        description=(
            "RFC3339 lower bound for an event's end time (events ending after it). Example: "
            "'2026-03-01T00:00:00Z'."
        ),
    )
    time_max: str | None = Field(
        default=None,
        description=(
            "RFC3339 upper bound for an event's start time (events starting before it). Example: "
            "'2026-03-08T00:00:00Z'."
        ),
    )
    max_results: int = Field(default=25, ge=1, le=2500, description="Maximum events to return.")
    single_events: bool = Field(default=True, description="Expand recurring events into single instances.")
    order_by: Literal["startTime", "updated"] = Field(default="startTime", description="Sort order.")
    range_preset: Literal["today", "tomorrow", "this_week", "next_7_days"] | None = Field(
        default=None,
        description=(
            "Optional relative window preset. If provided, fills missing time_min/time_max "
            "using the selected preset."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "calendar_id": "primary",
                    "time_min": "2026-03-01T00:00:00Z",
                    "time_max": "2026-03-08T00:00:00Z",
                    "max_results": 50,
                    "single_events": True,
                    "order_by": "startTime",
                }
            ]
        }
    }


class ReadEventsRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="One to 100 event IDs from the same calendar to read in detail.",
    )
    time_zone: str | None = Field(default=None, description="Optional timezone for response rendering.")
    max_attendees: int | None = Field(default=None, ge=1, description="Optional attendee cap in response payload.")


class RespondToEventRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar containing the event.")
    event_id: str = Field(description="Event whose attendee response should be updated.")
    response_status: Literal["accepted", "tentative", "declined"] = Field(
        description="Response to set for the authenticated attendee."
    )
    send_updates: SendUpdates | None = Field(
        default="all",
        description="Guest notification mode.",
    )


class EventAttachmentInput(ToolRequestModel):
    file_url: str = Field(
        description=(
            "Link to the file; for a Drive file use the same form as its Drive alternateLink, "
            "e.g. 'https://drive.google.com/file/d/<file_id>/view'."
        )
    )
    title: str | None = Field(default=None, description="Name shown for the attachment on the event.")
    mime_type: str | None = Field(
        default=None, description="MIME type of the file, e.g. 'application/pdf'."
    )
    icon_link: str | None = Field(
        default=None, description="URL of an icon shown next to the attachment."
    )
    file_id: str | None = Field(
        default=None,
        description=(
            "Drive file ID of the file; lets calendar_set_event_attachments remove it by file_id "
            "and drive_download_file save it."
        ),
    )

    def to_api(self) -> dict[str, str]:
        payload: dict[str, str] = {"fileUrl": self.file_url}
        if self.title is not None:
            payload["title"] = self.title
        if self.mime_type is not None:
            payload["mimeType"] = self.mime_type
        if self.icon_link is not None:
            payload["iconLink"] = self.icon_link
        if self.file_id is not None:
            payload["fileId"] = self.file_id
        return payload


class CreateEventRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Stable key that makes retried event creation return the original event.",
    )
    summary: str = Field(description="Event title/summary.")
    start_datetime: str = Field(description="Event start datetime in RFC3339 or ISO format.")
    end_datetime: str = Field(description="Event end datetime in RFC3339 or ISO format.")
    timezone: str | None = Field(default=None, description="IANA timezone name for start/end datetime.")
    description: str | None = Field(default=None, description="Optional event description.")
    location: str | None = Field(default=None, description="Optional event location.")
    color_id: str | None = Field(default=None, description="Optional Google Calendar color ID.")
    visibility: Literal["default", "public", "private", "confidential"] | None = Field(
        default="default",
        description="Optional event visibility mode.",
    )
    transparency: Literal["opaque", "transparent"] | None = Field(
        default="opaque",
        description="Whether the event blocks time on the calendar.",
    )
    add_meet_link: bool = Field(default=False, description="Request a new Google Meet link for the event.")
    attendees: list["EventAttendeeInput"] | None = Field(default=None, description="Event attendees.")
    attachments: list[EventAttachmentInput] | None = Field(
        default=None,
        description="Optional event attachments metadata (up to 25).",
        max_length=25,
    )
    supports_attachments: bool = Field(
        default=True,
        description="Whether client supports event attachments (sets supportsAttachments query param).",
    )
    send_updates: SendUpdates | None = Field(
        default=None,
        description="Guest notification mode for create (all, externalOnly, none).",
    )
    reminder_minutes: list[ReminderMinutes] | None = Field(
        default=None,
        max_length=MAX_REMINDERS,
        description="Popup reminders, in minutes before the start; empty means none, omitted means calendar defaults.",
    )
    recurrence: list[str] | None = Field(default=None, description="Recurrence rules (RRULE strings).")
    on_conflict: Literal["fail", "suggest_next_slot"] = Field(
        default="fail",
        description="Conflict strategy when requested time overlaps existing events.",
    )


class UpdateEventRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    event_id: str = Field(description="Calendar event ID to update.")
    summary: str | None = Field(default=None, description="Updated title/summary.")
    start_datetime: str | None = Field(default=None, description="Updated start datetime.")
    end_datetime: str | None = Field(default=None, description="Updated end datetime.")
    timezone: str | None = Field(default=None, description="IANA timezone applied to updated start/end.")
    description: str | None = Field(default=None, description="Updated event description.")
    location: str | None = Field(default=None, description="Updated event location.")
    color_id: str | None = Field(default=None, description="Optional Google Calendar color ID.")
    visibility: Literal["default", "public", "private", "confidential"] | None = Field(
        default=None,
        description="Optional event visibility mode.",
    )
    transparency: Literal["opaque", "transparent"] | None = Field(
        default=None,
        description="Whether the event blocks time on the calendar.",
    )
    add_meet_link: bool = Field(default=False, description="Request a new Google Meet link for the event.")
    attendees: list["EventAttendeeInput"] | None = Field(default=None, description="Replacement attendee list.")
    attachments: list[EventAttachmentInput] | None = Field(
        default=None,
        description="Replacement event attachments metadata (up to 25).",
        max_length=25,
    )
    supports_attachments: bool = Field(
        default=True,
        description="Whether client supports event attachments (sets supportsAttachments query param).",
    )
    reminder_minutes: list[ReminderMinutes] | None = Field(
        default=None,
        max_length=MAX_REMINDERS,
        description="Replacement popup reminders, in minutes before the start; empty removes all, omitted keeps them.",
    )
    recurrence: list[str] | None = Field(default=None, description="Updated recurrence rules.")
    send_updates: SendUpdates | None = Field(default=None, description="Guest notification mode for update.")
    on_conflict: Literal["fail", "suggest_next_slot"] = Field(
        default="fail",
        description="Conflict strategy when updated time overlaps existing events.",
    )


class DeleteEventRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    event_id: str = Field(description="Calendar event ID to delete.")
    send_updates: SendUpdates | None = Field(default=None, description="Guest notification mode for deletion.")
    force: bool = Field(
        default=False,
        description=(
            "Skip the interactive confirmation when true. Default false: the deletion "
            "is confirmed with the user first (clients that cannot confirm get a "
            "confirmation_required error and nothing is deleted)."
        ),
    )


class EventAttendeeInput(ToolRequestModel):
    email: EmailStr = Field(description="Guest's email address; the invitation goes here.")
    display_name: str | None = Field(
        default=None, alias="displayName", description="Name shown for the guest on the event."
    )
    optional: bool = Field(
        default=False, description="True marks the guest as optional rather than required."
    )
    comment: str | None = Field(default=None, description="The guest's note accompanying their response.")
    additional_guests: int = Field(
        default=0,
        alias="additionalGuests",
        ge=0,
        le=100,
        description="Number of extra people the guest brings, 0 to 100.",
    )


CreateEventRequest.model_rebuild()
UpdateEventRequest.model_rebuild()


class FindFreeTimeRequest(ToolRequestModel):
    participants: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Calendar IDs or attendee email addresses whose busy times are checked.",
    )
    time_min: str = Field(description="Start of the window: RFC3339, naive local datetime or date.")
    time_max: str = Field(description="End of the window, same formats as time_min.")
    time_zone: str | None = Field(
        default=None,
        description="IANA time zone for naive inputs, working hours and returned times.",
    )
    slot_duration_minutes: int = Field(
        default=30,
        ge=5,
        le=480,
        description="Desired duration for each suggested meeting slot in minutes.",
    )
    granularity_minutes: int = Field(
        default=15,
        ge=5,
        le=240,
        description="Step size used to generate candidate slots inside each free range.",
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of slot suggestions returned.",
    )
    working_hours_start: str = Field(
        default="08:00",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="Daily working-hours start in HH:MM (24h). Default is 08:00.",
    )
    working_hours_end: str = Field(
        default="17:00",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="Daily working-hours end in HH:MM (24h). Default is 17:00.",
    )


class EventAttachmentSelector(ToolRequestModel):
    file_url: str | None = Field(
        default=None,
        description="Match attachments whose file_url equals this, as listed by calendar_read_events.",
    )
    file_id: str | None = Field(
        default=None,
        description="Match attachments whose Drive file_id equals this, as listed by calendar_read_events.",
    )

    @model_validator(mode="after")
    def _require_a_selector(self) -> "EventAttachmentSelector":
        if not self.file_url and not self.file_id:
            raise ValueError("Each remove entry needs file_url, file_id or both.")
        return self


class SetEventAttachmentsRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_id: str = Field(description="Event ID to mutate.")
    add: list[EventAttachmentInput] | None = Field(
        default=None, max_length=25, description="Attachments to add (deduplicated by file_url)."
    )
    remove: list[EventAttachmentSelector] | None = Field(
        default=None, description="Selectors of attachments to remove."
    )
    send_updates: SendUpdates | None = Field(default=None, description="Guest notification mode for update (all, externalOnly, none).")

    @model_validator(mode="after")
    def _require_a_change(self) -> "SetEventAttachmentsRequest":
        if not self.add and not self.remove:
            raise ValueError("Provide at least one attachment in add or remove.")
        return self
