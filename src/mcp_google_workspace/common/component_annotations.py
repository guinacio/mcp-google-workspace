"""Shared tool annotation defaults for FastMCP components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

_NAMESPACES = {
    "apps",
    "calendar",
    "chat",
    "docs",
    "drive",
    "forms",
    "gmail",
    "keep",
    "meet",
    "people",
    "sheets",
    "slides",
    "tasks",
}

_LOCAL_ONLY_TOOLS = {
    "get_current_date",
    "get_state",
    "get_timezone_info",
    "next_range",
    "patch_state",
    "prev_range",
    "set_state",
    "today",
}

_READ_ONLY_PREFIXES = (
    "check_",
    "download_",
    "export_",
    "find_",
    "get_",
    "list_",
    "read_",
    "search_",
    "summarize_",
)

_MUTATING_PREFIXES = (
    "add_",
    "append_",
    "apply_",
    "archive_",
    "batch_update_",
    "complete_",
    "copy_",
    "create_",
    "delete_",
    "end_",
    "hide_",
    "mark_",
    "modify_",
    "move_",
    "patch_",
    "post_",
    "remove_",
    "replace_",
    "reply_",
    "reschedule_",
    "respond_",
    "send_",
    "set_",
    "share_",
    "toggle_",
    "trash_",
    "unarchive_",
    "unhide_",
    "unshare_",
    "update_",
    "upload_",
)

_MUTATING_TOOLS = {
    "next_range",
    "prev_range",
    "today",
}

_DESTRUCTIVE_PREFIXES = (
    "delete_",
    "remove_",
    "trash_",
)

_DESTRUCTIVE_TOOLS = {
    "archive_note",
    "batch_delete",
    "cancel_meeting",
    "end_active_conference",
}

_IDEMPOTENT_TOOLS = {
    "apply_labels",
    "archive_note",
    "cancel_meeting",
    "complete_task",
    "create_meeting_from_slot",
    "hide_drive",
    "mark_as_not_spam",
    "mark_as_read",
    "mark_as_spam",
    "mark_as_unread",
    "patch_note_checklist",
    "patch_state",
    "replace_document_text",
    "replace_text_in_presentation",
    "reschedule_meeting",
    "respond_to_event",
    "set_form_publish_settings",
    "set_state",
    "today",
    "unarchive_note",
    "unhide_drive",
    "untrash_email",
    "untrash_thread",
    "update_contact",
    "update_draft",
    "update_event",
    "update_file_content",
    "update_file_metadata",
    "update_label",
    "update_message",
    "update_note",
    "update_permission",
    "update_sheet_values",
    "update_space",
    "update_task",
    "update_vacation_settings",
}


def _base_tool_name(name: str) -> str:
    namespace, _, remainder = name.partition("_")
    if namespace in _NAMESPACES and remainder:
        return remainder
    return name


def _is_read_only(base_name: str) -> bool:
    if base_name in _MUTATING_TOOLS:
        return False
    if base_name.startswith(_MUTATING_PREFIXES):
        return False
    return base_name.startswith(_READ_ONLY_PREFIXES)


def _is_destructive(base_name: str, *, read_only: bool) -> bool:
    if read_only:
        return False
    return base_name in _DESTRUCTIVE_TOOLS or base_name.startswith(_DESTRUCTIVE_PREFIXES)


def _is_idempotent(base_name: str, *, read_only: bool) -> bool:
    return read_only or base_name in _IDEMPOTENT_TOOLS


def _open_world(base_name: str) -> bool:
    return base_name not in _LOCAL_ONLY_TOOLS


def _merge_annotations(existing: ToolAnnotations | Mapping[str, Any] | None, *, base_name: str) -> ToolAnnotations:
    if isinstance(existing, ToolAnnotations):
        current = existing
    elif isinstance(existing, Mapping):
        current = ToolAnnotations.model_validate(existing)
    else:
        current = None

    read_only = _is_read_only(base_name)
    destructive = _is_destructive(base_name, read_only=read_only)
    idempotent = _is_idempotent(base_name, read_only=read_only)
    open_world = _open_world(base_name)

    return ToolAnnotations(
        title=current.title if current is not None else None,
        readOnlyHint=read_only if current is None or current.readOnlyHint is None else current.readOnlyHint,
        destructiveHint=destructive if current is None or current.destructiveHint is None else current.destructiveHint,
        idempotentHint=idempotent if current is None or current.idempotentHint is None else current.idempotentHint,
        openWorldHint=open_world if current is None or current.openWorldHint is None else current.openWorldHint,
        _meta=current._meta if current is not None else None,
    )


def apply_default_tool_annotations(server: FastMCP) -> None:
    for component_id, component in server._local_provider._components.items():
        if not component_id.startswith("tool:"):
            continue
        component.annotations = _merge_annotations(component.annotations, base_name=_base_tool_name(component.name))
