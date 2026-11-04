"""Pydantic models for Drive tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..common.request_model import ToolRequestModel


class ListFilesRequest(ToolRequestModel):
    query: str | None = Field(default=None, description="Drive search query (q parameter).")
    page_size: int = Field(default=25, ge=1, le=1000, description="Maximum items to return.")
    page_token: str | None = Field(default=None, description="Token for next result page.")
    order_by: str | None = Field(default=None, description="Sort expression (e.g. modifiedTime desc).")
    corpora: Literal["user", "drive", "domain", "allDrives"] | None = Field(
        default=None,
        description="Search corpus (user, domain, drive, or allDrives).",
    )
    drive_id: str | None = Field(default=None, description="Shared Drive ID when searching a specific drive.")
    spaces: Literal["drive", "appDataFolder"] | None = Field(default=None, description="Space to query.")


class GetFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")


class CreateFolderRequest(ToolRequestModel):
    name: str = Field(description="Folder name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs (single parent recommended).")


class CreateFileMetadataRequest(ToolRequestModel):
    name: str = Field(description="File name.")
    mime_type: str | None = Field(default=None, description="Explicit MIME type for metadata-only file creation.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    description: str | None = Field(default=None, description="Optional file description.")
    app_properties: dict[str, str] | None = Field(default=None, description="Private appProperties map.")
    properties: dict[str, str] | None = Field(default=None, description="Public custom properties map.")


class UploadFileRequest(ToolRequestModel):
    local_path: str | None = Field(default=None, description="Local path (stdio/local use only).")
    uploaded_file: str | None = Field(
        default=None, description="Filename returned by the Workspace Files MCP App picker."
    )
    name: str | None = Field(default=None, description="Name to store in Drive (defaults to local filename).")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    mime_type: str | None = Field(default=None, description="Content MIME type; auto-detected when omitted.")
    resumable: bool = Field(default=True, description="Use resumable upload flow.")
    if_exists: Literal["rename", "overwrite", "skip"] = Field(
        default="rename",
        description="Behavior when a file with same name already exists in target parent scope.",
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
    app_properties: dict[str, str] | None = Field(default=None, description="appProperties replacement/patch.")
    properties: dict[str, str] | None = Field(default=None, description="properties replacement/patch.")
    remove_property_keys: list[str] = Field(default_factory=list, description="Public property keys to clear.")
    remove_app_property_keys: list[str] = Field(default_factory=list, description="appProperties keys to clear.")


class UpdateFileContentRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to update.")
    local_path: str | None = Field(default=None, description="Local path (stdio/local use only).")
    uploaded_file: str | None = Field(
        default=None, description="Filename returned by the Workspace Files MCP App picker."
    )
    mime_type: str | None = Field(default=None, description="Content MIME type.")
    resumable: bool = Field(default=True, description="Use resumable upload flow.")
    keep_revision_forever: bool | None = Field(default=None, description="Pin new revision when supported.")

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
        description="Safer delete mode. 'trash' moves to trash, 'permanent' irreversibly deletes.",
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
    output_path: str = Field(description="Destination path on local filesystem.")
    overwrite: bool = Field(default=False, description="Overwrite existing local file.")
    acknowledge_abuse: bool = Field(default=False, description="Acknowledge abusive content warning when required.")


class ExportGoogleFileRequest(ToolRequestModel):
    file_id: str = Field(description="Google-native file ID (Docs/Sheets/Slides/etc).")
    mime_type: str = Field(description="Desired export MIME type.")
    output_path: str = Field(description="Destination path on local filesystem.")
    overwrite: bool = Field(default=False, description="Overwrite existing local file.")


class GetFileContentCapabilitiesRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")


class ListPermissionsRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    page_size: int = Field(default=50, ge=1, le=200, description="Maximum permissions to return.")
    page_token: str | None = Field(default=None, description="Token for next result page.")


class GetPermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID.")


class CreatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    type: Literal["user", "group", "domain", "anyone"] = Field(
        description="Permission grantee type (user, group, domain, anyone)."
    )
    role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"] = Field(
        description="Permission role (reader, commenter, writer, fileOrganizer, organizer, owner)."
    )
    email_address: str | None = Field(default=None, description="Email for user/group permissions.")
    domain: str | None = Field(default=None, description="Domain for domain permission type.")
    allow_file_discovery: bool | None = Field(default=None, description="Discoverability for anyone/domain links.")
    expiration_time: str | None = Field(default=None, description="RFC3339 expiration timestamp.")
    send_notification_email: bool | None = Field(default=None, description="Send email notification to target user/group.")
    email_message: str | None = Field(default=None, description="Custom notification email message.")
    transfer_ownership: bool | None = Field(default=None, description="Required true when assigning owner role.")


class UpdatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID.")
    role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"] | None = Field(
        default=None, description="Updated role."
    )
    allow_file_discovery: bool | None = Field(default=None, description="Updated discoverability.")
    expiration_time: str | None = Field(default=None, description="Updated expiration timestamp (RFC3339).")
    remove_expiration: bool = Field(default=False, description="Remove permission expiration timestamp.")
    transfer_ownership: bool | None = Field(default=None, description="Required true when transferring ownership.")


class DeletePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID to remove.")


class ListDrivesRequest(ToolRequestModel):
    page_size: int = Field(default=25, ge=1, le=100, description="Maximum Shared Drives to return.")
    page_token: str | None = Field(default=None, description="Token for next result page.")
    query: str | None = Field(default=None, description="Query string for drives.list.")


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
