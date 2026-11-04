"""Shared tool annotation defaults for FastMCP components."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from functools import wraps
import inspect
from typing import Any, Protocol, cast

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .fastmcp_compat import local_tools
from .confirmation import install_confirmation_guard
from .execution import install_execution_guard
from .output_schemas import RAW_PASSTHROUGH_TOOLS, infer_tool_output_schema
from .parameter_descriptions import shared_parameter_description


class _ToolComponent(Protocol):
    name: str
    title: str | None
    description: str | None
    tags: set[str]
    parameters: dict[str, Any] | None
    output_schema: dict[str, Any] | None
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
    "get_calendar_context",
    "get_state",
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
    "download_attachment",
    "download_event_attachment",
    "download_file",
    "export_google_file",
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
    "end_active_conference",
}

_IDEMPOTENT_TOOLS = {
    "apply_labels",
    "archive_note",
    "complete_task",
    "hide_drive",
    "mark_as_not_spam",
    "mark_as_read",
    "mark_as_spam",
    "mark_as_unread",
    "patch_note_checklist",
    "patch_state",
    "replace_document_text",
    "replace_text_in_presentation",
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

_READ_ONLY_TOOLS = {
    "get_mail_digest",
    "tasks_digest",
    "get_calendar_digest",
}

_EXTRA_TOOL_TAGS = {
    # Drive file listing is the connector's primary browsing surface.
    "drive_list_files": {"browse"},
}

_TAG_STOPWORDS = {"a", "an", "as", "by", "for", "from", "in", "of", "the", "to"}

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
    canonical_name = f"{namespace}_{base_name}" if namespace is not None else name
    tags.update(_EXTRA_TOOL_TAGS.get(canonical_name, set()))
    return tags


def _is_read_only(base_name: str) -> bool:
    if base_name in _READ_ONLY_TOOLS:
        return True
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

    derived: dict[str, bool] = {
        "read_only_hint": read_only,
        "destructive_hint": destructive,
        "idempotent_hint": idempotent,
        "open_world_hint": open_world,
    }
    if current is None:
        return ToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        )
    # Explicit hints (and the title) win; only unset hints are derived.
    return current.model_copy(
        update={
            field: value
            for field, value in derived.items()
            if getattr(current, field) is None
        }
    )




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
            shared = shared_parameter_description(name)
            if shared is not None:
                schema["description"] = shared
        if signature is None:
            continue
        parameter = signature.parameters.get(name)
        if parameter is None or name == "ctx":
            continue
        if parameter.default is inspect.Signature.empty:
            required.add(name)

    component.parameters["required"] = sorted(required)
    simplify_optional_properties(component.parameters)


def _non_null_branches(schema: Mapping[str, Any]) -> list[Any] | None:
    branches = schema.get("anyOf")
    if not isinstance(branches, list):
        return None
    nulls = [branch for branch in branches if branch == {"type": "null"}]
    if len(nulls) != 1:
        return None
    return [branch for branch in branches if branch != {"type": "null"}]


def simplify_optional_properties(schema: Any) -> None:
    """Publish optional parameters as their plain type instead of ``T | null``.

    A parameter that is absent from ``required`` and defaults to null is
    optional already; the generated ``anyOf: [T, {type: null}]`` plus
    ``default: null`` restates that at the cost of a schema level per
    parameter. Omitting the parameter keeps meaning "not set". The server
    still validates arguments against the Python signature, so an explicit
    null from a client that sends one is accepted as before.
    """
    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = set(schema.get("required", []))
        for name, child in properties.items():
            if not isinstance(child, dict) or name in required:
                continue
            if "default" in child and child["default"] is not None:
                continue
            remaining = _non_null_branches(child)
            if remaining is None:
                continue
            del child["anyOf"]
            child.pop("default", None)
            if len(remaining) == 1 and isinstance(remaining[0], dict):
                for key, value in remaining[0].items():
                    child.setdefault(key, value)
            else:
                child["anyOf"] = remaining
        for child in properties.values():
            simplify_optional_properties(child)
    if "items" in schema:
        simplify_optional_properties(schema["items"])
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, []):
            simplify_optional_properties(branch)
    for definition in schema.get("$defs", {}).values():
        simplify_optional_properties(definition)


def _paginated_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    token = result.get("next_page_token", result.get("nextPageToken"))
    result.setdefault("next_page_token", token)
    result.setdefault("has_more", bool(token))
    result.setdefault(
        "fetched_at", datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    if "count" not in result:
        for value in result.values():
            if isinstance(value, list):
                result["count"] = len(value)
                break
        else:
            result["count"] = 0
    return result


def _wrap_pagination(component: Any, base_name: str) -> None:
    if not base_name.startswith(("list_", "search_")):
        return
    original = component.fn
    if getattr(original, "_workspace_pagination_wrapped", False):
        return
    if inspect.iscoroutinefunction(original):
        @wraps(original)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return _paginated_result(await original(*args, **kwargs))

        wrapped = async_wrapper
    else:
        @wraps(original)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return _paginated_result(original(*args, **kwargs))

        wrapped = sync_wrapper
    setattr(wrapped, "_workspace_pagination_wrapped", True)
    component.fn = wrapped

def apply_default_tool_annotations(server: FastMCP) -> None:
    namespace_hint = _server_namespace(server)
    for component in local_tools(server):
        tool_component = cast(_ToolComponent, component)
        derived_title = _humanize_tool_title(tool_component.name, namespace_hint=namespace_hint)
        base_title = _humanize_tool_title(_base_tool_name(tool_component.name), namespace_hint=None)
        if tool_component.title in {None, "", base_title}:
            tool_component.title = derived_title
        tool_component.tags.update(_tool_tags(tool_component.name, namespace_hint=namespace_hint))
        _enrich_parameters_schema(tool_component, namespace_hint=namespace_hint)
        namespace, base_name = _split_tool_name(
            tool_component.name, namespace_hint=namespace_hint
        )
        canonical_name = f"{namespace}_{base_name}" if namespace is not None else base_name
        inferred_output_schema = (
            None
            if canonical_name in RAW_PASSTHROUGH_TOOLS
            else infer_tool_output_schema(tool_component.fn, tool_name=base_name)
        )
        if canonical_name in RAW_PASSTHROUGH_TOOLS:
            tool_component.output_schema = None
        if inferred_output_schema is not None:
            if base_name.startswith(("list_", "search_")):
                inferred_output_schema["properties"].update(
                    {
                        "count": {
                            "type": "integer",
                            "description": "Number of items in this response.",
                        },
                        "next_page_token": {
                            "type": ["string", "null"],
                            "description": "Opaque token for the next page, or null.",
                        },
                        "has_more": {
                            "type": "boolean",
                            "description": "Whether another page is available.",
                        },
                        "fetched_at": {
                            "type": "string",
                            "format": "date-time",
                            "description": "RFC3339 time when this response was fetched.",
                        },
                    }
                )
            tool_component.output_schema = inferred_output_schema
        _wrap_pagination(
            tool_component,
            _base_tool_name(tool_component.name, namespace_hint=namespace_hint),
        )
        # Outermost wrapper: binds tool identity + validated arguments for the
        # confirmation adapter and returns its multi-round-trip ask as this
        # tool's InputRequiredResult (common/confirmation.py). Installed after
        # output-schema inference, which reads the undecorated source.
        install_confirmation_guard(tool_component, tool_component.name)
        # Outermost of all: error envelopes on every path, plus execution-time
        # authorization/admission/deadline for background-task workers, which
        # run the tool function without FastMCP middleware (common/execution.py).
        install_execution_guard(tool_component, namespace=namespace_hint)
        tool_component.annotations = _merge_annotations(
            tool_component.annotations,
            base_name=_base_tool_name(tool_component.name, namespace_hint=namespace_hint),
        )
