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

PEOPLE_TOOLS = {
    "search_contacts",
    "get_contact",
    "create_contact",
    "update_contact",
    "delete_contact",
    "manage_contact_groups",
}
RETIRED_PEOPLE_TOOLS = {"list_contacts", "list_contact_groups", "create_contact_group", "modify_contact_group_members"}
CONTACTS_SHAPE = {"contacts", "count", "total", "next_page_token", "has_more", "fetched_at"}


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
        return _Exec(
            {
                "connections": [{"resourceName": "people/c1", "names": [{"displayName": "Ada"}]}],
                "totalItems": 41,
                "nextPageToken": "page-2",
            }
        )


class _PeopleApi:
    def __init__(self):
        self.calls = []
        self.connections_api = _ConnectionsApi(self)

    def connections(self):
        return self.connections_api

    def searchContacts(self, **kwargs):
        self.calls.append(("searchContacts", kwargs))
        return _Exec({"results": [{"person": {"resourceName": "people/c2", "names": [{"displayName": "Bob"}]}}]})

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


@pytest.fixture
def service(monkeypatch):
    fake = _PeopleService()
    monkeypatch.setattr("mcp_google_workspace.people.tools.people_service", lambda: fake)
    return fake


def _call(tool, arguments, *, raise_on_error=True):
    async def scenario():
        async with Client(people_mcp) as client:
            return await client.call_tool(tool, arguments, raise_on_error=raise_on_error)

    return anyio.run(scenario)


def _invalid(tool, arguments):
    result = _call(tool, arguments, raise_on_error=False)
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_input", result.structured_content
    return result.structured_content


def test_people_server_registers_expected_tools_only():
    tool_names = set(anyio.run(_list_tool_names, people_mcp))
    assert tool_names == PEOPLE_TOOLS
    assert not tool_names & RETIRED_PEOPLE_TOOLS
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
    with pytest.raises(ValueError, match="modify_members"):
        ModifyContactGroupMembersRequest(group_name="friends")


def test_people_payload_helpers(service):
    contacts = list_contacts_payload(ListContactsRequest(page_size=25))
    search_contacts_payload(SearchContactsRequest(query="Ada", page_size=5))
    contact = get_contact_payload(GetContactRequest(person_name="123"))
    created = create_contact_payload(CreateContactRequest(given_name="Ada", email_addresses=["ada@example.com"]))
    updated = update_contact_payload(UpdateContactRequest(person_name="123", etag="etag-1", biography="Mathematician"))
    deleted = delete_contact_payload(DeleteContactRequest(person_name="123"))
    groups = list_contact_groups_payload(ListContactGroupsRequest(page_size=25))
    created_group = create_contact_group_payload(CreateContactGroupRequest(name="VIP"))
    modified_group = modify_contact_group_members_payload(
        ModifyContactGroupMembersRequest(group_name="friends", resource_names_to_add=["123"])
    )

    assert service.people_api.calls[0] == (
        "connections.list",
        {
            "resourceName": "people/me",
            "pageSize": 25,
            "pageToken": None,
            "personFields": "names,emailAddresses,phoneNumbers,organizations,biographies",
            "sortOrder": None,
            "sources": ["READ_SOURCE_TYPE_CONTACT"],
        },
    )
    assert contacts["totalItems"] == 41
    assert service.people_api.calls[1][1]["query"] == ""
    assert service.people_api.calls[2][1]["query"] == "Ada"
    assert service.people_api.calls[2][1]["pageSize"] == 5
    assert contact["kwargs"]["resourceName"] == "people/123"
    assert created["kwargs"]["body"]["emailAddresses"][0]["value"] == "ada@example.com"
    assert updated["kwargs"]["updatePersonFields"] == "biographies"
    assert deleted == {"status": "deleted", "person_name": "people/123"}
    assert groups["kwargs"]["pageSize"] == 25
    assert created_group["kwargs"]["body"]["contactGroup"]["name"] == "VIP"
    assert modified_group["kwargs"]["resourceName"] == "contactGroups/friends"


