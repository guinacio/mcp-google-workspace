from __future__ import annotations

from typing import Annotated, Any

import anyio

from mcp_google_workspace.common.output_schemas import infer_tool_output_schema
from mcp_google_workspace.server import workspace_mcp


def _properties(fn: Any) -> dict[str, Any]:
    schema = infer_tool_output_schema(fn)
    assert schema is not None
    return schema["properties"]


def test_scalar_parameter_echoed_under_plural_key_is_not_an_array() -> None:
    """Regression class for issue #5: value evidence must beat the plural-name rule."""

    def tool(rows: int, days: int = 7) -> dict[str, Any]:
        return {"skipped_rows": rows, "window_days": days, "events": []}

    properties = _properties(tool)
    assert properties["skipped_rows"]["type"] == "integer"
    assert properties["window_days"]["type"] == "integer"
    assert properties["events"]["type"] == "array"


def test_annotated_and_nullable_parameters_resolve_to_their_types() -> None:
    def tool(
        services: Annotated[str, "a description"],
        groups: str | None = None,
    ) -> dict[str, Any]:
        return {"services": services, "groups": groups}

    properties = _properties(tool)
    assert properties["services"]["type"] == "string"
    assert properties["groups"]["type"] == ["string", "null"]


def test_unit_suffix_names_are_integers_without_value_evidence() -> None:
    def tool(request: Any) -> dict[str, Any]:
        return {
            "retention_days": request.days,
            "timeout_seconds": request.timeout,
            "quota_bytes": request.quota,
        }

    properties = _properties(tool)
    assert properties["retention_days"]["type"] == "integer"
    assert properties["timeout_seconds"]["type"] == "integer"
    assert properties["quota_bytes"]["type"] == "integer"


def test_assigned_literals_and_builtin_calls_type_scalars() -> None:
    def tool() -> dict[str, Any]:
        totals = 0
        labels = sorted({"b", "a"})
        return {"totals": totals, "labels": labels, "size": len(labels)}

    properties = _properties(tool)
    assert properties["totals"]["type"] == "integer"
    assert properties["labels"]["type"] == "array"
    assert properties["size"]["type"] == "integer"


def test_conflicting_assignments_fall_back_to_the_name_heuristic() -> None:
    def tool(flag: bool) -> dict[str, Any]:
        items = 3
        if flag:
            items = []  # type: ignore[assignment]
        return {"items": items}

    properties = _properties(tool)
    assert properties["items"]["type"] == "array"


def test_conflicting_return_paths_union_instead_of_first_wins() -> None:
    """Regression for issue #9: an error path's literal string must not make
    the happy path's None value fail output validation."""

    def tool(ready: bool) -> dict[str, Any]:
        if not ready:
            return {"state": "error", "action": "Reconnect."}
        action = None
        if ready:
            action = "refresh"
        return {"state": "connected", "action": action}

    properties = _properties(tool)
    assert properties["action"]["type"] == ["string", "null"]
    assert properties["state"]["type"] == "string"


def test_type_conflicts_across_paths_union_and_untyped_paths_forfeit() -> None:
    def tool(flag: bool, opaque: Any) -> dict[str, Any]:
        if flag:
            return {"value": 1, "detail": "text", "blob": opaque.load()}
        return {"value": "one", "detail": None, "blob": {"nested": True}}

    properties = _properties(tool)
    assert properties["value"]["type"] == ["integer", "string"]
    assert properties["detail"]["type"] == ["string", "null"]
    # One untyped observation forfeits the claim entirely.
    assert "type" not in properties["blob"]


def test_ternary_values_union_their_branches() -> None:
    def tool(expiry: Any) -> dict[str, Any]:
        return {
            "expires_in_days": 1 if expiry else None,
            "expires_at": expiry.isoformat() if expiry else None,
        }

    properties = _properties(tool)
    assert properties["expires_in_days"]["type"] == ["integer", "null"]
    # An unresolvable branch means no claim, never a bare "null".
    assert "type" not in properties["expires_at"]


def test_connection_status_schema_accepts_the_connected_state() -> None:
    """Live pin for issue #9: nullable happy-path fields must be nullable."""

    async def schema() -> dict[str, Any]:
        tool = await workspace_mcp.get_tool("get_google_connection_status")
        assert tool is not None
        assert tool.output_schema is not None
        return tool.output_schema["properties"]

    properties = anyio.run(schema)
    assert properties["action"]["type"] == ["string", "null"]
    assert properties["checked_capability"]["type"] == ["string", "null"]
    assert "type" not in properties.get("expires_at", {})


def test_get_google_connection_status_validates_when_connected(monkeypatch) -> None:
    """End-to-end repro for issue #9: the most common state must not fail."""
    from fastmcp import Client

    from mcp_google_workspace.auth import google_oauth

    connected = {
        "state": "connected",
        "connected": True,
        "principal_key": "local",
        "granted_scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        "granted_capabilities": ["gmail"],
        "checked_capability": None,
        "required_scopes": [],
        "expires_at": None,
        "action": None,
    }
    monkeypatch.setattr(
        google_oauth,
        "google_connection_status",
        lambda capability=None: dict(connected),
    )

    async def call() -> Any:
        async with Client(workspace_mcp) as client:
            return await client.call_tool("get_google_connection_status", {})

    result = anyio.run(call)
    assert result.is_error is False
    assert result.structured_content["state"] == "connected"
    assert result.structured_content["action"] is None


def test_catalog_has_no_unit_suffix_fields_typed_as_arrays() -> None:
    """No live tool schema may declare a scalar quantity field as an array."""
    suffixes = (
        "_days", "_hours", "_minutes", "_seconds", "_ms",
        "_bytes", "_weeks", "_months", "_years",
    )

    async def catalog() -> list[Any]:
        return await workspace_mcp.list_tools(run_middleware=False)

    offenders = [
        f"{tool.name}.{name}"
        for tool in anyio.run(catalog)
        for name, prop in ((getattr(tool, "output_schema", None) or {}).get("properties") or {}).items()
        if name.lower().endswith(suffixes) and prop.get("type") == "array"
    ]
    assert offenders == []
