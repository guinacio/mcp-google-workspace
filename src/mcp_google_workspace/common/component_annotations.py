"""Shared tool annotation defaults for FastMCP components."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from functools import wraps
import inspect
import re
from typing import Any, Protocol, cast

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .fastmcp_compat import local_tools
from .confirmation import install_confirmation_guard
from .execution import install_execution_guard
from .output_schemas import infer_tool_output_schema
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
    "get_state",
    "next_range",
    "patch_state",
    "prev_range",
    "set_state",
    "today",
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


def consent_hints(published_name: str) -> dict[str, bool]:
    """``readOnlyHint``, ``destructiveHint`` and ``idempotentHint`` from the consent table.

    Read-only: the tool never changes anything. Destructive: some call of the
    tool is consequential and needs consent (sends, shares, permanent
    deletion, forwarding, bulk changes). Idempotent: as the table states it.
    """
    from .consent import CONSENT_TABLE

    policy = CONSENT_TABLE.get(published_name)
    if policy is None:
        raise RuntimeError(f"{published_name} is not classified in the consent table (common/consent.py).")
    read_only = policy.base == "read" and not policy.triggers
    destructive = policy.may_be_consequential if policy.destructive is None else policy.destructive
    return {
        "read_only_hint": read_only,
        "destructive_hint": False if read_only else destructive,
        "idempotent_hint": True if read_only else policy.idempotent,
    }


def _open_world(base_name: str) -> bool:
    return base_name not in _LOCAL_ONLY_TOOLS


def _merge_annotations(
    existing: ToolAnnotations | Mapping[str, Any] | None, *, base_name: str, published_name: str
) -> ToolAnnotations:
    if isinstance(existing, ToolAnnotations):
        current = existing
    elif isinstance(existing, Mapping):
        current = ToolAnnotations.model_validate(existing)
    else:
        current = ToolAnnotations()
    # The consent table is authoritative for the three safety hints; only the
    # title and openWorldHint may be set by the tool itself.
    update: dict[str, Any] = dict(consent_hints(published_name))
    if current.open_world_hint is None:
        update["open_world_hint"] = _open_world(base_name)
    return current.model_copy(update=update)


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


def flow_description(text: str | None) -> str | None:
    """Publish a docstring's paragraphs as flowing text.

    Docstrings are wrapped for the source file; a client renders the
    description itself, so line breaks inside a paragraph only cost tokens and
    break sentences mid-way. Blank-line paragraph breaks and list items stay.
    """
    if not text:
        return text
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        flowed: list[str] = []
        for line in lines:
            if flowed and not line.startswith(("- ", "* ")):
                flowed[-1] = f"{flowed[-1]} {line}"
            else:
                flowed.append(line)
        paragraphs.append("\n".join(flowed))
    return "\n\n".join(paragraphs)


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


# Tools that return a list of resources without a list_/search_ name; they get
# the same list envelope (count, has_more, next_page_token, fetched_at).
_LIST_ENVELOPE_TOOLS = frozenset(
    {
        "check_mail_updates",
        "get_calendar_digest",
        "get_mail_digest",
        "get_thread",
        "digest",  # tasks_tasks_digest: its local name starts with its namespace
    }
)


def _returns_list_envelope(base_name: str) -> bool:
    return base_name.startswith(("list_", "search_")) or base_name in _LIST_ENVELOPE_TOOLS


def _paginated_result(result: Any) -> Any:
    """Complete the list envelope every list-shaped result shares.

    The tool supplies its resource-named list (``files``, ``messages``,
    ``events``, ...), ``count`` and ``next_page_token`` when it has them; the
    wrapper fills ``count``, ``next_page_token``, ``has_more`` and
    ``fetched_at`` where the tool did not.
    """
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
    if not _returns_list_envelope(base_name):
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
        tool_component.description = flow_description(tool_component.description)
        tool_component.tags.update(_tool_tags(tool_component.name, namespace_hint=namespace_hint))
        _enrich_parameters_schema(tool_component, namespace_hint=namespace_hint)
        _, base_name = _split_tool_name(tool_component.name, namespace_hint=namespace_hint)
        inferred_output_schema = infer_tool_output_schema(tool_component.fn, tool_name=base_name)
        if inferred_output_schema is not None:
            if _returns_list_envelope(base_name):
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
        # Mounted namespace servers register local names (tasks_digest is
        # published as tasks_tasks_digest); the root registers published ones.
        published_name = f"{namespace_hint}_{tool_component.name}" if namespace_hint else tool_component.name
        install_confirmation_guard(tool_component, tool_component.name, published_name=published_name)
        # Outermost of all: error envelopes on every path, plus execution-time
        # authorization/admission/deadline for background-task workers, which
        # run the tool function without FastMCP middleware (common/execution.py).
        install_execution_guard(tool_component, namespace=namespace_hint)
        tool_component.annotations = _merge_annotations(
            tool_component.annotations,
            base_name=_base_tool_name(tool_component.name, namespace_hint=namespace_hint),
            published_name=published_name,
        )
