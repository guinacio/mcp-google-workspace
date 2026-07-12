from __future__ import annotations

import anyio
from fastmcp import Client
from jsonschema import Draft202012Validator

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.server import workspace_mcp


async def _list_workspace_tools_via_client():
    async with Client(workspace_mcp) as client:
        tools = await client.list_tools()
    return {tool.name: tool for tool in tools}


async def _list_server_tools(server):
    tools = await server.list_tools(run_middleware=False)
    return {tool.name: tool for tool in tools}


def _meta(tool) -> dict | None:
    annotations = tool.annotations
    assert annotations is not None
    return annotations._meta

def test_workspace_tool_catalog_has_strong_metadata() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)

    missing_descriptions: list[str] = []
    weak_titles: list[str] = []
    weak_tags: list[str] = []
    missing_param_descriptions: list[str] = []
    missing_required_arrays: list[str] = []

    for tool in tools.values():
        namespace = tool.name.split("_", 1)[0] if "_" in tool.name else ""
        if not (tool.description or "").strip():
            missing_descriptions.append(tool.name)
        if namespace and not (tool.title or "").lower().startswith(namespace.lower()):
            weak_titles.append(tool.name)
        if namespace and namespace not in set(tool.tags or []):
            weak_tags.append(tool.name)
        params = tool.parameters or {}
        properties = params.get("properties", {}) if isinstance(params, dict) else {}
        if properties and "required" not in params:
            missing_required_arrays.append(tool.name)
        for param_name, schema in properties.items():
            if not isinstance(schema, dict) or not (schema.get("description") or "").strip():
                missing_param_descriptions.append(f"{tool.name}:{param_name}")

    assert missing_descriptions == []
    assert weak_titles == []
    assert weak_tags == []
    assert missing_param_descriptions == []
    assert missing_required_arrays == []


def test_workspace_startup_does_not_fetch_google_credentials(monkeypatch) -> None:
    def fail_get_credentials():
        raise AssertionError("get_credentials should not be called during startup")

    monkeypatch.setattr(google_auth, "get_credentials", fail_get_credentials)
    tools = anyio.run(_list_workspace_tools_via_client)

    assert "gmail_send_email" in tools
    assert "calendar_search_events" in tools
    assert "drive_list_files" in tools
    assert "sheets_get_spreadsheet" in tools
    assert "docs_get_document" in tools


def test_workspace_tools_include_safety_annotations() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)

    assert tools
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["gmail_read_emails"].annotations.readOnlyHint is True
    assert tools["gmail_send_email"].annotations.readOnlyHint is False
    assert tools["gmail_send_email"].annotations.idempotentHint is False
    assert tools["drive_delete_file"].annotations.destructiveHint is True
    assert tools["calendar_get_calendar_context"].annotations.openWorldHint is False
    assert tools["sheets_get_spreadsheet"].annotations.readOnlyHint is True
    assert tools["tasks_delete_task"].annotations.destructiveHint is True
    assert tools["drive_list_files"].title == "Drive List Files"
    assert "drive" in tools["drive_list_files"].tags
    assert "browse" in tools["drive_list_files"].tags
    assert tools["drive_upload_file"].task_config.mode == "optional"
    assert tools["docs_batch_update_document"].task_config.mode == "optional"


def test_workspace_tools_have_closed_documented_output_schemas() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)
    failures: list[str] = []
    for tool in tools.values():
        schema = tool.output_schema
        if not isinstance(schema, dict):
            failures.append(f"{tool.name}:missing")
            continue
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object":
            failures.append(f"{tool.name}:not-object")
        if schema.get("additionalProperties") is not False:
            failures.append(f"{tool.name}:open-object")
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            failures.append(f"{tool.name}:no-properties")
            continue
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict) or not field_schema.get("description"):
                failures.append(f"{tool.name}:{field_name}:no-description")
    assert failures == []


