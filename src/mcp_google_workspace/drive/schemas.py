"""Pydantic models for Drive tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolRequestModel(BaseModel):
    """Base input model for MCP tools expecting object payloads."""

    model_config = {
        "json_schema_extra": {
            "description": (
                "Pass this as a JSON object payload to the tool. "
                "Do not pass a raw string for the full request."
            )
        }
    }


class ListFilesRequest(ToolRequestModel):
    query: str | None = Field(default=None, description="Drive search query (q parameter).")
    page_size: int = Field(default=25, ge=1, le=1000, description="Maximum items to return.")
    page_token: str | None = Field(default=None, description="Token for next result page.")
    order_by: str | None = Field(default=None, description="Sort expression (e.g. modifiedTime desc).")
    corpora: str | None = Field(
        default=None,
        description="Search corpus (user, domain, drive, or allDrives).",
    )
    drive_id: str | None = Field(default=None, description="Shared Drive ID when searching a specific drive.")
    include_items_from_all_drives: bool = Field(
        default=True,
        description="Include My Drive + Shared Drives items when supported.",
    )
    supports_all_drives: bool = Field(
        default=True,
        description="Indicates the application supports both My Drive and Shared Drives.",
    )
    spaces: str | None = Field(default=None, description="Spaces to query, usually 'drive'.")
    fields: str = Field(
        default="nextPageToken, files(id,name,mimeType,parents,modifiedTime,size,driveId,webViewLink)",
        description="Partial response fields selector.",
    )


class GetFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(
        default="id,name,mimeType,size,parents,owners,permissions,driveId,webViewLink,webContentLink,capabilities,exportLinks",
        description="Partial response fields selector.",
    )


class CreateFolderRequest(ToolRequestModel):
    name: str = Field(description="Folder name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs (single parent recommended).")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,mimeType,parents,driveId,webViewLink", description="Fields selector.")


class CreateFileMetadataRequest(ToolRequestModel):
    name: str = Field(description="File name.")
    mime_type: str | None = Field(default=None, description="Explicit MIME type for metadata-only file creation.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    description: str | None = Field(default=None, description="Optional file description.")
    app_properties: dict[str, str] | None = Field(default=None, description="Private appProperties map.")
    properties: dict[str, str] | None = Field(default=None, description="Public custom properties map.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,mimeType,parents,driveId,webViewLink", description="Fields selector.")


class UploadFileRequest(ToolRequestModel):
    local_path: str = Field(description="Path to local file to upload.")
    name: str | None = Field(default=None, description="Name to store in Drive (defaults to local filename).")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs.")
    mime_type: str | None = Field(default=None, description="Content MIME type; auto-detected when omitted.")
    resumable: bool = Field(default=True, description="Use resumable upload flow.")
    if_exists: Literal["rename", "overwrite", "skip"] = Field(
        default="rename",
        description="Behavior when a file with same name already exists in target parent scope.",
    )
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,mimeType,size,parents,driveId,webViewLink", description="Fields selector.")


class UpdateFileMetadataRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to patch.")
    name: str | None = Field(default=None, description="Updated filename.")
    description: str | None = Field(default=None, description="Updated description.")
    app_properties: dict[str, str] | None = Field(default=None, description="appProperties replacement/patch.")
    properties: dict[str, str] | None = Field(default=None, description="properties replacement/patch.")
    remove_property_keys: list[str] = Field(default_factory=list, description="Public property keys to clear.")
    remove_app_property_keys: list[str] = Field(default_factory=list, description="appProperties keys to clear.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,mimeType,parents,driveId,modifiedTime,webViewLink", description="Fields selector.")


class UpdateFileContentRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to update.")
    local_path: str = Field(description="Path to local file content.")
    mime_type: str | None = Field(default=None, description="Content MIME type.")
    resumable: bool = Field(default=True, description="Use resumable upload flow.")
    keep_revision_forever: bool | None = Field(default=None, description="Pin new revision when supported.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,mimeType,size,modifiedTime,driveId,webViewLink", description="Fields selector.")


class MoveFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to move.")
    add_parent_ids: list[str] = Field(default_factory=list, description="Parent IDs to add.")
    remove_parent_ids: list[str] = Field(default_factory=list, description="Parent IDs to remove.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,parents,driveId,webViewLink", description="Fields selector.")


class CopyFileRequest(ToolRequestModel):
    file_id: str = Field(description="Source Drive file ID.")
    name: str | None = Field(default=None, description="Optional copied file name.")
    parent_ids: list[str] = Field(default_factory=list, description="Parent folder IDs for copied file.")
    description: str | None = Field(default=None, description="Description for copied file.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,name,mimeType,parents,driveId,webViewLink", description="Fields selector.")


class DeleteFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to delete or trash.")
    delete_mode: Literal["trash", "permanent"] = Field(
        default="trash",
        description="Safer delete mode. 'trash' moves to trash, 'permanent' irreversibly deletes.",
    )
    confirm_permanent: bool = Field(
        default=True,
        description="Require interactive confirmation before permanent delete when true.",
    )
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")


class DownloadFileRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID to download.")
    output_path: str = Field(description="Destination path on local filesystem.")
    overwrite: bool = Field(default=False, description="Overwrite existing local file.")
    acknowledge_abuse: bool = Field(default=False, description="Acknowledge abusive content warning when required.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")


class ExportGoogleFileRequest(ToolRequestModel):
    file_id: str = Field(description="Google-native file ID (Docs/Sheets/Slides/etc).")
    mime_type: str = Field(description="Desired export MIME type.")
    output_path: str = Field(description="Destination path on local filesystem.")
    overwrite: bool = Field(default=False, description="Overwrite existing local file.")


class GetFileContentCapabilitiesRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")


class ListPermissionsRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    page_size: int = Field(default=50, ge=1, le=200, description="Maximum permissions to return.")
    page_token: str | None = Field(default=None, description="Token for next result page.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for Shared Drives when available.")
    fields: str = Field(default="nextPageToken,permissions(id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime)", description="Fields selector.")


class GetPermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for Shared Drives when available.")
    fields: str = Field(default="id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime", description="Fields selector.")


class CreatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    type: str = Field(description="Permission grantee type (user, group, domain, anyone).")
    role: str = Field(description="Permission role (reader, commenter, writer, fileOrganizer, organizer, owner).")
    email_address: str | None = Field(default=None, description="Email for user/group permissions.")
    domain: str | None = Field(default=None, description="Domain for domain permission type.")
    allow_file_discovery: bool | None = Field(default=None, description="Discoverability for anyone/domain links.")
    expiration_time: str | None = Field(default=None, description="RFC3339 expiration timestamp.")
    send_notification_email: bool | None = Field(default=None, description="Send email notification to target user/group.")
    email_message: str | None = Field(default=None, description="Custom notification email message.")
    transfer_ownership: bool | None = Field(default=None, description="Required true when assigning owner role.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for Shared Drives when available.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,type,role,emailAddress,domain,displayName,allowFileDiscovery,pendingOwner,expirationTime", description="Fields selector.")


class UpdatePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID.")
    role: str | None = Field(default=None, description="Updated role.")
    allow_file_discovery: bool | None = Field(default=None, description="Updated discoverability.")
    expiration_time: str | None = Field(default=None, description="Updated expiration timestamp (RFC3339).")
    remove_expiration: bool = Field(default=False, description="Remove permission expiration timestamp.")
    transfer_ownership: bool | None = Field(default=None, description="Required true when transferring ownership.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for Shared Drives when available.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")
    fields: str = Field(default="id,type,role,emailAddress,domain,displayName,allowFileDiscovery,pendingOwner,expirationTime", description="Fields selector.")


class DeletePermissionRequest(ToolRequestModel):
    file_id: str = Field(description="Drive file ID.")
    permission_id: str = Field(description="Permission ID to remove.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for Shared Drives when available.")
    supports_all_drives: bool = Field(default=True, description="Enable Shared Drives compatibility.")


class ListDrivesRequest(ToolRequestModel):
    page_size: int = Field(default=25, ge=1, le=100, description="Maximum Shared Drives to return.")
    page_token: str | None = Field(default=None, description="Token for next result page.")
    query: str | None = Field(default=None, description="Query string for drives.list.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for domain-managed drives.")
    fields: str = Field(default="nextPageToken,drives(id,name,hidden,createdTime,orgUnitId,restrictions,capabilities)", description="Fields selector.")


class GetDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for domain-managed drives.")
    fields: str = Field(default="id,name,hidden,createdTime,orgUnitId,restrictions,capabilities", description="Fields selector.")


class HideDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID to hide.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for domain-managed drives.")


class UnhideDriveRequest(ToolRequestModel):
    drive_id: str = Field(description="Shared Drive ID to unhide.")
    use_domain_admin_access: bool = Field(default=False, description="Use admin access for domain-managed drives.")


class FileOperationStatus(BaseModel):
    status: str = Field(description="Operation status.")
    file_id: str | None = Field(default=None, description="Related file ID.")
    details: dict[str, Any] | None = Field(default=None, description="Additional operation details.")
