"""Model-friendly Google Meet conference representations."""

from __future__ import annotations

from typing import Any

from ..common.timezone import in_account_timezone


def conference_envelope(record: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    return {
        "id": record.get("name"),
        "space_id": record.get("space"),
        "started_at": in_account_timezone(record.get("startTime"), account_timezone),
        "ended_at": in_account_timezone(record.get("endTime"), account_timezone),
        "expires_at": in_account_timezone(record.get("expireTime"), account_timezone),
        "timezone": account_timezone,
    }


def participant_envelope(participant: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    signed_in = participant.get("signedinUser", {})
    anonymous = participant.get("anonymousUser", {})
    phone = participant.get("phoneUser", {})
    identity = signed_in or anonymous or phone
    identity_type = "signed_in" if signed_in else "anonymous" if anonymous else "phone" if phone else "unknown"
    return {
        "id": participant.get("name"),
        "name": identity.get("displayName") or identity.get("user") or "Unknown participant",
        "user_id": identity.get("user"),
        "identity_type": identity_type,
        "joined_at": in_account_timezone(participant.get("earliestStartTime"), account_timezone),
        "left_at": in_account_timezone(participant.get("latestEndTime"), account_timezone),
        "timezone": account_timezone,
    }


def recording_envelope(recording: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    destination = recording.get("driveDestination", {})
    return {
        "id": recording.get("name"),
        "started_at": in_account_timezone(recording.get("startTime"), account_timezone),
        "ended_at": in_account_timezone(recording.get("endTime"), account_timezone),
        "timezone": account_timezone,
        "drive_file_id": destination.get("file"),
        "export_uri": recording.get("exportUri"),
    }


def transcript_envelope(transcript: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    destination = transcript.get("docsDestination", {})
    return {
        "id": transcript.get("name"),
        "started_at": in_account_timezone(transcript.get("startTime"), account_timezone),
        "ended_at": in_account_timezone(transcript.get("endTime"), account_timezone),
        "timezone": account_timezone,
        "document_id": destination.get("document"),
        "export_uri": transcript.get("exportUri"),
    }