def test_gmail_attachment_inputs_publish_the_complete_closed_schema() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)

    for tool_name in (
        "gmail_send_email",
        "gmail_create_draft",
        "gmail_update_draft",
    ):
        parameters = tools[tool_name].parameters
        Draft202012Validator.check_schema(parameters)
        attachment_schema = parameters["properties"]["attachments"]
        array_schema = next(
            branch for branch in attachment_schema["anyOf"] if branch.get("type") == "array"
        )
        item_schema = array_schema["items"]

        assert item_schema["additionalProperties"] is False
        assert set(item_schema["properties"]) == {
            "file_path",
            "uploaded_file",
            "mime_type",
            "filename",
        }
        assert len(item_schema["oneOf"]) == 2

    send_schema = tools["gmail_send_email"].parameters
    validator = Draft202012Validator(send_schema)
    base_request = {"subject": "Schema check", "to": ["agent@example.com"]}

    assert not list(
        validator.iter_errors({
            **base_request,
            "attachments": [{"uploaded_file": "picker-file.pdf"}],
        })
    )
    assert not list(
        validator.iter_errors({
            **base_request,
            "attachments": [{"file_path": "C:\\files\\local.pdf"}],
        })
    )
    assert list(
        validator.iter_errors({
            **base_request,
            "attachments": [{"base64": "cGRm"}],
        })
    )
    assert list(
        validator.iter_errors({
            **base_request,
            "attachments": [{
                "uploaded_file": "picker-file.pdf",
                "file_path": "C:\\files\\local.pdf",
            }],
        })
    )


def test_workspace_input_schemas_are_recursively_bounded_documented_and_reviewed() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)
    # These are intentional Google-defined polymorphic request maps or the generic
    # encrypted prepare/commit argument envelope. Every other object must be closed.
    reviewed_open_objects = {
        "prepare_workspace_action.arguments",
        "calendar_create_event.conference_data.anyOf[0]",
        "calendar_create_event.reminders.anyOf[0]",
        "calendar_update_event.conference_data.anyOf[0]",
        "calendar_update_event.reminders.anyOf[0]",
        "drive_create_file_metadata.app_properties.anyOf[0]",
        "drive_create_file_metadata.properties.anyOf[0]",
        "drive_update_file_metadata.app_properties.anyOf[0]",
        "drive_update_file_metadata.properties.anyOf[0]",
        "sheets_batch_update_spreadsheet.requests[]",
        "docs_batch_update_document.requests[]",
        "docs_batch_update_document.write_control.anyOf[0]",
        "forms_batch_update_form.requests[]",
        "forms_set_form_publish_settings.publish_settings",
        "slides_batch_update_presentation.requests[]",
    }
    found_open: set[str] = set()
    failures: list[str] = []

    def walk(schema: object, path: str) -> None:
        if not isinstance(schema, dict):
            return
        schema_type = schema.get("type")
        if schema_type == "object" or "additionalProperties" in schema:
            if schema.get("additionalProperties") is not False:
                found_open.add(path)
            if schema.get("additionalProperties") is not False:
                assert schema.get("maxProperties") == 10_000
            for name, child in schema.get("properties", {}).items():
                if not isinstance(child, dict) or not child.get("description"):
                    failures.append(f"{path}.{name}:missing-description")
                walk(child, f"{path}.{name}")
        if schema_type == "array":
            if "maxItems" not in schema:
                failures.append(f"{path}:unbounded-array")
        if schema_type == "string" and "maxLength" not in schema:
            failures.append(f"{path}:unbounded-string")
        if "items" in schema:
            walk(schema["items"], f"{path}[]")
        for keyword in ("anyOf", "oneOf", "allOf"):
            for index, branch in enumerate(schema.get(keyword, [])):
                walk(branch, f"{path}.{keyword}[{index}]")

    for tool in tools.values():
        Draft202012Validator.check_schema(tool.parameters)
        walk(tool.parameters, tool.name)

    assert found_open == reviewed_open_objects
    assert failures == []


def test_apps_tools_preserve_ui_metadata_and_local_hints() -> None:
    tools = anyio.run(_list_server_tools, apps_mcp)

    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["get_dashboard"].annotations.readOnlyHint is True
    assert tools["set_state"].annotations.openWorldHint is False
    assert tools["set_state"].annotations.idempotentHint is True
    assert "respond_to_event" not in tools
    assert _meta(tools["get_dashboard"]) == {"ui": {"resourceUri": "ui://apps/dashboard-ui"}}
