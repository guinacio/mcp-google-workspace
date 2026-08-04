"""FastMCP Meet tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import tool_error_payload
from ..common.timezone import resolve_user_timezone
from .client import meet_service, normalize_conference_record_name, normalize_space_name
from .presentation import conference_envelope, participant_envelope, recording_envelope, transcript_envelope
from .schemas import (
    CreateSpaceRequest,
    EndActiveConferenceRequest,
    GetConferenceRecordRequest,
    GetSpaceRequest,
    ListConferenceParticipantsRequest,
    ListConferenceRecordingsRequest,
    ListConferenceRecordsRequest,
    ListConferenceTranscriptsRequest,
    UpdateSpaceRequest,
)


def create_space_payload(request: CreateSpaceRequest) -> dict[str, Any]:
    service = meet_service()
    body = {"config": request.config} if request.config is not None else {}
    return service.spaces().create(body=body).execute()


def get_space_payload(request: GetSpaceRequest) -> dict[str, Any]:
    service = meet_service()
    return service.spaces().get(name=normalize_space_name(request.space_name)).execute()


def update_space_payload(request: UpdateSpaceRequest) -> dict[str, Any]:
    service = meet_service()
    space_name = normalize_space_name(request.space_name)
    return service.spaces().patch(
        name=space_name,
        updateMask=request.update_mask or "config",
        body={"name": space_name, "config": request.config},
    ).execute()


def end_active_conference_payload(request: EndActiveConferenceRequest) -> dict[str, Any]:
    service = meet_service()
    return service.spaces().endActiveConference(name=normalize_space_name(request.space_name), body={}).execute()


def list_conference_records_payload(request: ListConferenceRecordsRequest) -> dict[str, Any]:
    service = meet_service()
    return service.conferenceRecords().list(
        pageSize=request.page_size,
        pageToken=request.page_token,
        filter=request.filter,
    ).execute()


def get_conference_record_payload(request: GetConferenceRecordRequest) -> dict[str, Any]:
    service = meet_service()
    return service.conferenceRecords().get(name=normalize_conference_record_name(request.conference_record_name)).execute()


def list_conference_participants_payload(request: ListConferenceParticipantsRequest) -> dict[str, Any]:
    service = meet_service()
    return service.conferenceRecords().participants().list(
        parent=normalize_conference_record_name(request.conference_record_name),
        pageSize=request.page_size,
        pageToken=request.page_token,
        filter=request.filter,
    ).execute()


def list_conference_recordings_payload(request: ListConferenceRecordingsRequest) -> dict[str, Any]:
    service = meet_service()
    return service.conferenceRecords().recordings().list(
        parent=normalize_conference_record_name(request.conference_record_name),
        pageSize=request.page_size,
        pageToken=request.page_token,
    ).execute()


def list_conference_transcripts_payload(request: ListConferenceTranscriptsRequest) -> dict[str, Any]:
    service = meet_service()
    return service.conferenceRecords().transcripts().list(
        parent=normalize_conference_record_name(request.conference_record_name),
        pageSize=request.page_size,
        pageToken=request.page_token,
    ).execute()


def register_tools(server: FastMCP) -> None:
    @server.tool(name="create_space")
    def create_space(config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a Google Meet space with an optional Meet API configuration.

        ``config`` accepts Meet SpaceConfig fields. Returns the created Space resource,
        including its name and meeting URI, or a structured provider error.
        """
        try:
            return create_space_payload(CreateSpaceRequest(config=config))
        except HttpError as exc:
            return tool_error_payload(exc, operation="create_space")

    @server.tool(name="get_space")
    def get_space(space_name: str) -> dict[str, Any]:
        """Get a Meet space by bare code or canonical ``spaces/...`` name.

        Space names come from ``create_space``. Returns the Space resource or a
        structured provider error.
        """
        try:
            return get_space_payload(GetSpaceRequest(space_name=space_name))
        except HttpError as exc:
            return tool_error_payload(exc, space_name=normalize_space_name(space_name))

    @server.tool(name="update_space")
    def update_space(space_name: str, config: dict[str, Any], update_mask: str | None = None) -> dict[str, Any]:
        """Update selected configuration fields on an existing Meet space.

        ``space_name`` comes from ``create_space``; ``config`` contains replacement
        values and ``update_mask`` selects fields (default ``config``). Returns the
        updated Space resource or a structured provider error.
        """
        try:
            return update_space_payload(
                UpdateSpaceRequest(
                    space_name=space_name,
                    config=config,
                    update_mask=update_mask,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, space_name=normalize_space_name(space_name))

    @server.tool(name="end_active_conference")
    def end_active_conference(space_name: str) -> dict[str, Any]:
        """End the active conference in a Meet space, disconnecting participants.

        ``space_name`` comes from ``create_space``. Returns the Meet API operation
        response or a structured provider error.
        """
        try:
            return end_active_conference_payload(EndActiveConferenceRequest(space_name=space_name))
        except HttpError as exc:
            return tool_error_payload(exc, space_name=normalize_space_name(space_name))

    @server.tool(name="list_conference_records")
    async def list_conference_records(
        page_size: int = 50,
        page_token: str | None = None,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """List conference records with optional Meet API filtering and pagination.

        Returns normalized records, count, account timezone, and the next page token;
        record names feed the participant, recording, and transcript tools. API failures
        return a structured provider error.
        """
        try:
            result = await run_blocking(
                list_conference_records_payload,
                ListConferenceRecordsRequest(
                    page_size=page_size,
                    page_token=page_token,
                    filter=filter,
                ),
            )
            account_timezone = await resolve_user_timezone()
            records = result.get("conferenceRecords", [])
            return {
                "conference_records": [
                    conference_envelope(record, account_timezone=account_timezone)
                    for record in records
                ],
                "next_page_token": result.get("nextPageToken"),
                "count": len(records),
                "account_timezone": account_timezone,
            }
        except HttpError as exc:
            return tool_error_payload(exc, filter=filter, page_token=page_token)

    @server.tool(name="get_conference_record")
    async def get_conference_record(conference_record_name: str) -> dict[str, Any]:
        """Get one completed conference record by bare ID or canonical name.

        The name comes from ``list_conference_records``. Returns a normalized conference
        envelope with timezone-aware timestamps, or a structured provider error.
        """
        record_name = normalize_conference_record_name(conference_record_name)
        try:
            account_timezone = await resolve_user_timezone()
            return conference_envelope(
                await run_blocking(
                    get_conference_record_payload,
                    GetConferenceRecordRequest(conference_record_name=conference_record_name),
                ),
                account_timezone=account_timezone,
            )
        except HttpError as exc:
            return tool_error_payload(exc, conference_record_name=record_name)

    @server.tool(name="list_conference_participants")
    async def list_conference_participants(
        conference_record_name: str,
        page_size: int = 50,
        page_token: str | None = None,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """List participants for a conference record with optional filtering.

        The record name comes from ``list_conference_records``; page size/token control
        pagination. Returns normalized participants, count, timezone, next token, or a
        structured provider error.
        """
        record_name = normalize_conference_record_name(conference_record_name)
        try:
            result = await run_blocking(
                list_conference_participants_payload,
                ListConferenceParticipantsRequest(
                    conference_record_name=conference_record_name,
                    page_size=page_size,
                    page_token=page_token,
                    filter=filter,
                ),
            )
            account_timezone = await resolve_user_timezone()
            participants = result.get("participants", [])
            return {
                "participants": [
                    participant_envelope(item, account_timezone=account_timezone)
                    for item in participants
                ],
                "next_page_token": result.get("nextPageToken"),
                "count": len(participants),
                "account_timezone": account_timezone,
            }
        except HttpError as exc:
            return tool_error_payload(exc, conference_record_name=record_name)

    @server.tool(name="list_conference_recordings")
    async def list_conference_recordings(
        conference_record_name: str,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List recording artifacts attached to a conference record.

        The record name comes from ``list_conference_records``; page size/token control
        pagination. Returns normalized recordings, count, timezone, next token, or a
        structured provider error.
        """
        record_name = normalize_conference_record_name(conference_record_name)
        try:
            result = await run_blocking(
                list_conference_recordings_payload,
                ListConferenceRecordingsRequest(
                    conference_record_name=conference_record_name,
                    page_size=page_size,
                    page_token=page_token,
                ),
            )
            account_timezone = await resolve_user_timezone()
            recordings = result.get("recordings", [])
            return {
                "recordings": [
                    recording_envelope(item, account_timezone=account_timezone)
                    for item in recordings
                ],
                "next_page_token": result.get("nextPageToken"),
                "count": len(recordings),
                "account_timezone": account_timezone,
            }
        except HttpError as exc:
            return tool_error_payload(exc, conference_record_name=record_name)

    @server.tool(name="list_conference_transcripts")
    async def list_conference_transcripts(
        conference_record_name: str,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List transcript artifacts attached to a conference record.

        The record name comes from ``list_conference_records``; page size/token control
        pagination. Returns normalized transcripts, count, timezone, next token, or a
        structured provider error.
        """
        record_name = normalize_conference_record_name(conference_record_name)
        try:
            result = await run_blocking(
                list_conference_transcripts_payload,
                ListConferenceTranscriptsRequest(
                    conference_record_name=conference_record_name,
                    page_size=page_size,
                    page_token=page_token,
                ),
            )
            account_timezone = await resolve_user_timezone()
            transcripts = result.get("transcripts", [])
            return {
                "transcripts": [
                    transcript_envelope(item, account_timezone=account_timezone)
                    for item in transcripts
                ],
                "next_page_token": result.get("nextPageToken"),
                "count": len(transcripts),
                "account_timezone": account_timezone,
            }
        except HttpError as exc:
            return tool_error_payload(exc, conference_record_name=record_name)
