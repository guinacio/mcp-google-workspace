"""FastMCP Meet tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .client import meet_service, normalize_conference_record_name, normalize_space_name
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
    async def create_space(config: dict[str, Any] | None = None) -> dict[str, Any]:
        return create_space_payload(CreateSpaceRequest(config=config))

    @server.tool(name="get_space")
    async def get_space(space_name: str) -> dict[str, Any]:
        return get_space_payload(GetSpaceRequest(space_name=space_name))

    @server.tool(name="update_space")
    async def update_space(space_name: str, config: dict[str, Any], update_mask: str | None = None) -> dict[str, Any]:
        return update_space_payload(UpdateSpaceRequest(space_name=space_name, config=config, update_mask=update_mask))

    @server.tool(name="end_active_conference")
    async def end_active_conference(space_name: str) -> dict[str, Any]:
        return end_active_conference_payload(EndActiveConferenceRequest(space_name=space_name))

    @server.tool(name="list_conference_records")
    async def list_conference_records(
        page_size: int = 50,
        page_token: str | None = None,
        filter: str | None = None,
    ) -> dict[str, Any]:
        return list_conference_records_payload(
            ListConferenceRecordsRequest(page_size=page_size, page_token=page_token, filter=filter)
        )

    @server.tool(name="get_conference_record")
    async def get_conference_record(conference_record_name: str) -> dict[str, Any]:
        return get_conference_record_payload(GetConferenceRecordRequest(conference_record_name=conference_record_name))

    @server.tool(name="list_conference_participants")
    async def list_conference_participants(
        conference_record_name: str,
        page_size: int = 50,
        page_token: str | None = None,
        filter: str | None = None,
    ) -> dict[str, Any]:
        return list_conference_participants_payload(
            ListConferenceParticipantsRequest(
                conference_record_name=conference_record_name,
                page_size=page_size,
                page_token=page_token,
                filter=filter,
            )
        )

    @server.tool(name="list_conference_recordings")
    async def list_conference_recordings(
        conference_record_name: str,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return list_conference_recordings_payload(
            ListConferenceRecordingsRequest(
                conference_record_name=conference_record_name,
                page_size=page_size,
                page_token=page_token,
            )
        )

    @server.tool(name="list_conference_transcripts")
    async def list_conference_transcripts(
        conference_record_name: str,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return list_conference_transcripts_payload(
            ListConferenceTranscriptsRequest(
                conference_record_name=conference_record_name,
                page_size=page_size,
                page_token=page_token,
            )
        )
