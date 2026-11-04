import json
from pathlib import Path

import anyio
import pytest
from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.people.client import normalize_contact_group_name, normalize_person_name
from mcp_google_workspace.people.schemas import CreateContactRequest, ModifyContactGroupMembersRequest, UpdateContactRequest
from mcp_google_workspace.people.presentation import (
    contact_envelope,
    contact_group_envelope,
    contact_search_result,
    contacts_page,
    group_members_result,
)
from mcp_google_workspace.people.server import people_mcp
from mcp_google_workspace.people.tools import (
    CreateContactGroupRequest,
    DeleteContactRequest,
    GetContactRequest,
    ListContactGroupsRequest,
    ListContactsRequest,
    SearchContactsRequest,
    create_contact_group_payload,
    create_contact_payload,
    delete_contact_payload,
    get_contact_payload,
    list_contact_groups_payload,
    list_contacts_payload,
    modify_contact_group_members_payload,
    search_contacts_payload,
    update_contact_payload,
)
from mcp_google_workspace.server import workspace_mcp

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "google_payloads"
TIMEZONE = "America/Sao_Paulo"


async def _list_tool_names(server):
    tools = await server.list_tools(run_middleware=False)
    return [tool.name for tool in tools]


async def _get_tool(server, name):
    tools = await server.list_tools(run_middleware=False)
    return next(tool for tool in tools if tool.name == name)


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _ConnectionsApi:
    def __init__(self, parent):
        self.parent = parent

    def list(self, **kwargs):
        self.parent.calls.append(("connections.list", kwargs))
        return _Exec({"kind": "people.connections.list", "kwargs": kwargs})


class _PeopleApi:
    def __init__(self):
        self.calls = []
        self.connections_api = _ConnectionsApi(self)

    def connections(self):
        return self.connections_api

    def searchContacts(self, **kwargs):
        self.calls.append(("searchContacts", kwargs))
        return _Exec({"kind": "people.searchContacts", "kwargs": kwargs})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "people.get", "kwargs": kwargs})

    def createContact(self, **kwargs):
        self.calls.append(("createContact", kwargs))
        return _Exec({"kind": "people.createContact", "kwargs": kwargs})

    def updateContact(self, **kwargs):
        self.calls.append(("updateContact", kwargs))
        return _Exec({"kind": "people.updateContact", "kwargs": kwargs})

    def deleteContact(self, **kwargs):
        self.calls.append(("deleteContact", kwargs))
        return _Exec({"kind": "people.deleteContact", "kwargs": kwargs})


class _MembersApi:
    def __init__(self, parent):
        self.parent = parent

    def modify(self, **kwargs):
        self.parent.calls.append(("members.modify", kwargs))
        return _Exec({"kind": "contactGroups.members.modify", "kwargs": kwargs})


class _ContactGroupsApi:
    def __init__(self):
        self.calls = []
        self.members_api = _MembersApi(self)

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Exec({"kind": "contactGroups.list", "kwargs": kwargs})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "contactGroups.create", "kwargs": kwargs})

    def members(self):
        return self.members_api


class _PeopleService:
    def __init__(self):
        self.people_api = _PeopleApi()
        self.contact_groups_api = _ContactGroupsApi()

    def people(self):
        return self.people_api

    def contactGroups(self):
        return self.contact_groups_api


def test_people_server_registers_expected_tools_only():
    tool_names = anyio.run(_list_tool_names, people_mcp)
    assert "list_contacts" in tool_names
    assert "search_contacts" in tool_names
    assert "get_contact" in tool_names
    assert "create_contact" in tool_names
    assert "update_contact" in tool_names
    assert "delete_contact" in tool_names
    assert "list_contact_groups" in tool_names
    assert "create_contact_group" in tool_names
    assert "modify_contact_group_members" in tool_names
    assert all("directory" not in tool_name for tool_name in tool_names)


def test_people_request_validation_and_normalization():
    assert normalize_person_name("123") == "people/123"
    assert normalize_contact_group_name("friends") == "contactGroups/friends"

    created = CreateContactRequest(given_name="Ada")
    assert created.given_name == "Ada"

    with pytest.raises(ValueError):
        CreateContactRequest()
    with pytest.raises(ValueError):
        UpdateContactRequest(person_name="people/123")
    with pytest.raises(ValueError):
        ModifyContactGroupMembersRequest(group_name="friends")


def test_people_payload_helpers(monkeypatch):
    service = _PeopleService()
    monkeypatch.setattr("mcp_google_workspace.people.tools.people_service", lambda: service)

    contacts = list_contacts_payload(ListContactsRequest(page_size=25))
    search = search_contacts_payload(SearchContactsRequest(query="Ada", page_size=5))
    contact = get_contact_payload(GetContactRequest(person_name="123"))
    created = create_contact_payload(CreateContactRequest(given_name="Ada", email_addresses=["ada@example.com"]))
    updated = update_contact_payload(UpdateContactRequest(person_name="123", etag="etag-1", biography="Mathematician"))
    deleted = delete_contact_payload(DeleteContactRequest(person_name="123"))
    groups = list_contact_groups_payload(ListContactGroupsRequest(page_size=25))
    created_group = create_contact_group_payload(CreateContactGroupRequest(name="VIP"))
    modified_group = modify_contact_group_members_payload(
        ModifyContactGroupMembersRequest(group_name="friends", resource_names_to_add=["123"])
    )

    assert contacts["kwargs"]["resourceName"] == "people/me"
    assert service.people_api.calls[1][1]["query"] == ""
    assert service.people_api.calls[2][1]["query"] == "Ada"
    assert search["kwargs"]["pageSize"] == 5
    assert contact["kwargs"]["resourceName"] == "people/123"
    assert created["kwargs"]["body"]["emailAddresses"][0]["value"] == "ada@example.com"
    assert updated["kwargs"]["updatePersonFields"] == "biographies"
    assert deleted == {"status": "deleted", "person_name": "people/123"}
    assert groups["kwargs"]["pageSize"] == 25
    assert created_group["kwargs"]["body"]["contactGroup"]["name"] == "VIP"
    assert modified_group["kwargs"]["resourceName"] == "contactGroups/friends"


