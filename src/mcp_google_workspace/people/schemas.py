"""Pydantic models for People tools."""

from __future__ import annotations

from pydantic import EmailStr, Field, model_validator

from ..common.request_model import ToolRequestModel

_DEFAULT_PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,biographies"
_DEFAULT_GROUP_FIELDS = "name,groupType,memberCount,metadata"


class ListContactsRequest(ToolRequestModel):
    page_size: int = Field(default=100, ge=1, le=1000)
    page_token: str | None = Field(default=None)
    person_fields: str = Field(default=_DEFAULT_PERSON_FIELDS)
    sort_order: str | None = Field(default=None)
    sources: list[str] = Field(default_factory=lambda: ["READ_SOURCE_TYPE_CONTACT"])


class SearchContactsRequest(ToolRequestModel):
    query: str = Field(min_length=1)
    page_size: int = Field(default=20, ge=1, le=100)
    read_mask: str = Field(default=_DEFAULT_PERSON_FIELDS)
    sources: list[str] = Field(default_factory=lambda: ["READ_SOURCE_TYPE_CONTACT"])


class GetContactRequest(ToolRequestModel):
    person_name: str = Field(description="Contact resource name, e.g. people/c123.")
    person_fields: str = Field(default=_DEFAULT_PERSON_FIELDS)
    sources: list[str] = Field(default_factory=lambda: ["READ_SOURCE_TYPE_CONTACT"])


class CreateContactRequest(ToolRequestModel):
    given_name: str | None = Field(default=None)
    family_name: str | None = Field(default=None)
    display_name: str | None = Field(default=None)
    email_addresses: list[EmailStr] = Field(default_factory=list)
    phone_numbers: list[str] = Field(default_factory=list)
    organization: str | None = Field(default=None)
    biography: str | None = Field(default=None)
    person_fields: str = Field(default=_DEFAULT_PERSON_FIELDS)

    @model_validator(mode="after")
    def _ensure_content(self) -> "CreateContactRequest":
        if not any([
            self.given_name,
            self.family_name,
            self.display_name,
            self.email_addresses,
            self.phone_numbers,
            self.organization,
            self.biography,
        ]):
            raise ValueError("At least one contact field must be provided")
        return self


class UpdateContactRequest(ToolRequestModel):
    person_name: str = Field(description="Contact resource name, e.g. people/c123.")
    etag: str | None = Field(default=None)
    given_name: str | None = Field(default=None)
    family_name: str | None = Field(default=None)
    display_name: str | None = Field(default=None)
    email_addresses: list[EmailStr] | None = Field(default=None)
    phone_numbers: list[str] | None = Field(default=None)
    organization: str | None = Field(default=None)
    biography: str | None = Field(default=None)
    person_fields: str = Field(default=_DEFAULT_PERSON_FIELDS)
    sources: list[str] = Field(default_factory=lambda: ["READ_SOURCE_TYPE_CONTACT"])

    @model_validator(mode="after")
    def _ensure_updates(self) -> "UpdateContactRequest":
        if all(
            value is None
            for value in (
                self.given_name,
                self.family_name,
                self.display_name,
                self.email_addresses,
                self.phone_numbers,
                self.organization,
                self.biography,
            )
        ):
            raise ValueError("At least one mutable contact field must be provided")
        return self


class DeleteContactRequest(ToolRequestModel):
    person_name: str = Field(description="Contact resource name.")


class ListContactGroupsRequest(ToolRequestModel):
    page_size: int = Field(default=100, ge=1, le=1000)
    page_token: str | None = Field(default=None)
    group_fields: str = Field(default=_DEFAULT_GROUP_FIELDS)


class CreateContactGroupRequest(ToolRequestModel):
    name: str = Field(description="Contact group display name.")


class ModifyContactGroupMembersRequest(ToolRequestModel):
    group_name: str = Field(description="Contact group resource name.")
    resource_names_to_add: list[str] = Field(default_factory=list)
    resource_names_to_remove: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_changes(self) -> "ModifyContactGroupMembersRequest":
        if not self.resource_names_to_add and not self.resource_names_to_remove:
            raise ValueError("At least one contact must be added or removed")
        return self
