"""FastMCP People tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
from .client import normalize_contact_group_name, normalize_person_name, people_service
from .schemas import (
    GROUP_FIELDS,
    PERSON_FIELDS,
    ContactGroupAction,
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
from .presentation import (
    contact_envelope,
    contact_group_envelope,
    contact_groups_page,
    contact_search_result,
    contacts_page,
    group_members_result,
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


# Structured name parts Google stores and accepts back; display and
# unstructured names are derived from them.
_WRITABLE_NAME_PARTS = (
    "givenName",
    "familyName",
    "middleName",
    "honorificPrefix",
    "honorificSuffix",
    "phoneticGivenName",
    "phoneticFamilyName",
    "phoneticMiddleName",
)


def _merge_stored_name(service: Any, resource_name: str, name: dict[str, Any]) -> dict[str, Any]:
    """The contact's stored name parts with the passed ones written over them.

    Google replaces the whole name on a names update, so a part the caller left
    out (the family name when only given_name is passed) would be cleared.
    """
    person = service.people().get(resourceName=resource_name, personFields="names").execute()
    stored: dict[str, Any] = next(iter(person.get("names") or []), {})
    merged = {part: stored[part] for part in _WRITABLE_NAME_PARTS if stored.get(part)}
    return {**merged, **name}


def update_contact_payload(request: UpdateContactRequest) -> dict[str, Any]:
    service = people_service()
    resource_name = normalize_person_name(request.person_name)
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
    if "names" in body:
        body["names"] = [_merge_stored_name(service, resource_name, body["names"][0])]
    return service.people().updateContact(
        resourceName=resource_name,
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

# The people_manage_contact_groups parameters each action reads.
_GROUP_ACTION_PARAMETERS: dict[str, frozenset[str]] = {
    "list": frozenset({"page_size", "page_token"}),
    "create": frozenset({"name"}),
    "modify_members": frozenset({"group_name", "resource_names_to_add", "resource_names_to_remove"}),
}


def _reject_unused(action: str, **values: Any) -> None:
    """Refuse a parameter the action does not read, rather than silently dropping it."""
    unused = sorted(
        key
        for key, value in values.items()
        if value not in (None, "", []) and key not in _GROUP_ACTION_PARAMETERS[action]
    )
    if unused:
        raise ValueError(f"{', '.join(unused)} not used by action {action!r}")


def register_tools(server: FastMCP) -> None:
    @server.tool(name="search_contacts")
    def search_contacts(
        query: Annotated[
            str | None,
            (
                "Text matched against the start of the words in contacts' names, email addresses, "
                "phone numbers and organizations, e.g. 'jo' finds 'John Smith' but 'ohn' does not; "
                "omit it to list every saved contact, page by page."
            ),
        ] = None,
        page_size: Annotated[
            int | None,
            (
                "Contacts per response. Listing (no query): 1 to 1000, default 100. Searching: 1 to 100, "
                "default 20, and Google returns at most 30 matches."
            ),
        ] = None,
        page_token: Annotated[
            str | None,
            (
                "The next_page_token of the previous listing response; omit for the first page. Listing "
                "only: a search has no further pages."
            ),
        ] = None,
        sort_order: Annotated[
            SortOrder | None,
            (
                "Order when listing: LAST_MODIFIED_ASCENDING (Google's default when omitted), "
                "LAST_MODIFIED_DESCENDING, FIRST_NAME_ASCENDING or LAST_NAME_ASCENDING. Not allowed with "
                "query, whose matches come in Google's relevance order."
            ),
        ] = None,
        sources: Annotated[list[ReadSourceType] | None, _SOURCES] = None,
    ) -> dict[str, Any]:
        """Find the user's saved Google contacts whose name, email, phone or organization starts with query, or, without query, list every saved contact page by page (sort_order, page_token). A search returns at most 30 matches in one response with no paging. Returns contacts, each with resource_name (the person_name other people_ tools take; also as id), etag, name, given_name, family_name, emails and phones (each {value, type}), organizations ({name, title}) and biography; plus count, total (all saved contacts, null for a search) and next_page_token (null for a search)."""
        read_sources: list[ReadSourceType] = sources or ["READ_SOURCE_TYPE_CONTACT"]
        if query is None or not query.strip():
            try:
                page = contacts_page(
                    list_contacts_payload(
                        ListContactsRequest(
                            page_size=page_size if page_size is not None else 100,
                            page_token=page_token,
                            sort_order=sort_order,
                            sources=read_sources,
                        )
                    )
                )
            except HttpError as exc:
                raise provider_tool_error(exc) from exc
        else:
            if page_token is not None:
                raise ValueError("page_token applies only to listing contacts (no query); a search has no paging")
            if sort_order is not None:
                raise ValueError("sort_order applies only to listing contacts (no query); search matches come in relevance order")
            try:
                page = contact_search_result(
                    search_contacts_payload(
                        SearchContactsRequest(
                            query=query,
                            page_size=page_size if page_size is not None else 20,
                            sources=read_sources,
                        )
                    )
                )
            except HttpError as exc:
                raise provider_tool_error(exc, query=query) from exc
        # A search reports no total and has no further page.
        total = page.get("total")
        return {
            "contacts": list(page["contacts"]),
            "count": page["count"],
            "total": int(total) if total is not None else None,
            "next_page_token": page.get("next_page_token"),
        }

    @server.tool(name="get_contact")
    def get_contact(
        person_name: str,
        sources: Annotated[list[ReadSourceType] | None, _SOURCES] = None,
    ) -> dict[str, Any]:
        """Read one Google contact by its resource name, found with people_search_contacts. Use it to fetch the current etag before people_update_contact. Returns contact: resource_name, etag, name, given_name, family_name, emails and phones (each {value, type}), organizations ({name, title}) and biography."""
        try:
            return {
                "contact": contact_envelope(
                    get_contact_payload(
                        GetContactRequest(
                            person_name=person_name,
                            sources=sources or ["READ_SOURCE_TYPE_CONTACT"],
                        )
                    )
                )
            }
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
        """Create a new contact in the user's Google Contacts; at least one field must be set. To change an existing contact use people_update_contact. Returns status 'ok' and contact, the stored contact: resource_name (the person_name other people_ tools take), etag, name, given_name, family_name, emails, phones, organizations and biography."""
        try:
            created = create_contact_payload(
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
            return {"status": "ok", "contact": contact_envelope(created)}
        except HttpError as exc:
            raise provider_tool_error(exc) from exc

    @server.tool(name="update_contact")
    def update_contact(
        person_name: str,
        etag: Annotated[
            str | None,
            (
                "The contact's current etag, from people_get_contact or people_search_contacts. "
                "Google requires it to check that the contact has not changed "
                "since it was read, and rejects a stale one."
            ),
        ] = None,
        given_name: Annotated[
            str | None,
            (
                "New given (first) name; the stored family and middle names are kept unless passed."
            ),
        ] = None,
        family_name: Annotated[
            str | None,
            (
                "New family (last) name; the stored given and middle names are kept unless passed."
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
        """Change fields of an existing Google contact. Only the fields passed are written and each replaces the stored value: list fields replace the whole list, and a name part left out keeps its stored value. Get the current etag first with people_get_contact. Returns status 'ok' and contact, the contact as stored now: resource_name, its new etag (needed for a further update), name, given_name, family_name, emails, phones, organizations and biography."""
        try:
            updated = update_contact_payload(
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
            return {"status": "ok", "contact": contact_envelope(updated)}
        except HttpError as exc:
            raise provider_tool_error(exc, person_name=person_name) from exc

    @server.tool(name="delete_contact")
    async def delete_contact(
        person_name: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete one Google contact, found with people_search_contacts. The user is asked to confirm first, and the call returns status 'cancelled' if declined. Returns status 'deleted' with the person_name (people/...). These tools cannot restore a deleted contact."""
        request = DeleteContactRequest(person_name=person_name)
        try:
            return await run_blocking(delete_contact_payload, request)
        except HttpError as exc:
            raise provider_tool_error(exc, person_name=person_name) from exc

    @server.tool(name="manage_contact_groups")
    async def manage_contact_groups(
        action: Annotated[
            ContactGroupAction,
            (
                "'list' pages through the user's contact groups; 'create' adds an empty group called name; "
                "'modify_members' adds contacts to and removes contacts from the group group_name."
            ),
        ],
        name: Annotated[
            str | None,
            "Name of the new group; required for 'create'. Google rejects a name the user already has for a group.",
        ] = None,
        group_name: Annotated[
            str | None,
            (
                "Contact group resource name, e.g. 'contactGroups/myContacts', as resource_name in 'list' "
                "results; required for 'modify_members'. A bare ID is prefixed automatically."
            ),
        ] = None,
        resource_names_to_add: Annotated[
            list[str] | None,
            (
                "For 'modify_members': contact resource names (people/...) from people_search_contacts to "
                "add to the group; this list or resource_names_to_remove must be non-empty."
            ),
        ] = None,
        resource_names_to_remove: Annotated[
            list[str] | None,
            "For 'modify_members': contact resource names (people/...) to remove from the group.",
        ] = None,
        page_size: Annotated[int | None, "For 'list': contact groups per page, 1 to 1000, default 100."] = None,
        page_token: Annotated[
            str | None, "For 'list': the next_page_token of the previous 'list' response; omit for the first page."
        ] = None,
    ) -> dict[str, Any]:
        """Work with the user's contact groups (labels): 'list' pages through them, system groups such as myContacts and starred included; 'create' adds an empty group; 'modify_members' adds and removes contacts (from people_search_contacts) in one group in a single call. Every result echoes action. 'list' returns contact_groups (resource_name, the group_name to pass; name; group_type 'user' or 'system'; member_count), count, total and next_page_token; 'create' returns status 'ok' and contact_group; 'modify_members' returns status 'ok', group_name, not_found and cannot_remove_last_group (both empty when every change applied)."""
        _reject_unused(
            action,
            name=name,
            group_name=group_name,
            resource_names_to_add=resource_names_to_add,
            resource_names_to_remove=resource_names_to_remove,
            page_size=page_size,
            page_token=page_token,
        )
        if action == "list":
            try:
                groups = contact_groups_page(
                    await run_blocking(
                        list_contact_groups_payload,
                        ListContactGroupsRequest(
                            page_size=page_size if page_size is not None else 100, page_token=page_token
                        ),
                    )
                )
            except HttpError as exc:
                raise provider_tool_error(exc, action=action) from exc
            total = groups["total"]
            return {
                "action": "list",
                "contact_groups": list(groups["contact_groups"]),
                "count": groups["count"],
                "total": int(total) if total is not None else None,
                "next_page_token": groups["next_page_token"],
            }
        if action == "create":
            if not name:
                raise ValueError("action 'create' requires name")
            try:
                created = await run_blocking(create_contact_group_payload, CreateContactGroupRequest(name=name))
            except HttpError as exc:
                raise provider_tool_error(exc, action=action, name=name) from exc
            return {"status": "ok", "action": "create", "contact_group": contact_group_envelope(created)}
        if not group_name:
            raise ValueError("action 'modify_members' requires group_name")
        request = ModifyContactGroupMembersRequest(
            group_name=group_name,
            resource_names_to_add=resource_names_to_add or [],
            resource_names_to_remove=resource_names_to_remove or [],
        )
        try:
            members = group_members_result(
                await run_blocking(modify_contact_group_members_payload, request),
                group_name=normalize_contact_group_name(request.group_name),
            )
        except HttpError as exc:
            raise provider_tool_error(exc, action=action, group_name=group_name) from exc
        return {
            "status": "ok",
            "action": "modify_members",
            "group_name": str(members["group_name"]),
            "not_found": list(members["not_found"]),
            "cannot_remove_last_group": list(members["cannot_remove_last_group"]),
        }
