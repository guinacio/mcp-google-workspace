"""Google People API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_people_service


def people_service() -> Any:
    return build_people_service()


def normalize_person_name(person_name: str) -> str:
    if person_name.startswith("people/"):
        return person_name
    return f"people/{person_name}"


def normalize_contact_group_name(group_name: str) -> str:
    if group_name.startswith("contactGroups/"):
        return group_name
    return f"contactGroups/{group_name}"
