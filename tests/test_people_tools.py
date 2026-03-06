import anyio
import pytest

from mcp_google_workspace.people.client import normalize_contact_group_name, normalize_person_name
from mcp_google_workspace.people.schemas import CreateContactRequest, ModifyContactGroupMembersRequest, UpdateContactRequest
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

    assert list_tool.annotations.readOnlyHint is True
    assert update_tool.annotations.idempotentHint is True
    assert delete_tool.annotations.destructiveHint is True
