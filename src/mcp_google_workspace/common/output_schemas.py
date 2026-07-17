"""Derive strict JSON output schemas from tool implementation return paths."""

from __future__ import annotations

import ast
from collections.abc import Callable
import inspect
import textwrap
from typing import Any


_OUTPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "prepare_workspace_action": ("status", "commit_token", "expires_at", "impact", "next_action"),
    "commit_workspace_action": ("status", "tool", "result"),
    "get_dashboard": ("title", "generated_at_utc", "state", "sections", "warnings", "section_errors", "weekly_calendar"),
    "get_weekly_calendar_view": ("state", "week_start", "week_end", "timezone", "total_events", "days", "fallback_text"),
    "get_event_detail": (
        "event_id", "calendar_id", "title", "start", "end", "timezone", "status",
        "location", "description", "conference_link", "conference_provider",
        "organizer_email", "organizer_name", "self_response_status", "attendees",
        "attachments",
    ),
    "get_email_detail": (
        "message_id", "thread_id", "subject", "from_value", "to", "cc", "bcc",
        "date", "date_timezone", "source_date", "snippet", "text_body", "html_body",
        "attachments", "labels", "is_unread",
    ),
    "list_calendars": ("kind", "etag", "nextPageToken", "nextSyncToken", "items"),
    "check_time_availability": ("kind", "timeMin", "timeMax", "groups", "calendars"),
    "get_spreadsheet": ("spreadsheetId", "properties", "sheets", "namedRanges", "spreadsheetUrl", "developerMetadata", "dataSources", "dataSourceSchedules"),
    "create_spreadsheet": ("spreadsheetId", "properties", "sheets", "namedRanges", "spreadsheetUrl", "developerMetadata", "dataSources", "dataSourceSchedules"),
    "get_sheet_values": ("range", "majorDimension", "values"),
    "batch_get_sheet_values": ("spreadsheetId", "valueRanges"),
    "append_sheet_values": ("spreadsheetId", "tableRange", "updates"),
    "update_sheet_values": ("spreadsheetId", "updatedRange", "updatedRows", "updatedColumns", "updatedCells", "updatedData"),
    "batch_update_spreadsheet": ("spreadsheetId", "replies", "updatedSpreadsheet"),
    "get_document": ("documentId", "title", "body", "headers", "footers", "footnotes", "documentStyle", "namedStyles", "lists", "namedRanges", "revisionId", "suggestionsViewMode", "tabs"),
    "create_document": ("documentId", "title", "body", "headers", "footers", "footnotes", "documentStyle", "namedStyles", "lists", "namedRanges", "revisionId", "suggestionsViewMode", "tabs"),
    "append_document_text": ("documentId", "replies", "writeControl"),
    "replace_document_text": ("documentId", "replies", "writeControl"),
    "batch_update_document": ("documentId", "replies", "writeControl"),
    "create_tasklist": ("kind", "id", "etag", "title", "updated", "selfLink"),
    "create_task": ("kind", "id", "etag", "title", "updated", "selfLink", "parent", "position", "notes", "status", "due", "completed", "deleted", "hidden", "links", "webViewLink", "assignmentInfo"),
    "update_task": ("kind", "id", "etag", "title", "updated", "selfLink", "parent", "position", "notes", "status", "due", "completed", "deleted", "hidden", "links", "webViewLink", "assignmentInfo"),
    "complete_task": ("kind", "id", "etag", "title", "updated", "selfLink", "parent", "position", "notes", "status", "due", "completed", "deleted", "hidden", "links", "webViewLink", "assignmentInfo"),
    "move_task": ("kind", "id", "etag", "title", "updated", "selfLink", "parent", "position", "notes", "status", "due", "completed", "deleted", "hidden", "links", "webViewLink", "assignmentInfo"),
    "list_contacts": ("connections", "totalPeople", "nextPageToken", "nextSyncToken"),
    "search_contacts": ("results",),
    "get_contact": ("resourceName", "etag", "metadata", "names", "emailAddresses", "phoneNumbers", "organizations", "addresses", "biographies", "birthdays", "photos", "urls", "relations", "memberships"),
    "create_contact": ("resourceName", "etag", "metadata", "names", "emailAddresses", "phoneNumbers", "organizations", "addresses", "biographies", "birthdays", "photos", "urls", "relations", "memberships"),
    "update_contact": ("resourceName", "etag", "metadata", "names", "emailAddresses", "phoneNumbers", "organizations", "addresses", "biographies", "birthdays", "photos", "urls", "relations", "memberships"),
    "list_contact_groups": ("contactGroups", "nextPageToken", "totalItems"),
    "create_contact_group": ("resourceName", "etag", "metadata", "groupType", "name", "formattedName", "memberResourceNames", "memberCount", "clientData"),
    "modify_contact_group_members": ("notFoundResourceNames", "canNotRemoveLastContactGroupResourceNames"),
    "get_form": ("formId", "info", "settings", "items", "responderUri", "revisionId", "linkedSheetId"),
    "create_form": ("formId", "info", "settings", "items", "responderUri", "revisionId", "linkedSheetId"),
    "batch_update_form": ("form", "replies", "writeControl"),
    "set_form_publish_settings": ("formId", "publishSettings"),
    "get_presentation": ("presentationId", "pageSize", "slides", "title", "masters", "layouts", "locale", "revisionId", "notesMaster"),
    "create_presentation": ("presentationId", "pageSize", "slides", "title", "masters", "layouts", "locale", "revisionId", "notesMaster"),
    "replace_text_in_presentation": ("presentationId", "replies", "writeControl"),
    "get_slide_page": ("objectId", "pageType", "pageElements", "pageProperties", "slideProperties", "layoutProperties", "notesProperties", "masterProperties", "revisionId"),
    "get_slide_thumbnail": ("contentUrl", "width", "height"),
    "batch_update_presentation": ("presentationId", "replies", "writeControl"),
}


