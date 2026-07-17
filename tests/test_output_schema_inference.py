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
