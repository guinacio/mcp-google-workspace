"""Model-friendly Google Meet conference representations."""

from __future__ import annotations

from typing import Any


def conference_envelope(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("name"),
        "space_id": record.get("space"),
        "started_at": record.get("startTime"),
        "ended_at": record.get("endTime"),
        "expires_at": record.get("expireTime"),
    }


def participant_envelope(participant: dict[str, Any]) -> dict[str, Any]:
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
        "joined_at": participant.get("earliestStartTime"),
        "left_at": participant.get("latestEndTime"),
    }


def recording_envelope(recording: dict[str, Any]) -> dict[str, Any]:
    destination = recording.get("driveDestination", {})
    return {
        "id": recording.get("name"),
        "started_at": recording.get("startTime"),
        "ended_at": recording.get("endTime"),
        "drive_file_id": destination.get("file"),
        "export_uri": recording.get("exportUri"),
    }


def transcript_envelope(transcript: dict[str, Any]) -> dict[str, Any]:
    destination = transcript.get("docsDestination", {})
    return {
        "id": transcript.get("name"),
        "started_at": transcript.get("startTime"),
        "ended_at": transcript.get("endTime"),
        "document_id": destination.get("document"),
        "export_uri": transcript.get("exportUri"),
    }