def _registered_schema(tool_name: str) -> dict[str, Any] | None:
    fields = _OUTPUT_FIELDS.get(tool_name)
    if fields is None:
        return None
    return {
        "type": "object",
        "title": f"{tool_name.replace('_', ' ').title()} response",
        "description": "Documented structured response returned by this MCP tool.",
        "properties": {
            name: _named_field_schema(name) for name in fields
        },
        "required": [],
        "additionalProperties": False,
    }


def _field_description(name: str) -> str:
    special = {
        "status": "Machine-readable operation status.",
        "count": "Number of items in this response.",
        "next_page_token": "Opaque token for retrieving the next page, or null when complete.",  # nosec B105 -- schema documentation, not a credential
        "has_more": "Whether another page of results is available.",
        "fetched_at": "RFC3339 timestamp when the response was fetched.",
    }
    return special.get(name, f"Response field: {name.replace('_', ' ')}.")


def _named_field_schema(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = {"description": _field_description(name)}
    lowered = name.lower()
    if (
        lowered in {"count", "total", "totalitems", "totalpeople", "updatedrows", "updatedcolumns", "updatedcells", "width", "height", "bytes_written"}
        or lowered.startswith("total_")
        or lowered.endswith("_count")
        # Unit suffixes are scalar quantities even though they end in "s";
        # without this, the plural rule below declared them arrays (issue #5).
        or lowered.endswith(("_days", "_hours", "_minutes", "_seconds", "_ms", "_bytes", "_weeks", "_months", "_years"))
    ):
        schema["type"] = "integer"
    elif lowered.startswith(("has_", "is_")) or lowered in {"connected", "retryable", "truncated", "success"}:
        schema["type"] = "boolean"
    elif lowered.endswith(("token", "id", "uri", "url", "time", "date", "status")) or lowered in {"kind", "etag", "title", "message", "mode", "action", "range", "majordimension"}:
        schema["type"] = ["string", "null"]
    elif lowered in {
        "calendars", "errors", "groups", "headers", "footers", "footnotes", "lists", "namedranges", "section_errors",
        "state", "properties", "settings", "metadata", "body", "form", "file", "event", "thread", "updates", "writecontrol", "publishsettings", "optional_namespaces", "file_inputs", "impact", "next_action", "result",
    }:
        schema["type"] = "object"
    elif lowered in {
        "items", "files", "responses", "results", "replies", "connections", "sheets", "slides", "masters", "layouts", "values", "valueranges", "contactgroups", "names", "emailaddresses", "phonenumbers", "organizations", "addresses", "biographies", "birthdays", "photos", "urls", "relations", "memberships", "links", "days", "sections", "warnings", "enabled_namespaces", "oauth_capabilities",
    } or lowered.endswith("s") and lowered not in {"status", "properties", "settings"}:
        schema.update({"type": "array", "items": {}})
    return schema


_Resolver = Callable[[ast.expr], dict[str, Any]]

_BUILTIN_CALL_SCHEMAS: dict[str, dict[str, Any]] = {
    "len": {"type": "integer"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "str": {"type": "string"},
    "list": {"type": "array", "items": {}},
    "sorted": {"type": "array", "items": {}},
    "dict": {"type": "object"},
}


def _annotation_schema(node: ast.expr | None) -> dict[str, Any]:
    """Map a simple parameter annotation AST to a JSON schema type."""
    if node is None:
        return {}
    if isinstance(node, ast.Name):
        return dict(
            {
                "int": {"type": "integer"},
                "float": {"type": "number"},
                "bool": {"type": "boolean"},
                "str": {"type": "string"},
            }.get(node.id, {})
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # X | None keeps X's type but allows null.
        if isinstance(node.right, ast.Constant) and node.right.value is None:
            inner = _annotation_schema(node.left)
            inner_type = inner.get("type")
            if isinstance(inner_type, str):
                return {**inner, "type": [inner_type, "null"]}
        return {}
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        if node.value.id == "list":
            return {"type": "array", "items": {}}
        if node.value.id == "dict":
            return {"type": "object"}
        if node.value.id == "Annotated":
            if isinstance(node.slice, ast.Tuple) and node.slice.elts:
                return _annotation_schema(node.slice.elts[0])
    return {}


def _literal_schema(node: ast.expr, resolver: _Resolver | None = None) -> dict[str, Any]:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if isinstance(value, str):
            return {"type": "string"}
    if isinstance(node, (ast.List, ast.ListComp)):
        return {"type": "array", "items": {}}
    if isinstance(node, ast.Dict):
        shape = _dict_shape(node, resolver)
        return _schema_for_shapes([shape]) if shape else {"type": "object"}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        builtin = _BUILTIN_CALL_SCHEMAS.get(node.func.id)
        if builtin is not None:
            return dict(builtin)
    if isinstance(node, ast.IfExp):
        body = _literal_schema(node.body, resolver)
        orelse = _literal_schema(node.orelse, resolver)
        if body and orelse:
            return _merge_property_schemas(body, orelse)
        return {}
    if resolver is not None:
        return resolver(node)
    return {}


def _dict_shape(node: ast.Dict, resolver: _Resolver | None = None) -> dict[str, dict[str, Any]]:
    shape: dict[str, dict[str, Any]] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            # Evidence from the value expression always beats the name
            # heuristic: a scalar must never be declared an array because
            # its key happens to end in "s" (issue #5).
            field_schema = _literal_schema(value, resolver)
            if not field_schema:
                field_schema = _named_field_schema(key.value)
            field_schema.setdefault("description", _field_description(key.value))
            shape[key.value] = field_schema
    return shape


def _merge_property_schemas(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Union two observations of the same field across return paths.

    Different return statements may type the same key differently (a literal
    string on an error path, a nullable variable on the happy path). The
    merged schema must accept every observed shape: conflicting types union,
    and an untyped observation forfeits the type claim entirely (issue #9).
    """
    first_body = {key: value for key, value in first.items() if key != "description"}
    second_body = {key: value for key, value in second.items() if key != "description"}
    if first_body == second_body:
        return dict(first)
    merged: dict[str, Any] = {}
    first_type, second_type = first_body.get("type"), second_body.get("type")
    if first_type is not None and second_type is not None:
        kinds: list[str] = []
        for declared in (first_type, second_type):
            for kind in [declared] if isinstance(declared, str) else list(declared):
                if kind not in kinds:
                    kinds.append(kind)
        concrete = sorted(kind for kind in kinds if kind != "null")
        ordered = concrete + (["null"] if "null" in kinds else [])
        merged["type"] = ordered[0] if len(ordered) == 1 else ordered
        if "array" in ordered:
            merged["items"] = {}
    description = first.get("description") or second.get("description")
    if description:
        merged["description"] = description
    return merged


def _schema_for_shapes(shapes: list[dict[str, dict[str, Any]]]) -> dict[str, Any]:
    nonempty = [shape for shape in shapes if shape]
    if not nonempty:
        return {}
    properties: dict[str, dict[str, Any]] = {}
    for shape in nonempty:
        for name, schema in shape.items():
            existing = properties.get(name)
            properties[name] = (
                dict(schema)
                if existing is None
                else _merge_property_schemas(existing, schema)
            )
    return {
        "type": "object",
        "properties": properties,
        "required": [],
        "additionalProperties": False,
    }


class _FunctionReturns(ast.NodeVisitor):
    def __init__(self, root: ast.AsyncFunctionDef | ast.FunctionDef) -> None:
        self.root = root
        self.returns: list[ast.expr] = []
        self.assignments: dict[str, list[ast.expr]] = {}
        self.subscript_fields: dict[str, dict[str, dict[str, Any]]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.returns.append(node.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.assignments.setdefault(target.id, []).append(node.value)
            elif (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                self.subscript_fields.setdefault(target.value.id, {})[target.slice.value] = {
                    **_literal_schema(node.value),
                    "description": _field_description(target.slice.value),
                }
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            self.assignments.setdefault(node.target.id, []).append(node.value)
            self.generic_visit(node.value)


def _unwrap_call(node: ast.expr) -> ast.Call | None:
    while isinstance(node, (ast.Await, ast.Starred)):
        node = node.value
    return node if isinstance(node, ast.Call) else None


def _call_target(call: ast.Call, fn: Callable[..., Any]) -> Callable[..., Any] | None:
    candidate: ast.expr | None = call.func
    if isinstance(candidate, ast.Name) and candidate.id == "run_blocking" and call.args:
        candidate = call.args[0]
    if isinstance(candidate, ast.Name):
        target = fn.__globals__.get(candidate.id)
        return target if callable(target) else None
    return None


def infer_tool_output_schema(
    fn: Callable[..., Any],
    *,
    tool_name: str | None = None,
    _seen: set[int] | None = None,
) -> dict[str, Any] | None:
    """Infer a closed object schema from literal dict returns and helper calls."""
    if tool_name is not None and (registered := _registered_schema(tool_name)) is not None:
        return registered
    seen = _seen or set()
    if id(fn) in seen:
        return None
    seen.add(id(fn))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, IndentationError, SyntaxError):
        return None
    root = next(
        (node for node in tree.body if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))),
        None,
    )
    if root is None:
        return None
    visitor = _FunctionReturns(root)
    visitor.visit(root)
    shapes: list[dict[str, dict[str, Any]]] = []
    parameter_schemas = {
        arg.arg: schema
        for arg in [*root.args.posonlyargs, *root.args.args, *root.args.kwonlyargs]
        if (schema := _annotation_schema(arg.annotation))
    }

    def resolve(node: ast.expr) -> dict[str, Any]:
        """Type a value expression from parameter annotations or assignments."""
        if not isinstance(node, ast.Name):
            return {}
        annotated = parameter_schemas.get(node.id)
        if annotated:
            return dict(annotated)
        assigned = [
            _literal_schema(value) for value in visitor.assignments.get(node.id, [])
        ]
        if not assigned or not all(assigned):
            # Any unresolvable assignment forfeits the claim: partial evidence
            # must not narrow the schema (a None initializer plus an opaque
            # reassignment would otherwise read as "always null").
            return {}
        kinds = {schema.get("type") for schema in assigned}
        if not all(isinstance(kind, str) for kind in kinds):
            return {}
        if kinds == {"null"}:
            # A lone None initializer proves nothing about the real value.
            return {}
        if len(kinds) == 1:
            return dict(assigned[0])
        if len(kinds) == 2 and "null" in kinds:
            concrete = next(kind for kind in kinds if kind != "null")
            if concrete == "array":
                return {}
            return {"type": [concrete, "null"]}
        return {}

    def collect(node: ast.expr) -> None:
        if isinstance(node, ast.Dict):
            shapes.append(_dict_shape(node, resolve))
            return
        if isinstance(node, ast.Name):
            for assigned in visitor.assignments.get(node.id, []):
                collect(assigned)
            if node.id in visitor.subscript_fields:
                shapes.append(visitor.subscript_fields[node.id])
            return
        call = _unwrap_call(node)
        if call is None:
            return
        target = _call_target(call, fn)
        if target is None:
            return
        nested = infer_tool_output_schema(target, _seen=seen)
        if nested and isinstance(nested.get("properties"), dict):
            shapes.append(nested["properties"])

    for returned in visitor.returns:
        collect(returned)
    schema = _schema_for_shapes(shapes)
    if not schema:
        return None
    schema["title"] = f"{fn.__name__.replace('_', ' ').title()} response"
    schema["description"] = "Structured response returned by this MCP tool."
    return schema
