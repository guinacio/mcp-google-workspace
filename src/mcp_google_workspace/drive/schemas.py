"""Pydantic models for Drive tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..common.request_model import ToolRequestModel

FileKind = Literal["folder", "document", "spreadsheet", "presentation", "form", "image", "video", "audio"]
PermissionRole = Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"]
PermissionType = Literal["user", "group", "domain", "anyone"]


class ListFilesRequest(ToolRequestModel):
    query: str | None = Field(default=None, description="Drive search query in Drive's q syntax, combined with the other filters by AND.")
    folder_id: str | None = Field(default=None, description="Only direct children of this folder.")
    kind: FileKind | None = Field(default=None, description="Only files of this kind.")
    modified_after: str | None = Field(default=None, description="Only files modified after this RFC3339 time or YYYY-MM-DD date.")
    full_text: str | None = Field(default=None, description="Only files whose name or content contains this text.")
    page_size: int = Field(default=25, ge=1, le=1000, description="Maximum items to return.")
    page_token: str | None = Field(default=None, description="next_page_token from the previous page; omit for the first page.")
    order_by: str | None = Field(default=None, description="Comma-separated sort keys, each optionally followed by ' desc', e.g. 'folder,modifiedTime desc'.")
    drive_id: str | None = Field(default=None, description="Shared drive ID; limits the search to that shared drive.")


class GetFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")


class CreateFolderRequest(ToolRequestModel):
    name: str = Field(description="Folder name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder ID as a one-element list (Drive allows one parent); empty means My Drive root.")


class UploadFileRequest(ToolRequestModel):
    local_path: str | None = Field(default=None, description="Path on the server's machine; only accepted when the server runs locally.")
    uploaded_file: str | None = Field(
        default=None, description="Upload ID (upl_...) from the Workspace Files picker or uploads_list_files."
    )
    file_id: str | None = Field(default=None, description="Existing Drive file whose content is replaced.")
    name: str | None = Field(default=None, description="Name to store in Drive; defaults to the source file's name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    mime_type: str | None = Field(default=None, description="Content MIME type, or the type of an empty file created from metadata.")
    description: str | None = Field(default=None, description="Description of the new or overwritten file.")
    resumable: bool = Field(default=True, description="Upload in chunks with progress reports; false sends one request.")
    if_exists: Literal["rename", "overwrite", "skip"] | None = Field(
        default=None,
        description="When a same-named non-trashed file exists: rename the upload (default), overwrite that file's content, or skip.",
    )
    keep_revision_forever: bool | None = Field(default=None, description="Keep the new revision forever (file_id mode, binary files only).")

    @model_validator(mode="after")
    def _validate_source(self) -> "UploadFileRequest":
        if self.local_path and self.uploaded_file:
            raise ValueError("Give at most one of local_path or uploaded_file, not both.")
        has_source = bool(self.local_path or self.uploaded_file)
        if self.file_id is not None:
            if not has_source:
                raise ValueError(
                    "drive_upload_file with file_id replaces that file's content: give local_path or uploaded_file."
                )
            conflicting = [
                field
                for field, value in (
                    ("name", self.name),
                    ("parent_ids", self.parent_ids or None),
                    ("if_exists", self.if_exists),
                    ("description", self.description),
                )
                if value is not None
            ]
            if conflicting:
                raise ValueError(
                    f"{', '.join(conflicting)} cannot be combined with file_id: replacing a file's content keeps its "
                    "name, folder and description; change those with drive_update_file_metadata or drive_move_file."
                )
            return self
        if self.keep_revision_forever is not None:
            raise ValueError("keep_revision_forever applies only when file_id replaces an existing file's content.")
        if not has_source:
            if not self.name:
                raise ValueError(
                    "drive_upload_file without local_path or uploaded_file creates an empty file: name is required."
                )
            if self.if_exists is not None:
                raise ValueError(
                    "if_exists applies only to content uploads; an empty file created from metadata is always new."
                )
        return self


class UpdateFileMetadataRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to patch.")
    name: str | None = Field(default=None, description="Updated filename.")
    description: str | None = Field(default=None, description="Updated description.")
    app_properties: dict[str, str] | None = Field(default=None, description="Private key/value pairs to add or change; unlisted keys are kept.")
    properties: dict[str, str] | None = Field(default=None, description="Public key/value pairs to add or change; unlisted keys are kept.")
    remove_property_keys: list[str] = Field(default_factory=list, description="Public property keys to clear.")
    remove_app_property_keys: list[str] = Field(default_factory=list, description="Private app property keys to delete.")


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


class DownloadFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to download or export.")
    output_path: str = Field(description="Destination file path on the server's machine; parent directories are created.")
    export_mime_type: str | None = Field(default=None, description="Export format for a Google-native file; ignored for stored files.")
    overwrite: bool = Field(default=False, description="Replace an existing file at output_path; otherwise the call fails if it exists.")
    acknowledge_abuse: bool = Field(default=False, description="Download a file Google flagged as malware or abusive (owner or shared-drive organizer only).")


class ListPermissionsRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str | None = Field(default=None, description="Return only this permission.")
    page_size: int = Field(default=50, ge=1, le=200, description="Maximum permissions to return.")
    page_token: str | None = Field(default=None, description="next_page_token from the previous page; omit for the first page.")


class CreatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    type: PermissionType = Field(
        description="Grantee type: user or group (needs email_address), domain (needs domain), or anyone."
    )
    role: PermissionRole = Field(
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
    role: PermissionRole | None = Field(
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
    drive_id: str | None = Field(default=None, description="Return only this shared drive.")
    page_size: int = Field(default=25, ge=1, le=100, description="Maximum Shared Drives to return.")
    page_token: str | None = Field(default=None, description="next_page_token from the previous page; omit for the first page.")
    query: str | None = Field(default=None, description="Filter in Drive's shared-drive search syntax, e.g. name contains 'Finance'.")


class UpdateDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID.")
    hidden: bool = Field(description="True hides the shared drive from the default view; false shows it again.")


class FileOperationStatus(BaseModel):
    status: str = Field(description="Operation status.")
    file_id: str | None = Field(default=None, description="Related file ID.")
    details: dict[str, Any] | None = Field(default=None, description="Additional operation details.")