def test_people_tool_annotations():
    list_tool = anyio.run(_get_tool, people_mcp, "list_contacts")
    update_tool = anyio.run(_get_tool, people_mcp, "update_contact")
    delete_tool = anyio.run(_get_tool, people_mcp, "delete_contact")

    assert list_tool.annotations.read_only_hint is True
    assert update_tool.annotations.idempotent_hint is True
    assert delete_tool.annotations.destructive_hint is True


class _FailingExec:
    def execute(self):
        raise HttpError(Response({"status": "403"}), b'{"error":{"message":"Denied"}}')


class _FailingPeopleService:
    def people(self):
        return self

    def searchContacts(self, **kwargs):
        return _FailingExec()


def test_search_contacts_tool_returns_structured_error(monkeypatch):
    monkeypatch.setattr("mcp_google_workspace.people.tools.people_service", _FailingPeopleService)

    async def scenario():
        async with Client(people_mcp) as client:
            result = await client.call_tool(
                "search_contacts", {"query": "Ada"}, raise_on_error=False
            )
            assert result.is_error is True
            return result.structured_content

    # W5: a Google API failure is an isError tool result with the error envelope
    # (was a successful result carrying an "error" dict).
    result = anyio.run(scenario)
    assert "403" in result["message"]
    assert result["code"] == "permission_denied"
    assert result["provider_status"] == 403
    assert result["details"]["context"]["query"] == "Ada"


def test_contact_envelope_keeps_what_the_next_call_needs():
    person = {
        "resourceName": "people/c1",
        "etag": "%etag",
        "metadata": {"sources": [{"type": "CONTACT", "updateTime": "2026-01-01T00:00:00Z"}]},
        "names": [
            {"metadata": {"source": {"type": "PROFILE"}}, "displayName": "Profile Name"},
            {"metadata": {"primary": True}, "displayName": "Ada Lovelace", "givenName": "Ada", "familyName": "Lovelace"},
        ],
        "emailAddresses": [
            {"value": "ada@example.com", "type": "work", "formattedType": "Work"},
            {"value": "ADA@example.com", "metadata": {"source": {"type": "PROFILE"}}},
        ],
        "phoneNumbers": [{"value": "+1 555-0100", "canonicalForm": "+15550100"}],
    }

    envelope = contact_envelope(person)

    assert envelope == {
        "resource_name": "people/c1",
        "etag": "%etag",
        "name": "Ada Lovelace",
        "given_name": "Ada",
        "family_name": "Lovelace",
        "emails": [{"value": "ada@example.com", "type": "work"}],
        "phones": [{"value": "+1 555-0100", "type": None}],
        "organizations": [],
        "biography": None,
    }
    assert contact_envelope({})["name"] is None


def test_people_list_and_group_normalizers():
    page = contacts_page({"connections": [{"resourceName": "people/c1"}], "totalItems": 9, "totalPeople": 9})
    assert set(page) == {"contacts", "count", "total", "next_page_token"}
    assert (page["count"], page["total"], page["next_page_token"]) == (1, 9, None)
    assert contact_search_result({}) == {"contacts": [], "count": 0}

    group = contact_group_envelope(
        {
            "resourceName": "contactGroups/starred",
            "name": "starred",
            "formattedName": "Starred",
            "groupType": "SYSTEM_CONTACT_GROUP",
            "metadata": {"updateTime": "2026-11-03T15:00:00Z"},
        },
    )
    assert group == {
        "resource_name": "contactGroups/starred",
        "name": "Starred",
        "group_type": "system",
        "member_count": 0,
    }
    assert group_members_result({}, group_name="contactGroups/abc") == {
        "status": "ok",
        "group_name": "contactGroups/abc",
        "not_found": [],
        "cannot_remove_last_group": [],
    }


class _PayloadService:
    """A Google client stub whose every ``execute()`` returns one payload."""

    def __init__(self, payload):
        self._payload = payload

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *_args, **_kwargs):
        return self

    def execute(self, *_args, **_kwargs):
        return json.loads(json.dumps(self._payload))


async def _fixed_timezone():
    return TIMEZONE


def _call_workspace(tool, arguments):
    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool(tool, arguments)

    return anyio.run(scenario)


@pytest.mark.parametrize(
    ("tool", "arguments", "fixture"),
    [
        ("people_list_contacts", {"page_size": 3}, "people_list_contacts"),
        ("people_get_contact", {"person_name": "people/c5566778899001122334"}, "people_get_contact"),
        ("people_list_contact_groups", {}, "people_list_contact_groups"),
        (
            "people_modify_contact_group_members",
            {"group_name": "4f1d2c3b0a9e8d7c", "resource_names_to_add": ["c1111111111111111111"]},
            "people_modify_contact_group_members",
        ),
    ],
)
def test_people_tools_return_the_normalized_shape_through_the_server(tool, arguments, fixture, monkeypatch):
    payload = json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
    golden = json.loads((FIXTURES / "golden" / f"{fixture}.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    result = _call_workspace(tool, arguments)

    assert result.is_error is False, result.content
    structured = dict(result.structured_content)
    for added in ("has_more", "fetched_at"):  # the list pagination wrapper's fields
        structured.pop(added, None)
    assert structured == golden