def test_people_tool_annotations():
    search_tool = anyio.run(_get_tool, people_mcp, "search_contacts")
    update_tool = anyio.run(_get_tool, people_mcp, "update_contact")
    delete_tool = anyio.run(_get_tool, people_mcp, "delete_contact")

    assert search_tool.annotations.read_only_hint is True
    assert update_tool.annotations.idempotent_hint is True
    assert delete_tool.annotations.destructive_hint is True


# --- people_search_contacts: list mode (formerly people_list_contacts) and search mode ---


def test_search_contacts_without_query_lists_contacts(service):
    result = _call(
        "search_contacts",
        {"page_size": 50, "page_token": "page-1", "sort_order": "FIRST_NAME_ASCENDING"},
    )

    assert service.people_api.calls == [
        (
            "connections.list",
            {
                "resourceName": "people/me",
                "pageSize": 50,
                "pageToken": "page-1",
                "personFields": "names,emailAddresses,phoneNumbers,organizations,biographies",
                "sortOrder": "FIRST_NAME_ASCENDING",
                "sources": ["READ_SOURCE_TYPE_CONTACT"],
            },
        )
    ]
    structured = result.structured_content
    assert set(structured) == CONTACTS_SHAPE
    assert [contact["resource_name"] for contact in structured["contacts"]] == ["people/c1"]
    assert (structured["count"], structured["total"]) == (1, 41)
    assert (structured["next_page_token"], structured["has_more"]) == ("page-2", True)


@pytest.mark.parametrize("arguments", [{}, {"query": ""}, {"query": "  "}])
def test_search_contacts_lists_with_list_defaults(arguments, service):
    _call("search_contacts", arguments)

    name, kwargs = service.people_api.calls[0]
    assert name == "connections.list"
    assert (kwargs["pageSize"], kwargs["pageToken"], kwargs["sortOrder"]) == (100, None, None)


def test_search_contacts_with_query_searches(service):
    result = _call("search_contacts", {"query": "Bo", "sources": ["READ_SOURCE_TYPE_CONTACT", "READ_SOURCE_TYPE_PROFILE"]})

    assert [name for name, _ in service.people_api.calls] == ["searchContacts", "searchContacts"]
    warmup, search = (kwargs for _, kwargs in service.people_api.calls)
    assert warmup["query"] == "" and warmup["pageSize"] == 1
    assert search["query"] == "Bo"
    assert search["pageSize"] == 20
    assert search["sources"] == ["READ_SOURCE_TYPE_CONTACT", "READ_SOURCE_TYPE_PROFILE"]
    structured = result.structured_content
    # One shape for both modes.
    assert set(structured) == CONTACTS_SHAPE
    assert [contact["resource_name"] for contact in structured["contacts"]] == ["people/c2"]
    assert (structured["count"], structured["total"], structured["next_page_token"]) == (1, None, None)
    assert structured["has_more"] is False


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"query": "Bo", "page_token": "page-2"}, "page_token"),
        ({"query": "Bo", "sort_order": "LAST_NAME_ASCENDING"}, "sort_order"),
        ({"query": "Bo", "page_size": 101}, "page_size"),
        ({"page_size": 1001}, "page_size"),
    ],
)
def test_search_contacts_rejects_listing_parameters_with_query(arguments, message, service):
    error = _invalid("search_contacts", arguments)
    assert message in json.dumps(error)
    assert service.people_api.calls == []


def test_search_contacts_publishes_query_as_optional():
    tool = anyio.run(_get_tool, people_mcp, "search_contacts")
    assert "query" not in tool.parameters.get("required", [])
    assert tool.parameters["properties"]["sort_order"]["enum"]


class _FailingExec:
    def execute(self):
        raise HttpError(Response({"status": "403"}), b'{"error":{"message":"Denied"}}')


class _FailingPeopleService:
    def people(self):
        return self

    def searchContacts(self, **kwargs):
        return _FailingExec()

    def connections(self):
        return self

    def list(self, **kwargs):
        return _FailingExec()

    def contactGroups(self):
        return self

    def create(self, **kwargs):
        return _FailingExec()


