"""Pydantic models for Drive tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..common.request_model import ToolRequestModel


class ListFilesRequest(ToolRequestModel):
    query: str | None = Field(default=None, description="Drive search query in Drive's q syntax; omitted lists every accessible file, trashed included.")
    page_size: int = Field(default=25, ge=1, le=1000, description="Maximum items to return.")
    page_token: str | None = Field(default=None, description="next_page_token from the previous page; omit for the first page.")
    order_by: str | None = Field(default=None, description="Comma-separated sort keys, each optionally followed by ' desc', e.g. 'folder,modifiedTime desc'.")
    corpora: Literal["user", "drive", "domain", "allDrives"] | None = Field(
        default=None,
        description="Collection to search: user, domain, drive (requires drive_id), or allDrives.",
    )
    drive_id: str | None = Field(default=None, description="Shared drive ID; required when corpora is 'drive'.")
    spaces: Literal["drive", "appDataFolder"] | None = Field(default=None, description="Storage space to search: drive or appDataFolder.")


class GetFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")


class CreateFolderRequest(ToolRequestModel):
    name: str = Field(description="Folder name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder ID as a one-element list (Drive allows one parent); empty means My Drive root.")


class CreateFileMetadataRequest(ToolRequestModel):
    name: str = Field(description="File name.")
    mime_type: str | None = Field(default=None, description="MIME type of the empty file, e.g. application/vnd.google-apps.document for a blank Doc.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    description: str | None = Field(default=None, description="Optional file description.")
    app_properties: dict[str, str] | None = Field(default=None, description="Private key/value pairs visible only to this server's OAuth client.")
    properties: dict[str, str] | None = Field(default=None, description="Public custom key/value pairs visible to any app.")


class UploadFileRequest(ToolRequestModel):
    local_path: str | None = Field(default=None, description="Path on the server's machine; only accepted when the server runs locally.")
    uploaded_file: str | None = Field(
        default=None, description="Upload ID (upl_...) from the Workspace Files picker or files_list_files."
    )
    name: str | None = Field(default=None, description="Name to store in Drive; defaults to the source file's name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    mime_type: str | None = Field(default=None, description="Content MIME type; defaults to the picker's type or a guess from the file extension.")
    resumable: bool = Field(default=True, description="Upload in chunks with progress reports; false sends one request.")
    if_exists: Literal["rename", "overwrite", "skip"] = Field(
        default="rename",
        description="When a same-named non-trashed file exists: rename the upload, overwrite that file's content, or skip.",
    )

    @model_validator(mode="after")
    def _validate_source(self) -> "UploadFileRequest":
        if sum(bool(value) for value in (self.local_path, self.uploaded_file)) != 1:
            raise ValueError("Exactly one of local_path or uploaded_file must be provided.")
        return self


class UpdateFileMetadataRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to patch.")
    name: str | None = Field(default=None, description="Updated filename.")
    description: str | None = Field(default=None, description="Updated description.")
    app_properties: dict[str, str] | None = Field(default=None, description="Private key/value pairs to add or change; unlisted keys are kept.")
    properties: dict[str, str] | None = Field(default=None, description="Public key/value pairs to add or change; unlisted keys are kept.")
    remove_property_keys: list[str] = Field(default_factory=list, description="Public property keys to clear.")
    remove_app_property_keys: list[str] = Field(default_factory=list, description="Private app property keys to delete.")


class UpdateFileContentRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to update.")
    local_path: str | None = Field(default=None, description="Path on the server's machine; only accepted when the server runs locally.")
    uploaded_file: str | None = Field(
        default=None, description="Upload ID (upl_...) from the Workspace Files picker or files_list_files."
    )
    mime_type: str | None = Field(default=None, description="MIME type of the replacement content; defaults to the picker's type or a guess from the extension.")
    resumable: bool = Field(default=True, description="Upload in chunks with progress reports; false sends one request.")
    keep_revision_forever: bool | None = Field(default=None, description="Keep the new revision forever (binary files only).")

    @model_validator(mode="after")
    def _validate_source(self) -> "UpdateFileContentRequest":
        if sum(bool(value) for value in (self.local_path, self.uploaded_file)) != 1:
            raise ValueError("Exactly one of local_path or uploaded_file must be provided.")
        return self


class MoveFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to move.")
    add_parent_ids: list[str] = Field(default_factory=list, description="Parent IDs to add.")
    remove_parent_ids: list[str] = Field(default_factory=list, description="Parent IDs to remove.")


class CopyFileRequest(ToolRequestModel):
    file_id: str = Field(description="Source Drive file ID.")
    name: str | None = Field(default=None, description="Optional copied file name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs for copied file.")
    description: str | None = Field(default=None, description="Description for copied file.")


class DeleteFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to delete or trash.")
    delete_mode: Literal["trash", "permanent"] = Field(
        default="trash",
        description="'trash' moves the file to Drive's trash (restorable); 'permanent' deletes it irreversibly after confirmation.",
    )
    confirm_permanent: bool = Field(
        default=True,
        description=(
            "Must stay true for delete_mode='permanent' (false is rejected); a permanent "
            "delete is always confirmed interactively. Ignored for delete_mode='trash'."
        ),
    )


class DownloadFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to download.")
    output_path: str = Field(description="Destination file path on the server's machine; parent directories are created.")
    overwrite: bool = Field(default=False, description="Replace an existing file at output_path; otherwise the call fails if it exists.")
    acknowledge_abuse: bool = Field(default=False, description="Download a file Google flagged as malware or abusive (owner or shared-drive organizer only).")


class ExportGoogleFileRequest(ToolRequestModel):
    file_id: str = Field(description="Google-native file ID (Docs/Sheets/Slides/etc).")
    mime_type: str = Field(description="Export MIME type valid for the source type, e.g. application/pdf or text/csv.")
    output_path: str = Field(description="Destination file path on the server's machine; parent directories are created.")
    overwrite: bool = Field(default=False, description="Replace an existing file at output_path; otherwise the call fails if it exists.")


class GetFileContentCapabilitiesRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")


class ListPermissionsRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    page_size: int = Field(default=50, ge=1, le=200, description="Maximum permissions to return.")
    page_token: str | None = Field(default=None, description="next_page_token from the previous page; omit for the first page.")


class GetPermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID.")


class CreatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    type: Literal["user", "group", "domain", "anyone"] = Field(
        description="Grantee type: user or group (needs email_address), domain (needs domain), or anyone."
    )
    role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"] = Field(
        description="Permission role (reader, commenter, writer, fileOrganizer, organizer, owner)."
    )
    email_address: str | None = Field(default=None, description="Email address of the user or group grantee.")
    domain: str | None = Field(default=None, description="Domain name for a domain grant, e.g. example.com.")
    allow_file_discovery: bool | None = Field(default=None, description="For domain or anyone grants: whether the file can be found by search.")
    expiration_time: str | None = Field(default=None, description="RFC3339 expiration timestamp.")
    send_notification_email: bool | None = Field(default=None, description="Email user or group grantees about the share; Google defaults to true.")
    email_message: str | None = Field(default=None, description="Plain-text message added to the notification email.")
    transfer_ownership: bool | None = Field(default=None, description="Required true when assigning owner role.")


class UpdatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID.")
    role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"] | None = Field(
        default=None, description="New access level; omit to keep the current role."
    )
    allow_file_discovery: bool | None = Field(default=None, description="For domain or anyone grants: whether the file can be found by search.")
    expiration_time: str | None = Field(default=None, description="Updated expiration timestamp (RFC3339).")
    remove_expiration: bool = Field(default=False, description="Clear the expiration so access no longer ends; overrides expiration_time.")
    transfer_ownership: bool | None = Field(default=None, description="Required true when transferring ownership.")


class DeletePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID to remove.")


class ListDrivesRequest(ToolRequestModel):
    page_size: int = Field(default=25, ge=1, le=100, description="Maximum Shared Drives to return.")
    page_token: str | None = Field(default=None, description="next_page_token from the previous page; omit for the first page.")
    query: str | None = Field(default=None, description="Filter in Drive's shared-drive search syntax, e.g. name contains 'Finance'.")


class GetDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID.")


class HideDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID to hide.")


class UnhideDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID to unhide.")


class FileOperationStatus(BaseModel):
    status: str = Field(description="Operation status.")
    file_id: str | None = Field(default=None, description="Related file ID.")
    details: dict[str, Any] | None = Field(default=None, description="Additional operation details.")
