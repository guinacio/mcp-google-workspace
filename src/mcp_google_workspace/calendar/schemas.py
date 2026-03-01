"""Pydantic models for Calendar tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class ListEventsRequest(ToolRequestModel):
    calendar_id: str = Field(
        default="primary",
        description="Calendar ID, usually 'primary'.",
    )
    time_min: str | None = Field(
        default=None,
        description=(
            "RFC3339 lower bound for event start time. Example: "
            "'2026-03-01T00:00:00Z'."
        ),
    )
    time_max: str | None = Field(
        default=None,
        description=(
            "RFC3339 upper bound for event start time. Example: "
            "'2026-03-08T00:00:00Z'."
        ),
    )
    max_results: int = Field(default=25, ge=1, le=2500, description="Maximum events to return.")
    single_events: bool = Field(default=True, description="Expand recurring events into single instances.")
    order_by: str = Field(default="startTime", description="Sort order, typically 'startTime'.")

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


class GetEventRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_id: str = Field(description="Calendar event ID to fetch.")
    time_zone: str | None = Field(default=None, description="Optional timezone for response rendering.")
    max_attendees: int | None = Field(default=None, ge=1, description="Optional attendee cap in response payload.")


class EventAttachmentInput(BaseModel):
    file_url: str = Field(description="Attachment file URL (for Drive use file alternateLink format).")
    title: str | None = Field(default=None, description="Display title of the attachment.")
    mime_type: str | None = Field(default=None, description="Attachment MIME type.")
    icon_link: str | None = Field(default=None, description="Optional icon URL for the attachment.")
    file_id: str | None = Field(default=None, description="Drive file ID if available.")

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
    conference_data: dict[str, Any] | None = Field(
        default=None,
        description="Optional Google conference data payload (for Meet links).",
    )
    attendees: list[dict[str, Any]] | None = Field(default=None, description="Attendee objects (email/displayName/etc).")
    attachments: list[EventAttachmentInput] | None = Field(
        default=None,
        description="Optional event attachments metadata (up to 25).",
        max_length=25,
    )
    supports_attachments: bool = Field(
        default=True,
        description="Whether client supports event attachments (sets supportsAttachments query param).",
    )
    send_updates: str | None = Field(
        default=None,
        description="Guest notification mode for create (all, externalOnly, none).",
    )
    reminders: dict[str, Any] | None = Field(default=None, description="Reminder override configuration.")
    recurrence: list[str] | None = Field(default=None, description="Recurrence rules (RRULE strings).")


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
    conference_data: dict[str, Any] | None = Field(
        default=None,
        description="Optional Google conference data payload (for Meet links).",
    )
    attendees: list[dict[str, Any]] | None = Field(default=None, description="Replacement attendee list.")
    attachments: list[EventAttachmentInput] | None = Field(
        default=None,
        description="Replacement event attachments metadata (up to 25).",
        max_length=25,
    )
    supports_attachments: bool = Field(
        default=True,
        description="Whether client supports event attachments (sets supportsAttachments query param).",
    )
    reminders: dict[str, Any] | None = Field(default=None, description="Updated reminder configuration.")
    recurrence: list[str] | None = Field(default=None, description="Updated recurrence rules.")
    send_updates: str | None = Field(default=None, description="Guest notification mode for update.")


class DeleteEventRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Target calendar ID.")
    event_id: str = Field(description="Calendar event ID to delete.")
    send_updates: str | None = Field(default=None, description="Guest notification mode for deletion.")
    force: bool = Field(default=False, description="Skip interactive confirmation when true.")


class FreeBusyRequest(ToolRequestModel):
    timeMin: str = Field(description="RFC3339 start of availability window.")
    timeMax: str = Field(description="RFC3339 end of availability window.")
    items: list[dict[str, str]] = Field(description="Calendars to query, each with an id field.")
    timeZone: str | None = Field(default=None, description="Timezone for response rendering.")


class FindCommonFreeSlotsRequest(ToolRequestModel):
    participants: list[str] = Field(
        min_length=1,
        description="List of participant calendar IDs/emails to include in availability search.",
    )
    time_min: str = Field(description="RFC3339 start time for the scheduling window.")
    time_max: str = Field(description="RFC3339 end time for the scheduling window.")
    slot_duration_minutes: int = Field(
        default=30,
        ge=5,
        le=480,
        description="Desired duration for each suggested meeting slot.",
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
    time_zone: str | None = Field(
        default=None,
        description="Optional timezone for freebusy query response rendering.",
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


class ListEventAttachmentsRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_id: str = Field(description="Event ID whose attachments should be listed.")


class AddEventAttachmentRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_id: str = Field(description="Event ID to mutate.")
    attachment: EventAttachmentInput = Field(description="Attachment metadata to add.")
    send_updates: str | None = Field(default=None, description="Guest notification mode for update (all, externalOnly, none).")


class RemoveEventAttachmentRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_id: str = Field(description="Event ID to mutate.")
    file_url: str | None = Field(default=None, description="Attachment fileUrl to remove.")
    file_id: str | None = Field(default=None, description="Attachment fileId to remove.")
    send_updates: str | None = Field(default=None, description="Guest notification mode for update (all, externalOnly, none).")


class DownloadEventAttachmentRequest(ToolRequestModel):
    calendar_id: str = Field(default="primary", description="Calendar ID containing the event.")
    event_id: str = Field(description="Event ID that contains the attachment.")
    file_url: str | None = Field(default=None, description="Attachment fileUrl to download (preferred selector).")
    file_id: str | None = Field(default=None, description="Drive file ID to download.")
    output_path: str = Field(description="Destination path for downloaded/exported content.")
    export_mime_type: str | None = Field(
        default=None,
        description="Export MIME type for Google-native docs (defaults to PDF where supported).",
    )
    overwrite: bool = Field(default=False, description="Overwrite output file if it already exists.")