@pytest.mark.parametrize(
    ("tool", "arguments", "context"),
    [
        ("search_contacts", {"query": "Ada"}, {"query": "Ada"}),
        ("search_contacts", {}, {}),
        ("manage_contact_groups", {"action": "list"}, {"action": "list"}),
        ("manage_contact_groups", {"action": "create", "name": "VIP"}, {"action": "create", "name": "VIP"}),
    ],
)
def test_people_tools_return_structured_errors(tool, arguments, context, monkeypatch):
    monkeypatch.setattr("mcp_google_workspace.people.tools.people_service", _FailingPeopleService)

    # A Google API failure is an isError tool result with the error envelope.
    result = _call(tool, arguments, raise_on_error=False)

    assert result.is_error is True
    error = result.structured_content
    assert "403" in error["message"]
    assert error["code"] == "permission_denied"
    assert error["provider_status"] == 403
    assert (error.get("details") or {}).get("context", {}) == context


# --- people_manage_contact_groups ---


def test_manage_contact_groups_list(service):
    result = _call("manage_contact_groups", {"action": "list", "page_size": 25, "page_token": "g2"})

    assert service.contact_groups_api.calls == [
        ("list", {"groupFields": "name,groupType,memberCount,metadata", "pageSize": 25, "pageToken": "g2"})
    ]
    structured = result.structured_content
    assert structured["action"] == "list"
    assert {"contact_groups", "count", "total", "next_page_token"} <= set(structured)


def test_manage_contact_groups_list_defaults(service):
    _call("manage_contact_groups", {"action": "list"})
    assert service.contact_groups_api.calls[0][1]["pageSize"] == 100
    assert service.contact_groups_api.calls[0][1]["pageToken"] is None


def test_manage_contact_groups_create(service):
    result = _call("manage_contact_groups", {"action": "create", "name": "VIP"})

    assert service.contact_groups_api.calls == [("create", {"body": {"contactGroup": {"name": "VIP"}}})]
    assert result.structured_content["status"] == "ok"
    assert result.structured_content["action"] == "create"
    assert set(result.structured_content["contact_group"]) == {"resource_name", "name", "group_type", "member_count"}


def test_manage_contact_groups_modify_members(service):
    result = _call(
        "manage_contact_groups",
        {
            "action": "modify_members",
            "group_name": "friends",
            "resource_names_to_add": ["c1"],
            "resource_names_to_remove": ["people/c2"],
        },
    )

    assert service.contact_groups_api.calls == [
        (
            "members.modify",
            {
                "resourceName": "contactGroups/friends",
                "body": {"resourceNamesToAdd": ["people/c1"], "resourceNamesToRemove": ["people/c2"]},
            },
        )
    ]
    assert result.structured_content == {
        "status": "ok",
        "action": "modify_members",
        "group_name": "contactGroups/friends",
        "not_found": [],
        "cannot_remove_last_group": [],
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"action": "create"}, "action 'create' requires name"),
        ({"action": "modify_members", "resource_names_to_add": ["c1"]}, "action 'modify_members' requires group_name"),
        ({"action": "modify_members", "group_name": "friends"}, "resource_names_to_add or resource_names_to_remove"),
        ({"action": "modify_members", "group_name": "friends", "resource_names_to_add": []}, "resource_names_to_add"),
        ({"action": "list", "name": "VIP"}, "name not used by action 'list'"),
        ({"action": "create", "name": "VIP", "group_name": "friends"}, "group_name not used by action 'create'"),
        ({"action": "modify_members", "group_name": "g", "resource_names_to_add": ["c1"], "page_token": "x"}, "page_token"),
    ],
)
def test_manage_contact_groups_validates_parameters_per_action(arguments, message, service):
    error = _invalid("manage_contact_groups", arguments)
    assert message in json.dumps(error)
    assert service.contact_groups_api.calls == []


def test_manage_contact_groups_rejects_an_unknown_action(service):
    result = _call("manage_contact_groups", {"action": "rename", "name": "VIP"}, raise_on_error=False)

    assert result.is_error is True
    assert "action" in result.content[0].text
    assert service.contact_groups_api.calls == []


def test_manage_contact_groups_publishes_the_action_enum():
    tool = anyio.run(_get_tool, people_mcp, "manage_contact_groups")
    assert tool.parameters["required"] == ["action"]
    assert tool.parameters["properties"]["action"]["enum"] == ["list", "create", "modify_members"]


