"""FastMCP Meet tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
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

SpaceNameArg = Annotated[
    str,
    "Meet space resource name 'spaces/abc123' or the bare space ID, from meet_create_space (the name field of the result).",
]
RecordNameArg = Annotated[
    str,
    "Conference record resource name 'conferenceRecords/abc123' or the bare ID, from meet_list_conference_records (the id field of a record).",
]
PageSizeArg = Annotated[int, "Maximum number of results to return in one page, from 1 to 100."]
SPACE_CONFIG_FIELDS = (
    "Meet API SpaceConfig object, passed through unchanged. Its fields include accessType "
    "(OPEN, TRUSTED or RESTRICTED: who can join without knocking), entryPointAccess (ALL or "
    "CREATOR_APP_ONLY), moderation (OFF or ON) and attendanceReportGenerationType "
    "(GENERATE_REPORT or DO_NOT_GENERATE)."
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
    def create_space(
        config: Annotated[
            dict[str, Any] | None,
            SPACE_CONFIG_FIELDS + " Omit to create the space with the account's default settings.",
        ] = None,
    ) -> dict[str, Any]:
        """Create a Google Meet space, the persistent meeting room behind a join link.

        Use meet_update_space to change its settings later. Returns the created Space resource:
        name ('spaces/...', the identifier the other space tools take), meetingUri, meetingCode
        and config.
        """
        try:
            return create_space_payload(CreateSpaceRequest(config=config))
        except HttpError as exc:
            raise provider_tool_error(exc, operation="create_space") from exc

    @server.tool(name="get_space")
    def get_space(space_name: SpaceNameArg) -> dict[str, Any]:
        """Get a Meet space by its ID or 'spaces/...' name.

        Space names come from meet_create_space. Returns the Space resource: name, meetingUri,
        meetingCode, config and activeConference when a meeting is in progress.
        """
        try:
            return get_space_payload(GetSpaceRequest(space_name=space_name))
        except HttpError as exc:
            raise provider_tool_error(exc, space_name=normalize_space_name(space_name)) from exc

    @server.tool(name="update_space")
    def update_space(
        space_name: SpaceNameArg,
        config: Annotated[
            dict[str, Any],
            SPACE_CONFIG_FIELDS + " Only the fields named by update_mask are applied.",
        ],
        update_mask: Annotated[
            str | None,
            "Comma-separated Space field paths to update, e.g. 'config.accessType,config.moderation'; omit to use 'config'.",
        ] = None,
    ) -> dict[str, Any]:
        """Change the configuration of an existing Meet space, such as who can join without knocking.

        To read the current settings first use meet_get_space. Returns the updated Space
        resource, including its config.
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
            raise provider_tool_error(exc, space_name=normalize_space_name(space_name)) from exc

    @server.tool(name="end_active_conference")
    def end_active_conference(space_name: SpaceNameArg) -> dict[str, Any]:
        """End the conference currently running in a Meet space, disconnecting every participant.

        The space itself remains and can host a new conference. Returns the Meet API's empty
        response once the conference has ended.
        """
        try:
            return end_active_conference_payload(EndActiveConferenceRequest(space_name=space_name))
        except HttpError as exc:
            raise provider_tool_error(exc, space_name=normalize_space_name(space_name)) from exc

    @server.tool(name="list_conference_records")
    async def list_conference_records(
        page_size: PageSizeArg = 50,
        page_token: str | None = None,
        filter: Annotated[
            str | None,
            "Meet API filter on space.meeting_code, space.name, start_time or end_time, e.g. 'space.meeting_code = \"abc-mnop-xyz\"' or 'end_time IS NULL' for ongoing conferences; omit for no filter.",
        ] = None,
    ) -> dict[str, Any]:
        """List Meet conference records, one per meeting held in a space, with optional filtering.

        Use the id of a record with meet_get_conference_record, meet_list_conference_participants,
        meet_list_conference_recordings or meet_list_conference_transcripts. Returns
        conference_records (id, space_id, started_at, ended_at, expires_at, timezone), count,
        next_page_token and account_timezone.
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
            raise provider_tool_error(exc, filter=filter, page_token=page_token) from exc

    @server.tool(name="get_conference_record")
    async def get_conference_record(conference_record_name: RecordNameArg) -> dict[str, Any]:
        """Get one conference record by its ID or 'conferenceRecords/...' name.

        Find the name with meet_list_conference_records. Returns id, space_id, started_at,
        ended_at, expires_at and timezone, with timestamps in the account time zone.
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
            raise provider_tool_error(exc, conference_record_name=record_name) from exc

    @server.tool(name="list_conference_participants")
    async def list_conference_participants(
        conference_record_name: RecordNameArg,
        page_size: PageSizeArg = 50,
        page_token: str | None = None,
        filter: Annotated[
            str | None,
            "Meet API filter on earliest_start_time or latest_end_time, e.g. 'latest_end_time IS NULL' for participants still in the conference; omit for no filter.",
        ] = None,
    ) -> dict[str, Any]:
        """List the participants of one conference record, with optional filtering.

        Find the record with meet_list_conference_records. Returns participants (id, name, user_id,
        identity_type of signed_in, anonymous or phone, joined_at, left_at), count,
        next_page_token and account_timezone.
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
            raise provider_tool_error(exc, conference_record_name=record_name) from exc

    @server.tool(name="list_conference_recordings")
    async def list_conference_recordings(
        conference_record_name: RecordNameArg,
        page_size: PageSizeArg = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List the recordings of one conference record.

        Find the record with meet_list_conference_records. Returns recordings (id, started_at,
        ended_at, drive_file_id, export_uri), count, next_page_token and account_timezone; get the
        video with drive_download_file using drive_file_id.
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
            raise provider_tool_error(exc, conference_record_name=record_name) from exc

    @server.tool(name="list_conference_transcripts")
    async def list_conference_transcripts(
        conference_record_name: RecordNameArg,
        page_size: PageSizeArg = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List the transcripts of one conference record.

        Find the record with meet_list_conference_records. Returns transcripts (id, started_at,
        ended_at, document_id, export_uri), count, next_page_token and account_timezone; read the
        text with docs_read_document using document_id.
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
            raise provider_tool_error(exc, conference_record_name=record_name) from exc
