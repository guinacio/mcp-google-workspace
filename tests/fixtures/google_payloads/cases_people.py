"""People normalizers over recorded-shape People API payloads."""

from __future__ import annotations

from mcp_google_workspace.people.presentation import (
    contact_envelope,
    contact_group_envelope,
    contact_groups_page,
    contact_search_result,
    contacts_page,
    group_members_result,
)

from . import Case

CASES: list[Case] = [
    Case("people_list_contacts", "people_list_contacts.json", contacts_page, items_key="contacts"),
    Case("people_search_contacts", "people_search_contacts.json", contact_search_result, items_key="contacts"),
    Case("people_search_contacts_no_match", "people_empty_response.json", contact_search_result),
    Case(
        "people_get_contact",
        "people_get_contact.json",
        lambda payload: {"contact": contact_envelope(payload)},
    ),
    Case(
        "people_create_contact",
        "people_create_contact.json",
        lambda payload: {"status": "ok", "contact": contact_envelope(payload)},
    ),
    Case(
        "people_update_contact",
        "people_update_contact.json",
        lambda payload: {"status": "ok", "contact": contact_envelope(payload)},
    ),
    Case(
        "people_list_contact_groups",
        "people_list_contact_groups.json",
        lambda payload: contact_groups_page(payload),
        items_key="contact_groups",
    ),
    Case(
        "people_create_contact_group",
        "people_create_contact_group.json",
        lambda payload: {
            "status": "ok",
            "contact_group": contact_group_envelope(payload),
        },
    ),
    Case(
        "people_modify_contact_group_members",
        "people_modify_contact_group_members.json",
        lambda payload: group_members_result(payload, group_name="contactGroups/4f1d2c3b0a9e8d7c"),
    ),
    Case(
        "people_modify_contact_group_members_all_applied",
        "people_empty_response.json",
        lambda payload: group_members_result(payload, group_name="contactGroups/4f1d2c3b0a9e8d7c"),
    ),
]
