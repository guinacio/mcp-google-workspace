"""Normalized People API responses: contacts and contact groups without Google's metadata."""

from __future__ import annotations

from typing import Any

_GROUP_TYPES = {"USER_CONTACT_GROUP": "user", "SYSTEM_CONTACT_GROUP": "system"}


def _primary(entries: Any) -> dict[str, Any]:
    """The entry Google flags primary, else the first one; {} when there is none."""
    if not isinstance(entries, list):
        return {}
    candidates = [entry for entry in entries if isinstance(entry, dict)]
    for entry in candidates:
        if (entry.get("metadata") or {}).get("primary"):
            return entry
    return candidates[0] if candidates else {}


def _labelled_values(entries: Any, *, fold_case: bool) -> list[dict[str, Any]]:
    """Email or phone entries as ``{value, type}``, merged-source duplicates dropped."""
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        value = entry.get("value") if isinstance(entry, dict) else None
        if not isinstance(value, str) or not value:
            continue
        key = value.casefold() if fold_case else value
        if key in seen:
            continue
        seen.add(key)
        values.append({"value": value, "type": entry.get("type")})
    return values


def contact_envelope(person: dict[str, Any]) -> dict[str, Any]:
    """One People API Person as the fields a model reads and the next call needs."""
    name = _primary(person.get("names"))
    biography = _primary(person.get("biographies"))
    return {
        "id": person.get("resourceName"),  # the same value, under the key other services use
        "resource_name": person.get("resourceName"),
        "etag": person.get("etag"),
        "name": name.get("displayName"),
        "given_name": name.get("givenName"),
        "family_name": name.get("familyName"),
        "emails": _labelled_values(person.get("emailAddresses"), fold_case=True),
        "phones": _labelled_values(person.get("phoneNumbers"), fold_case=False),
        "organizations": [
            {"name": entry.get("name"), "title": entry.get("title")}
            for entry in person.get("organizations") or []
            if isinstance(entry, dict)
        ],
        "biography": biography.get("value"),
    }


def contacts_page(payload: dict[str, Any]) -> dict[str, Any]:
    """people.connections.list as one page of contacts."""
    contacts = [contact_envelope(person) for person in payload.get("connections") or []]
    return {
        "contacts": contacts,
        "count": len(contacts),
        "total": int(payload["totalItems"]) if payload.get("totalItems") is not None else None,
        "next_page_token": payload.get("nextPageToken"),
    }


def contact_search_result(payload: dict[str, Any]) -> dict[str, Any]:
    """people.searchContacts with each result's person unwrapped; Google omits results on no match."""
    contacts = [
        contact_envelope(result["person"])
        for result in payload.get("results") or []
        if isinstance(result, dict) and isinstance(result.get("person"), dict)
    ]
    return {"contacts": contacts, "count": len(contacts)}


def contact_group_envelope(group: dict[str, Any]) -> dict[str, Any]:
    """One ContactGroup; system groups carry Google's localized formattedName as name."""
    return {
        "resource_name": group.get("resourceName"),
        "name": group.get("formattedName") or group.get("name"),
        "group_type": _GROUP_TYPES.get(str(group.get("groupType") or "")),
        # memberCount is requested on every read and omitted by Google when it is zero.
        "member_count": int(group.get("memberCount") or 0),
    }


def contact_groups_page(payload: dict[str, Any]) -> dict[str, Any]:
    """contactGroups.list as one page of groups."""
    groups = [contact_group_envelope(group) for group in payload.get("contactGroups") or []]
    return {
        "contact_groups": groups,
        "count": len(groups),
        "total": int(payload["totalItems"]) if payload.get("totalItems") is not None else None,
        "next_page_token": payload.get("nextPageToken"),
    }


def group_members_result(payload: dict[str, Any], *, group_name: str) -> dict[str, Any]:
    """contactGroups.members.modify; Google omits both lists when every change applied."""
    return {
        "status": "ok",
        "group_name": group_name,
        "not_found": list(payload.get("notFoundResourceNames") or []),
        "cannot_remove_last_group": list(payload.get("canNotRemoveLastContactGroupResourceNames") or []),
    }
