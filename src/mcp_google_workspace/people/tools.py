"""FastMCP People tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import confirm_destructive_action, run_blocking
from ..common.errors import tool_error_payload
from .client import normalize_contact_group_name, normalize_person_name, people_service
from .schemas import (
    CreateContactGroupRequest,
    CreateContactRequest,
    DeleteContactRequest,
    GetContactRequest,
    ListContactGroupsRequest,
    ListContactsRequest,
    ModifyContactGroupMembersRequest,
    ReadSourceType,
    SearchContactsRequest,
    SortOrder,
    UpdateContactRequest,
)


def _build_contact_body(
    *,
    given_name: str | None,
    family_name: str | None,
    display_name: str | None,
    email_addresses: list[str] | None,
    phone_numbers: list[str] | None,
    organization: str | None,
    biography: str | None,
    etag: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    body: dict[str, Any] = {}
    update_fields: list[str] = []

    if any(value is not None for value in (given_name, family_name, display_name)):
        body["names"] = [{
            **({"givenName": given_name} if given_name is not None else {}),
            **({"familyName": family_name} if family_name is not None else {}),
            **({"displayName": display_name} if display_name is not None else {}),
        }]
        update_fields.append("names")
    if email_addresses is not None:
        body["emailAddresses"] = [{"value": value} for value in email_addresses]
        update_fields.append("emailAddresses")
    if phone_numbers is not None:
        body["phoneNumbers"] = [{"value": value} for value in phone_numbers]
        update_fields.append("phoneNumbers")
    if organization is not None:
        body["organizations"] = [{"name": organization}]
        update_fields.append("organizations")
    if biography is not None:
        body["biographies"] = [{"value": biography}]
        update_fields.append("biographies")
    if etag is not None:
        body["etag"] = etag
    return body, update_fields


def list_contacts_payload(request: ListContactsRequest) -> dict[str, Any]:
    service = people_service()
    return service.people().connections().list(
        resourceName="people/me",
        pageSize=request.page_size,
        pageToken=request.page_token,
        personFields=request.person_fields,
        sortOrder=request.sort_order,
        sources=request.sources,
    ).execute()


def search_contacts_payload(request: SearchContactsRequest) -> dict[str, Any]:
    service = people_service()
    # People API quirk: searchContacts reads from a lazily-populated server-side
    # cache, and Google's docs instruct clients to send an empty-query warmup
    # request first so the real search sees fresh results.
    # https://developers.google.com/people/v1/contacts#search_the_users_contacts
    service.people().searchContacts(query="", readMask=request.read_mask, pageSize=1, sources=request.sources).execute()
    return service.people().searchContacts(
        query=request.query,
        readMask=request.read_mask,
        pageSize=request.page_size,
        sources=request.sources,
    ).execute()


def get_contact_payload(request: GetContactRequest) -> dict[str, Any]:
    service = people_service()
    return service.people().get(
        resourceName=normalize_person_name(request.person_name),
        personFields=request.person_fields,
        sources=request.sources,
    ).execute()


def create_contact_payload(request: CreateContactRequest) -> dict[str, Any]:
    service = people_service()
    body, _ = _build_contact_body(
        given_name=request.given_name,
        family_name=request.family_name,
        display_name=request.display_name,
        email_addresses=[str(item) for item in request.email_addresses],
        phone_numbers=request.phone_numbers,
        organization=request.organization,
        biography=request.biography,
    )
    return service.people().createContact(personFields=request.person_fields, body=body).execute()


def update_contact_payload(request: UpdateContactRequest) -> dict[str, Any]:
    service = people_service()
    body, update_fields = _build_contact_body(
        given_name=request.given_name,
        family_name=request.family_name,
        display_name=request.display_name,
        email_addresses=[str(item) for item in request.email_addresses] if request.email_addresses is not None else None,
        phone_numbers=request.phone_numbers,
        organization=request.organization,
        biography=request.biography,
        etag=request.etag,
    )
    return service.people().updateContact(
        resourceName=normalize_person_name(request.person_name),
        updatePersonFields=",".join(update_fields),
        personFields=request.person_fields,
        sources=request.sources,
        body=body,
    ).execute()


def delete_contact_payload(request: DeleteContactRequest) -> dict[str, Any]:
    service = people_service()
    person_name = normalize_person_name(request.person_name)
    service.people().deleteContact(resourceName=person_name).execute()
    return {"status": "deleted", "person_name": person_name}


def list_contact_groups_payload(request: ListContactGroupsRequest) -> dict[str, Any]:
    service = people_service()
    return service.contactGroups().list(
        groupFields=request.group_fields,
        pageSize=request.page_size,
        pageToken=request.page_token,
    ).execute()


def create_contact_group_payload(request: CreateContactGroupRequest) -> dict[str, Any]:
    service = people_service()
    return service.contactGroups().create(body={"contactGroup": {"name": request.name}}).execute()


def modify_contact_group_members_payload(request: ModifyContactGroupMembersRequest) -> dict[str, Any]:
    service = people_service()
    return service.contactGroups().members().modify(
        resourceName=normalize_contact_group_name(request.group_name),
        body={
            "resourceNamesToAdd": [normalize_person_name(item) for item in request.resource_names_to_add],
            "resourceNamesToRemove": [normalize_person_name(item) for item in request.resource_names_to_remove],
        },
    ).execute()


def register_tools(server: FastMCP) -> None:
    @server.tool(name="list_contacts")
    def list_contacts(
        page_size: int = 100,
        page_token: str | None = None,
        person_fields: str = "names,emailAddresses,phoneNumbers,organizations,biographies",
        sort_order: SortOrder | None = None,
        sources: list[ReadSourceType] | None = None,
    ) -> dict[str, Any]:
        """Page through the account's saved contacts.

        person_fields is a comma-separated People API field mask controlling
        which contact fields are returned; page_token continues a previous page.
        Returns the raw People connections.list payload ('connections' list plus
        'nextPageToken'), or {"error": ...} on API failure.
        """
        try:
            return list_contacts_payload(
                ListContactsRequest(
                    page_size=page_size,
                    page_token=page_token,
                    person_fields=person_fields,
                    sort_order=sort_order,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc)

    @server.tool(name="search_contacts")
    def search_contacts(
        query: str,
        page_size: int = 20,
        read_mask: str = "names,emailAddresses,phoneNumbers,organizations,biographies",
        sources: list[ReadSourceType] | None = None,
    ) -> dict[str, Any]:
        """Search saved contacts by name, email, phone, or organization text.

        query matches prefixes of those fields; read_mask is a comma-separated
        People API field mask for the returned contact data. Returns the raw
        searchContacts payload ('results' list of {person} matches), or
        {"error": ...} on API failure.
        """
        try:
            return search_contacts_payload(
                SearchContactsRequest(
                    query=query,
                    page_size=page_size,
                    read_mask=read_mask,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, query=query)

    @server.tool(name="get_contact")
    def get_contact(
        person_name: str,
        person_fields: str = "names,emailAddresses,phoneNumbers,organizations,biographies",
        sources: list[ReadSourceType] | None = None,
    ) -> dict[str, Any]:
        """Fetch one contact by resource name (e.g. 'people/c123'; bare IDs are normalized).

        person_fields is a comma-separated People API field mask for the
        returned data. Returns the Person resource, or {"error": ...} on API
        failure (including unknown person_name).
        """
        try:
            return get_contact_payload(
                GetContactRequest(
                    person_name=person_name,
                    person_fields=person_fields,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, person_name=person_name)

    @server.tool(name="create_contact")
    def create_contact(
        given_name: str | None = None,
        family_name: str | None = None,
        display_name: str | None = None,
        email_addresses: list[str] | None = None,
        phone_numbers: list[str] | None = None,
        organization: str | None = None,
        biography: str | None = None,
        person_fields: str = "names,emailAddresses,phoneNumbers,organizations,biographies",
    ) -> dict[str, Any]:
        """Create a new contact from the provided name/email/phone/organization fields.

        At least one field must be supplied. Returns the created Person
        resource (including its 'resourceName' for later get/update/delete),
        or {"error": ...} on API failure.
        """
        try:
            return create_contact_payload(
                CreateContactRequest(
                    given_name=given_name,
                    family_name=family_name,
                    display_name=display_name,
                    email_addresses=email_addresses or [],
                    phone_numbers=phone_numbers or [],
                    organization=organization,
                    biography=biography,
                    person_fields=person_fields,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc)

    @server.tool(name="update_contact")
    def update_contact(
        person_name: str,
        etag: str | None = None,
        given_name: str | None = None,
        family_name: str | None = None,
        display_name: str | None = None,
        email_addresses: list[str] | None = None,
        phone_numbers: list[str] | None = None,
        organization: str | None = None,
        biography: str | None = None,
        person_fields: str = "names,emailAddresses,phoneNumbers,organizations,biographies",
        sources: list[ReadSourceType] | None = None,
    ) -> dict[str, Any]:
        """Update fields on an existing contact identified by person_name.

        Only the supplied fields are patched (at least one is required); list
        fields like email_addresses replace the stored list wholesale. Pass the
        contact's current etag to guard against concurrent edits. Returns the
        updated Person resource, or {"error": ...} on API failure (e.g. a stale
        etag).
        """
        try:
            return update_contact_payload(
                UpdateContactRequest(
                    person_name=person_name,
                    etag=etag,
                    given_name=given_name,
                    family_name=family_name,
                    display_name=display_name,
                    email_addresses=email_addresses,
                    phone_numbers=phone_numbers,
                    organization=organization,
                    biography=biography,
                    person_fields=person_fields,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, person_name=person_name)

    @server.tool(name="delete_contact")
    async def delete_contact(
        person_name: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete a contact after interactive host confirmation.

        person_name is the contact resource name (e.g. 'people/c123'). Returns
        {"status": "deleted"} on success, {"status": "cancelled"} if the user
        declines, or {"error": ...} on API failure.
        """
        request = DeleteContactRequest(person_name=person_name)
        if not await confirm_destructive_action(
            ctx, "delete_contact", f"Permanently delete contact {request.person_name}?"
        ):
            return {"status": "cancelled", "person_name": request.person_name}
        try:
            return await run_blocking(delete_contact_payload, request)
        except HttpError as exc:
            return tool_error_payload(exc, person_name=person_name)

    @server.tool(name="list_contact_groups")
    def list_contact_groups(
        page_size: int = 100,
        page_token: str | None = None,
        group_fields: str = "name,groupType,memberCount,metadata",
    ) -> dict[str, Any]:
        """Page through the account's contact groups (labels).

        group_fields is a comma-separated People API field mask for group data.
        Returns the raw contactGroups.list payload ('contactGroups' list plus
        'nextPageToken'), or {"error": ...} on API failure.
        """
        try:
            return list_contact_groups_payload(
                ListContactGroupsRequest(page_size=page_size, page_token=page_token, group_fields=group_fields)
            )
        except HttpError as exc:
            return tool_error_payload(exc)

    @server.tool(name="create_contact_group")
    def create_contact_group(name: str) -> dict[str, Any]:
        """Create a new contact group (label) with the given display name.

        Returns the created ContactGroup resource (including its
        'resourceName'), or {"error": ...} on API failure.
        """
        try:
            return create_contact_group_payload(CreateContactGroupRequest(name=name))
        except HttpError as exc:
            return tool_error_payload(exc, name=name)

    @server.tool(name="modify_contact_group_members")
    def modify_contact_group_members(
        group_name: str,
        resource_names_to_add: list[str] | None = None,
        resource_names_to_remove: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add and/or remove contacts from a contact group.

        group_name is the group resource name (e.g. 'contactGroups/abc'; bare
        IDs are normalized), and the two lists hold contact resource names.
        At least one addition or removal is required. Returns the raw
        members.modify payload, or {"error": ...} on API failure.
        """
        try:
            return modify_contact_group_members_payload(
                ModifyContactGroupMembersRequest(
                    group_name=group_name,
                    resource_names_to_add=resource_names_to_add or [],
                    resource_names_to_remove=resource_names_to_remove or [],
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, group_name=group_name)
