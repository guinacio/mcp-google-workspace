"""Shared tool annotation defaults for FastMCP components."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any, Protocol, cast

from fastmcp import FastMCP
from mcp.types import ToolAnnotations


class _ToolComponent(Protocol):
    name: str
    title: str | None
    description: str | None
    tags: set[str]
    parameters: dict[str, Any] | None
    fn: Any
    annotations: ToolAnnotations | Mapping[str, Any] | None

_NAMESPACES = {
    "apps",
    "calendar",
    "chat",
    "docs",
    "drive",
    "forms",
    "gemini",
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

_NAMESPACE_TITLES = {
    "apps": "Workspace Apps",
    "calendar": "Calendar",
    "chat": "Chat",
    "docs": "Docs",
    "drive": "Drive",
    "forms": "Forms",
    "gemini": "Gemini",
    "gmail": "Gmail",
    "keep": "Keep",
    "meet": "Meet",
    "people": "People",
    "sheets": "Sheets",
    "slides": "Slides",
    "tasks": "Tasks",
}

_NAMESPACE_TAGS = {
    "apps": {"dashboard", "workspace-ui", "assistant-workflow"},
    "calendar": {"calendar", "events", "schedule", "meetings"},
    "chat": {"chat", "messaging", "spaces", "channels"},
    "docs": {"documents", "docs", "writing"},
    "drive": {"drive", "storage", "files", "folders", "shared-drive"},
    "forms": {"forms", "surveys"},
    "gemini": {"ai", "llm", "generation"},
    "gmail": {"gmail", "email", "mail", "inbox", "messages"},
    "keep": {"keep", "notes", "checklists"},
    "meet": {"meet", "video", "conference"},
    "people": {"people", "contacts", "directory"},
    "sheets": {"sheets", "spreadsheets", "tables"},
    "slides": {"slides", "presentations", "decks"},
    "tasks": {"tasks", "todos", "task-management"},
}

_TAG_STOPWORDS = {"a", "an", "as", "by", "for", "from", "in", "of", "the", "to"}

_ACTION_OBJECT_FALLBACKS = {
    "calendar": "event",
    "chat": "message",
    "drive": "file",
    "gmail": "email",
    "keep": "note",
    "tasks": "task",
}

_SPECIAL_TOOL_DESCRIPTIONS = {
    "get_current_date": "Get the current date and time in the user's configured calendar timezone.",
    "get_timezone_info": "Get the user's calendar timezone and localized current time details.",
    "mark_as_not_spam": "Mark the Gmail message as not spam and optionally return it to the inbox.",
    "mark_as_read": "Mark the Gmail message as read.",
    "mark_as_spam": "Mark the Gmail message as spam.",
    "mark_as_unread": "Mark the Gmail message as unread.",
    "next_range": "Move the workspace dashboard or calendar view to the next date range.",
    "patch_state": "Update the workspace dashboard session state with a partial patch.",
    "prev_range": "Move the workspace dashboard or calendar view to the previous date range.",
    "today": "Move the workspace dashboard or calendar view back to today.",
    "untrash_email": "Restore the Gmail message from trash.",
    "untrash_thread": "Restore the Gmail thread from trash.",
}


def _server_namespace(server: FastMCP) -> str | None:
    prefix, _, _ = server.name.partition("-")
    if prefix in _NAMESPACES:
        return prefix
    return None


def _split_tool_name(name: str, namespace_hint: str | None = None) -> tuple[str | None, str]:
    namespace, _, remainder = name.partition("_")
    if namespace in _NAMESPACES and remainder:
        return namespace, remainder
    if namespace_hint in _NAMESPACES:
        return namespace_hint, name
    return None, name


def _base_tool_name(name: str, namespace_hint: str | None = None) -> str:
    return _split_tool_name(name, namespace_hint=namespace_hint)[1]


def _humanize_segment(segment: str) -> str:
    special = {
        "api": "API",
        "bcc": "BCC",
        "cc": "CC",
        "csv": "CSV",
        "html": "HTML",
        "id": "ID",
        "ids": "IDs",
        "json": "JSON",
        "pdf": "PDF",
        "rsvp": "RSVP",
        "ui": "UI",
        "uri": "URI",
        "url": "URL",
    }
    return special.get(segment, segment.upper() if segment.isupper() else segment.capitalize())


def _humanize_tool_title(name: str, namespace_hint: str | None = None) -> str:
    namespace, base_name = _split_tool_name(name, namespace_hint=namespace_hint)
    words = [_humanize_segment(segment) for segment in base_name.split("_") if segment]
    if namespace is None:
        return " ".join(words) or name
    namespace_title = _NAMESPACE_TITLES.get(namespace, namespace.capitalize())
    return f"{namespace_title} {' '.join(words)}".strip()


def _tool_tags(name: str, namespace_hint: str | None = None) -> set[str]:
    namespace, base_name = _split_tool_name(name, namespace_hint=namespace_hint)
    tags = {"google-workspace"}
    if namespace is not None:
        tags.add(namespace)
        tags.update(_NAMESPACE_TAGS.get(namespace, set()))
        tags.add(f"google-{namespace}")
    parts = [segment for segment in base_name.split("_") if segment and segment not in _TAG_STOPWORDS]
    tags.update(parts)
    tags.add(base_name.replace("_", "-"))
    return tags


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

    annotations = ToolAnnotations(
        title=current.title if current is not None else None,
        readOnlyHint=read_only if current is None or current.readOnlyHint is None else current.readOnlyHint,
        destructiveHint=destructive if current is None or current.destructiveHint is None else current.destructiveHint,
        idempotentHint=idempotent if current is None or current.idempotentHint is None else current.idempotentHint,
        openWorldHint=open_world if current is None or current.openWorldHint is None else current.openWorldHint,
    )
    existing_meta = getattr(current, "_meta", None) if current is not None else None
    if existing_meta is not None:
        setattr(annotations, "_meta", existing_meta)
    return annotations


def _namespace_display(namespace: str | None) -> str:
    if namespace is None:
        return "Google Workspace"
    return _NAMESPACE_TITLES.get(namespace, f"Google {namespace.capitalize()}")


def _tool_subject(base_name: str) -> str:
    words = base_name.split("_")
    if not words:
        return "resource"
    verb = words[0]
    remainder = " ".join(words[1:]).strip()
    if verb in {"get", "list", "search", "read", "download", "export", "summarize", "check", "find"} and remainder:
        return remainder
    if verb in {"create", "update", "delete", "move", "copy", "share", "unshare", "mark", "set", "patch", "respond", "reschedule", "cancel", "upload", "send", "add", "remove", "replace", "modify", "complete", "archive", "unarchive", "trash", "untrash", "reply", "hide", "unhide", "post", "append", "generate"} and remainder:
        return remainder
    return base_name.replace("_", " ")


_COMMON_PARAMETER_DESCRIPTIONS = {
    "query": "Search or filter expression used to narrow the results.",
    "page_size": "Maximum number of items to return in this page of results.",
    "page_token": "Pagination token from a previous response used to fetch the next page.",
    "max_results": "Maximum number of results to return.",
    "fields": "Partial-response field selector controlling which fields are returned.",
    "order_by": "Sort order to apply to the returned results.",
    "format": "Response format to request from the Google API.",
    "metadata_headers": "Header names to include when using a metadata response format.",
    "include_spam_trash": "Whether to include spam and trash items in the result set.",
    "include_items_from_all_drives": "Whether to include items from shared drives as well as My Drive.",
    "supports_all_drives": "Whether the request should support shared drives.",
    "drive_id": "Shared Drive ID to scope the request to.",
    "corpora": "Google Drive corpora scope for the search, such as user or drive.",
    "spaces": "Google Drive space to search, such as drive or appDataFolder.",
    "subject": "Email subject line.",
    "text_body": "Plain-text message body.",
    "html_body": "HTML message body.",
    "attachments": "Attachment payloads to include with the request.",
    "confirm_send": "Whether the user must confirm before the email is sent.",
    "summary_mode": "Whether to omit full message bodies and return a compact summary instead.",
    "calendar_id": "Google Calendar identifier for the target calendar.",
    "event_id": "Google Calendar event identifier.",
    "message_id": "Gmail message identifier.",
    "thread_id": "Gmail thread identifier.",
    "label_id": "Gmail label identifier.",
    "label_ids": "List of Gmail label identifiers.",
    "add_label_ids": "Label identifiers to add.",
    "remove_label_ids": "Label identifiers to remove.",
    "person_fields": "Person fields to request from the People API.",
    "sort_order": "Sort order for the returned results.",
    "sources": "People API sources to include.",
    "include_grid_data": "Whether to include full grid cell data in the spreadsheet response.",
    "ranges": "A1 notation ranges to limit the response to.",
    "range_preset": "Convenience preset for the requested time range.",
    "time_min": "Inclusive RFC3339 start time for the requested window.",
    "time_max": "Exclusive RFC3339 end time for the requested window.",
    "single_events": "Whether recurring events should be expanded into individual instances.",
    "response_status": "Response status to apply, such as accepted or declined.",
    "send_updates": "Guest notification mode to use for this calendar mutation.",
    "idempotency_key": "Stable key used to make retries safe without duplicating work.",
}


def _infer_tool_description(name: str, namespace_hint: str | None = None) -> str:
    namespace, base_name = _split_tool_name(name, namespace_hint=namespace_hint)
    if base_name in _SPECIAL_TOOL_DESCRIPTIONS:
        return _SPECIAL_TOOL_DESCRIPTIONS[base_name]

    namespace_display = _namespace_display(namespace)
    subject = _tool_subject(base_name)
    default_object = _ACTION_OBJECT_FALLBACKS.get(namespace, "resource") if namespace is not None else "resource"

    if base_name.startswith("list_"):
        return f"List {subject} from {namespace_display}."
    if base_name.startswith("get_"):
        return f"Get {subject} from {namespace_display}."
    if base_name.startswith("search_"):
        return f"Search {namespace_display} for {subject}."
    if base_name.startswith("read_"):
        return f"Read {subject} from {namespace_display}."
    if base_name.startswith("find_"):
        return f"Find available {subject} in {namespace_display}."
    if base_name.startswith("create_"):
        return f"Create {subject} in {namespace_display}."
    if base_name.startswith("update_"):
        return f"Update {subject} in {namespace_display}."
    if base_name.startswith("patch_"):
        return f"Apply a partial update to {subject} in {namespace_display}."
    if base_name.startswith("delete_"):
        return f"Delete {subject} from {namespace_display}."
    if base_name.startswith("batch_delete"):
        return f"Delete multiple {default_object}s in {namespace_display}."
    if base_name.startswith("batch_modify"):
        return f"Apply bulk updates to multiple {default_object}s in {namespace_display}."
    if base_name.startswith("add_"):
        return f"Add {subject} in {namespace_display}."
    if base_name.startswith("append_"):
        return f"Append {subject} in {namespace_display}."
    if base_name.startswith("apply_"):
        return f"Apply {subject} in {namespace_display}."
    if base_name.startswith("move_"):
        return f"Move {subject} in {namespace_display}."
    if base_name.startswith("copy_"):
        return f"Copy {subject} in {namespace_display}."
    if base_name.startswith("upload_"):
        return f"Upload {subject} to {namespace_display}."
    if base_name.startswith("download_"):
        return f"Download {subject} from {namespace_display}."
    if base_name.startswith("export_"):
        return f"Export {subject} from {namespace_display}."
    if base_name.startswith("generate_"):
        return f"Generate {subject} with {namespace_display}."
    if base_name.startswith("send_"):
        return f"Send {subject} with {namespace_display}."
    if base_name.startswith("mark_as_"):
        state = base_name.removeprefix("mark_as_").replace("_", " ")
        return f"Mark the {default_object} as {state} in {namespace_display}."
    if base_name.startswith("mark_"):
        return f"Update the status of {subject} in {namespace_display}."
    if base_name.startswith("respond_"):
        return f"Respond to {subject} in {namespace_display}."
    if base_name.startswith("reply_"):
        return f"Reply to {subject} in {namespace_display}."
    if base_name.startswith("remove_"):
        return f"Remove {subject} from {namespace_display}."
    if base_name.startswith("reschedule_"):
        return f"Reschedule {subject} in {namespace_display}."
    if base_name.startswith("share_"):
        return f"Share {subject} from {namespace_display}."
    if base_name.startswith("unshare_"):
        return f"Remove sharing from {subject} in {namespace_display}."
    if base_name.startswith("hide_"):
        return f"Hide {subject} in {namespace_display}."
    if base_name.startswith("unhide_"):
        return f"Unhide {subject} in {namespace_display}."
    if base_name.startswith("trash_"):
        return f"Move the {subject} to trash in {namespace_display}."
    if base_name.startswith("untrash_"):
        return f"Restore the {subject} from trash in {namespace_display}."
    if base_name.startswith("summarize_"):
        return f"Summarize {subject} from {namespace_display}."
    return f"Run the {base_name.replace('_', ' ')} operation in {namespace_display}."


def _infer_param_description(
    tool_name: str,
    parameter_name: str,
    schema: Mapping[str, Any],
    *,
    namespace_hint: str | None = None,
) -> str:
    if parameter_name in _COMMON_PARAMETER_DESCRIPTIONS:
        return _COMMON_PARAMETER_DESCRIPTIONS[parameter_name]
    namespace, base_name = _split_tool_name(tool_name, namespace_hint=namespace_hint)
    if parameter_name.endswith("_id"):
        target = parameter_name[:-3].replace("_", " ")
        return f"Identifier for the target {target}."
    if parameter_name.endswith("_ids"):
        target = parameter_name[:-4].replace("_", " ")
        return f"Identifiers for the target {target}."
    if parameter_name.endswith("_email"):
        target = parameter_name[:-6].replace("_", " ")
        return f"Email address for the {target}."
    if parameter_name.endswith("_emails"):
        target = parameter_name[:-7].replace("_", " ")
        return f"Email addresses for the {target}."
    if parameter_name.endswith("_path"):
        target = parameter_name[:-5].replace("_", " ")
        return f"Local filesystem path for the {target}."
    if parameter_name.endswith("_url"):
        target = parameter_name[:-4].replace("_", " ")
        return f"URL for the {target}."
    if parameter_name.startswith("include_"):
        target = parameter_name[len("include_"):].replace("_", " ")
        return f"Whether to include {target} in the response."
    if parameter_name.startswith("supports_"):
        target = parameter_name[len("supports_"):].replace("_", " ")
        return f"Whether the request should support {target}."
    if parameter_name.startswith("is_"):
        target = parameter_name[len("is_"):].replace("_", " ")
        return f"Whether the {base_name.replace('_', ' ')} request should be treated as {target}."
    if parameter_name.startswith("has_"):
        target = parameter_name[len("has_"):].replace("_", " ")
        return f"Whether to require {target}."
    if parameter_name.startswith("max_"):
        target = parameter_name[len("max_"):].replace("_", " ")
        return f"Maximum {target} to include in the result."
    if parameter_name.startswith("min_"):
        target = parameter_name[len("min_"):].replace("_", " ")
        return f"Minimum {target} value to allow."
    if parameter_name in {"to", "cc", "bcc"}:
        return f"{parameter_name.upper()} recipient addresses."
    if parameter_name in {"start", "end"}:
        return f"RFC3339 {parameter_name} datetime for this operation."
    if parameter_name == "timezone":
        return "IANA timezone name to use for the operation."
    if parameter_name == "mime_type":
        return "MIME type for the file or content payload."
    if parameter_name == "name":
        return f"Display name to use for the target {namespace or 'workspace'} resource."
    if parameter_name == "description":
        return "Human-readable description for the target resource."
    humanized = parameter_name.replace("_", " ")
    schema_type = schema.get("type")
    if schema_type == "boolean":
        return f"Whether to enable {humanized}."
    if schema_type in {"integer", "number"}:
        return f"Numeric value for {humanized}."
    return f"Value for {humanized} used by the {base_name.replace('_', ' ')} operation."


def _enrich_parameters_schema(component: Any, *, namespace_hint: str | None) -> None:
    if not isinstance(component.parameters, dict):
        return
    properties = component.parameters.get("properties")
    if not isinstance(properties, dict):
        return
    try:
        signature = inspect.signature(component.fn)
    except (TypeError, ValueError):
        signature = None

    required = set(component.parameters.get("required", []))
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        if not (schema.get("description") or "").strip():
            schema["description"] = _infer_param_description(
                component.name,
                name,
                schema,
                namespace_hint=namespace_hint,
            )
        if signature is None:
            continue
        parameter = signature.parameters.get(name)
        if parameter is None or name == "ctx":
            continue
        if parameter.default is inspect.Signature.empty:
            required.add(name)

    component.parameters["required"] = sorted(required)

def apply_default_tool_annotations(server: FastMCP) -> None:
    namespace_hint = _server_namespace(server)
    for component_id, component in server._local_provider._components.items():
        if not component_id.startswith("tool:"):
            continue
        tool_component = cast(_ToolComponent, component)
        derived_title = _humanize_tool_title(tool_component.name, namespace_hint=namespace_hint)
        base_title = _humanize_tool_title(_base_tool_name(tool_component.name), namespace_hint=None)
        if tool_component.title in {None, "", base_title}:
            tool_component.title = derived_title
        if not (tool_component.description or "").strip():
            tool_component.description = _infer_tool_description(tool_component.name, namespace_hint=namespace_hint)
        tool_component.tags.update(_tool_tags(tool_component.name, namespace_hint=namespace_hint))
        _enrich_parameters_schema(tool_component, namespace_hint=namespace_hint)
        tool_component.annotations = _merge_annotations(
            tool_component.annotations,
            base_name=_base_tool_name(tool_component.name, namespace_hint=namespace_hint),
        )
