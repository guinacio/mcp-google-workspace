"""FastMCP People tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.confirmation import confirm_destructive_action
from ..common.errors import provider_tool_error
from .client import normalize_contact_group_name, normalize_person_name, people_service
from .schemas import (
    GROUP_FIELDS,
    PERSON_FIELDS,
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
        personFields=PERSON_FIELDS,
        sortOrder=request.sort_order,
        sources=request.sources,
    ).execute()


def search_contacts_payload(request: SearchContactsRequest) -> dict[str, Any]:
    service = people_service()
    # People API quirk: searchContacts reads from a lazily-populated server-side
    # cache, and Google's docs instruct clients to send an empty-query warmup
    # request first so the real search sees fresh results.
    # https://developers.google.com/people/v1/contacts#search_the_users_contacts
    service.people().searchContacts(query="", readMask=PERSON_FIELDS, pageSize=1, sources=request.sources).execute()
    return service.people().searchContacts(
        query=request.query,
        readMask=PERSON_FIELDS,
        pageSize=request.page_size,
        sources=request.sources,
    ).execute()


def get_contact_payload(request: GetContactRequest) -> dict[str, Any]:
    service = people_service()
    return service.people().get(
        resourceName=normalize_person_name(request.person_name),
        personFields=PERSON_FIELDS,
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
    return service.people().createContact(personFields=PERSON_FIELDS, body=body).execute()


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
        personFields=PERSON_FIELDS,
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
        groupFields=GROUP_FIELDS,
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


_SOURCES = (
    "Which data each returned person carries: READ_SOURCE_TYPE_CONTACT (the saved contact, the default "
    "when omitted), READ_SOURCE_TYPE_PROFILE (the person's Google profile, merged in) and "
    "READ_SOURCE_TYPE_DOMAIN_CONTACT (the organization's shared domain contacts)."
)
_DISPLAY_NAME = (
    "Sent as the name's displayName, which the People API documents as output-only (Google formats it "
    "from the structured name), so it is not expected to be stored; set given_name and family_name instead."
)


def register_tools(server: FastMCP) -> None:
    @server.tool(name="list_contacts")
    def list_contacts(
        page_size: Annotated[int, "Contacts per page, 1 to 1000."] = 100,
        page_token: str | None = None,
        sort_order: Annotated[
            SortOrder | None,
            (
                "Order of the contacts: LAST_MODIFIED_ASCENDING (Google's default when omitted), "
                "LAST_MODIFIED_DESCENDING, FIRST_NAME_ASCENDING or LAST_NAME_ASCENDING."
            ),
        ] = None,
        sources: Annotated[list[ReadSourceType] | None, _SOURCES] = None,
    ) -> dict[str, Any]:
        """List the user's saved Google contacts, one page at a time. To find a contact by name, email or phone use people_search_contacts instead. Returns the Google connections.list response: connections (Person resources with resourceName, etag, names, emailAddresses, phoneNumbers, organizations and biographies), nextPageToken and totalItems."""
        try:
            return list_contacts_payload(
                ListContactsRequest(
                    page_size=page_size,
                    page_token=page_token,
                    sort_order=sort_order,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc) from exc

    @server.tool(name="search_contacts")
    def search_contacts(
        query: Annotated[
            str,
            (
                "Text matched against the start of the words in contacts' names, email addresses, "
                "phone numbers and organizations, e.g. 'jo' finds 'John Smith' but 'ohn' does not."
            ),
        ],
        page_size: Annotated[int, "Maximum matches to return, 1 to 100; Google returns at most 30."] = 20,
        sources: Annotated[list[ReadSourceType] | None, _SOURCES] = None,
    ) -> dict[str, Any]:
        """Find the user's saved Google contacts whose name, email, phone or organization starts with the query text. To page through every contact use people_list_contacts; there is no paging here. Returns the Google searchContacts response: results, each with a person (resourceName, etag, names, emailAddresses, phoneNumbers, organizations and biographies); results is missing when nothing matches."""
        try:
            return search_contacts_payload(
                SearchContactsRequest(
                    query=query,
                    page_size=page_size,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, query=query) from exc

    @server.tool(name="get_contact")
    def get_contact(
        person_name: str,
        sources: Annotated[list[ReadSourceType] | None, _SOURCES] = None,
    ) -> dict[str, Any]:
        """Read one Google contact by its resource name, found with people_search_contacts or people_list_contacts. Use it to fetch the current etag before people_update_contact. Returns the Google Person resource: resourceName, etag, names, emailAddresses, phoneNumbers, organizations and biographies."""
        try:
            return get_contact_payload(
                GetContactRequest(
                    person_name=person_name,
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, person_name=person_name) from exc

    @server.tool(name="create_contact")
    def create_contact(
        given_name: str | None = None,
        family_name: str | None = None,
        display_name: Annotated[str | None, _DISPLAY_NAME] = None,
        email_addresses: Annotated[
            list[str] | None, "Email addresses to store, in order; each must be a valid address."
        ] = None,
        phone_numbers: Annotated[
            list[str] | None, "Phone numbers to store, in order, as written, e.g. '+1 650 555 0100'."
        ] = None,
        organization: str | None = None,
        biography: str | None = None,
    ) -> dict[str, Any]:
        """Create a new contact in the user's Google Contacts; at least one field must be set. To change an existing contact use people_update_contact. Returns the created Google Person resource: resourceName (the person_name other people_ tools take), etag, and the stored names, emailAddresses, phoneNumbers, organizations and biographies."""
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
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc) from exc

    @server.tool(name="update_contact")
    def update_contact(
        person_name: str,
        etag: Annotated[
            str | None,
            (
                "The contact's current etag, from people_get_contact, people_list_contacts or "
                "people_search_contacts. Google requires it to check that the contact has not changed "
                "since it was read, and rejects a stale one."
            ),
        ] = None,
        given_name: Annotated[
            str | None,
            (
                "New given (first) name. given_name, family_name and display_name are written together as "
                "the contact's one name, so a part left out is cleared; pass family_name too to keep it."
            ),
        ] = None,
        family_name: Annotated[
            str | None,
            (
                "New family (last) name. Written together with given_name as the contact's one name, so "
                "pass given_name too to keep it."
            ),
        ] = None,
        display_name: Annotated[str | None, _DISPLAY_NAME] = None,
        email_addresses: Annotated[
            list[str] | None,
            "Replacement email addresses: replace every stored address, and [] removes them all; each must be valid.",
        ] = None,
        phone_numbers: Annotated[
            list[str] | None, "Replacement phone numbers: replace every stored number, and [] removes them all."
        ] = None,
        organization: Annotated[
            str | None, "Replacement company name; replaces the stored organization entry, job title included."
        ] = None,
        biography: Annotated[str | None, "Replacement notes; replaces the stored notes."] = None,
        sources: Annotated[list[ReadSourceType] | None, _SOURCES] = None,
    ) -> dict[str, Any]:
        """Change fields of an existing Google contact. Only the fields passed are written and each replaces the stored value: list fields replace the whole list, and the name parts are written together as one name. Get the current etag first with people_get_contact. Returns the updated Google Person resource, with its new etag, names, emailAddresses, phoneNumbers, organizations and biographies."""
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
                    sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, person_name=person_name) from exc

    @server.tool(name="delete_contact")
    async def delete_contact(
        person_name: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete one Google contact, found with people_search_contacts or people_list_contacts. The user is asked to confirm first: the call returns status 'cancelled' if declined, and fails with confirmation_required when the client cannot ask. Returns status 'deleted' with the person_name (people/...). These tools cannot restore a deleted contact."""
        request = DeleteContactRequest(person_name=person_name)
        if not await confirm_destructive_action(
            ctx, "delete_contact", f"Permanently delete contact {request.person_name}?"
        ):
            return {"status": "cancelled", "person_name": request.person_name}
        try:
            return await run_blocking(delete_contact_payload, request)
        except HttpError as exc:
            raise provider_tool_error(exc, person_name=person_name) from exc

    @server.tool(name="list_contact_groups")
    def list_contact_groups(
        page_size: Annotated[int, "Contact groups per page, 1 to 1000."] = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List the user's contact groups (labels), including system groups such as myContacts and starred. To change a group's members use people_modify_contact_group_members, and to add a group use people_create_contact_group. Returns the Google contactGroups.list response: contactGroups (each with resourceName, etag, name, groupType and memberCount), nextPageToken and totalItems."""
        try:
            return list_contact_groups_payload(
                ListContactGroupsRequest(page_size=page_size, page_token=page_token)
            )
        except HttpError as exc:
            raise provider_tool_error(exc) from exc

    @server.tool(name="create_contact_group")
    def create_contact_group(
        name: Annotated[str, "Name of the new group; Google rejects a name the user already has for a group."],
    ) -> dict[str, Any]:
        """Create a new, empty contact group (label) in the user's Google Contacts. Add contacts to it afterwards with people_modify_contact_group_members. Returns the created Google ContactGroup resource: resourceName (contactGroups/..., the group_name other people_ tools take), etag, name and groupType."""
        try:
            return create_contact_group_payload(CreateContactGroupRequest(name=name))
        except HttpError as exc:
            raise provider_tool_error(exc, name=name) from exc

    @server.tool(name="modify_contact_group_members")
    def modify_contact_group_members(
        group_name: str,
        resource_names_to_add: list[str] | None = None,
        resource_names_to_remove: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add contacts to and remove contacts from one contact group in a single call; at least one of the two lists must be non-empty. Contact resource names come from people_search_contacts or people_list_contacts, group names from people_list_contact_groups. Returns the Google members.modify response: notFoundResourceNames and canNotRemoveLastContactGroupResourceNames list the contacts that were not changed, and both are missing when every change applied."""
        try:
            return modify_contact_group_members_payload(
                ModifyContactGroupMembersRequest(
                    group_name=group_name,
                    resource_names_to_add=resource_names_to_add or [],
                    resource_names_to_remove=resource_names_to_remove or [],
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, group_name=group_name) from exc