# --- delete_contact confirmation (unchanged gating) ---


@pytest.mark.parametrize("accepted", [True, False])
def test_delete_contact_asks_for_confirmation(accepted, service, monkeypatch):
    asked = []

    async def confirm(ctx, action_name, message, **kwargs):
        asked.append((action_name, message, kwargs))
        return accepted

    # The consent gate around every tool asks through the confirmation adapter.
    monkeypatch.setattr("mcp_google_workspace.common.confirmation.confirm_destructive_action", confirm)

    result = _call("delete_contact", {"person_name": "c9"})

    # The message shows the name as passed; the deletion itself normalizes it.
    assert asked == [("delete_contact", "Permanently delete contact c9?", {"explicit_confirm_field": False})]
    if accepted:
        assert service.people_api.calls == [("deleteContact", {"resourceName": "people/c9"})]
        assert result.structured_content == {"status": "deleted", "person_name": "people/c9"}
    else:
        assert service.people_api.calls == []
        assert result.structured_content == {
            "status": "cancelled",
            "action": "delete_contact",
            "reason": "The user declined; nothing was changed.",
        }


# --- presentation ---


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


def _call_workspace(tool, arguments):
    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool(tool, arguments)

    return anyio.run(scenario)


@pytest.mark.parametrize(
    ("tool", "arguments", "fixture", "extra"),
    [
        ("people_search_contacts", {"page_size": 3}, "people_list_contacts", {}),
        # Search mode carries the list fields too, null: Google reports no total and no further page.
        (
            "people_search_contacts",
            {"query": "ada"},
            "people_search_contacts",
            {"total": None, "next_page_token": None},
        ),
        ("people_get_contact", {"person_name": "people/c5566778899001122334"}, "people_get_contact", {}),
        ("people_manage_contact_groups", {"action": "list"}, "people_list_contact_groups", {"action": "list"}),
        (
            "people_manage_contact_groups",
            {"action": "create", "name": "VIP"},
            "people_create_contact_group",
            {"action": "create"},
        ),
        (
            "people_manage_contact_groups",
            {
                "action": "modify_members",
                "group_name": "4f1d2c3b0a9e8d7c",
                "resource_names_to_add": ["c1111111111111111111"],
            },
            "people_modify_contact_group_members",
            {"action": "modify_members"},
        ),
    ],
)
def test_people_tools_return_the_normalized_shape_through_the_server(tool, arguments, fixture, extra, monkeypatch):
    payload = json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
    golden = json.loads((FIXTURES / "golden" / f"{fixture}.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    result = _call_workspace(tool, arguments)

    assert result.is_error is False, result.content
    structured = dict(result.structured_content)
    for added in ("has_more", "fetched_at"):  # the list pagination wrapper's fields
        structured.pop(added, None)
    assert structured == {**golden, **extra}


def test_update_with_only_given_name_keeps_the_stored_family_and_middle_name(service):
    stored = {"givenName": "Ada", "familyName": "Lovelace", "middleName": "King", "displayName": "Ada King Lovelace"}
    service.people_api.get = lambda **kwargs: (
        service.people_api.calls.append(("get", kwargs)) or _Exec({"resourceName": "people/123", "names": [stored]})
    )

    update_contact_payload(UpdateContactRequest(person_name="123", etag="etag-1", given_name="Augusta"))

    (get,) = [kwargs for name, kwargs in service.people_api.calls if name == "get"]
    assert get["resourceName"] == "people/123" and get["personFields"] == "names"
    (update,) = [kwargs for name, kwargs in service.people_api.calls if name == "updateContact"]
    assert update["updatePersonFields"] == "names"
    assert update["body"]["names"] == [{"givenName": "Augusta", "familyName": "Lovelace", "middleName": "King"}]


def test_update_without_name_parts_does_not_read_the_contact(service):
    update_contact_payload(UpdateContactRequest(person_name="123", etag="etag-1", biography="Mathematician"))

    assert [name for name, _ in service.people_api.calls] == ["updateContact"]
