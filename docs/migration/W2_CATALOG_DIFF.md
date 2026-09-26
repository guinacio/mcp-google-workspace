# W2 — Catalog contract diff (FastMCP 3.4.4 → 4.0.10)

Scope: `tests/contracts/catalog_default.json` and `tests/contracts/catalog_all_optional.json`,
regenerated with `uv run python scripts/snapshot_catalog.py` after the W2 changes. The
W0 snapshots were listed by a FastMCP 3 in-memory client (MCP 2025-11-25); the W2
snapshots are listed by a FastMCP 4.0.10 in-memory client, which negotiates MCP
2026-07-28. Snapshot keys stay in wire (camelCase) form; the helper now reads SDK 2
snake_case attributes (`tool.input_schema`, `resource.mime_type`, …) and dumps models
`by_alias`.

## Counts

| Configuration | Tools | Resources | Templates | Prompts | `discovery_view` |
| --- | --- | --- | --- | --- | --- |
| Default, W0 | 139 | 7 | 2 | 3 | 15 |
| **Default, W2** | **140** | 7 | 2 | 3 | 15 (unchanged) |
| All optional, W0 | 193 | 14 | 9 | 7 | 16 |
| **All optional, W2** | **192** | 14 | 9 | 7 | 16 (unchanged) |

Default: +1 (`files_store_files`). All optional: +1 (`files_store_files`) −2 (the two
summary tools) = 192. No resource, template, or prompt was added, removed or renamed.

## Diff categories

Only 5 of 139 (default) / 5 of 191 (all-optional, common entries) tool definitions changed,
and every prompt changed only in argument descriptions. Resources and templates are
byte-identical.

### 1. App-only callback now listed — intended framework/spec change

`files_store_files` (the picker's upload callback) appears in `tools/list`. FastMCP
3 omitted app-only tools; FastMCP 4.0.10 lists them because the MCP Apps spec puts
visibility filtering on the host, and a tool absent from `tools/list` cannot be routed
by name through intermediaries (`fastmcp/server/server.py`, `list_tools`). It remains
app-only and excluded from BM25 search results (`discovery_view` unchanged):

```json
{"name": "files_store_files", "meta": {"ui": {"visibility": ["app"]},
 "fastmcp": {"app": "Workspace Files", "tool_hash": "4a1eab56c80c", ...}}, ...}
```

Because its input schema is now published, W2 replaced the generated open
`files: list[dict]` schema with a closed, documented, bounded schema for the exact
Prefab `DropZone` payload (`name` ≤1024, `size` 0..25 MiB, `type` ≤255, `data` base64 ≤
34,952,536 chars, `additionalProperties: false`). Hosts that ignore `visibility` would
expose this tool to the model; server-side enforcement of app-only calls is W6.

### 2. Summary tools removed — intended (owner decision, 2026-09-26)

`chat_summarize_space_messages` and `keep_summarize_note` are deleted. They depended on
`ctx.sample()`, which FastMCP 4 removed (MCP Sampling is deprecated in 2026-07-28), and
no server-side summarization provider will be added. The Chat/Keep read tools and the
user-invoked summary *prompts* (`chat_summarize_chat_thread_prompt`,
`keep_summarize_keep_note_prompt`) are unchanged.

### 3. `_meta.fastmcp.tool_hash` on Workspace Files tools — intended framework change

Every tool contributed by the `FileUpload` app provider (`files_delete_file`,
`files_file_manager`, `files_list_files`, `files_list_files_page`, `files_read_file`)
now carries its stable backend address in `_meta`:

```diff
   "meta": {"fastmcp": {"app": "Workspace Files", "tags": [...],
+                       "tool_hash": "2abf46aeadcd"},
```

It is a SHA-256 of static strings (deterministic across machines).

### 4. Picker output schema accepts the FastMCP 4 Prefab envelope — intended (W2 fix)

FastMCP 4.0.10 adds late-bound tool names under `_meta.fastmcp.toolNames` to the
Prefab payload, which the closed W0 schema rejected (the stdio bundle picker test
failed with `'_meta' was unexpected`). Only that exact shape was added; any other
top-level or `_meta` key still fails validation (`tests/test_file_uploads.py`):

```diff
 "files_file_manager".outputSchema.properties:
+  "_meta": {"type": "object", "additionalProperties": false, "properties": {
+    "fastmcp": {"type": "object", "additionalProperties": false, "properties": {
+      "toolNames": {"type": "object", "additionalProperties": {"type": "string"}}}}}}
```

### 5. Prompt argument descriptions reworded — intended framework change

FastMCP 4 regenerates the hint it attaches to string prompt arguments (10 arguments
across all 7 prompts):

```diff
- "Provide as a JSON string matching the following schema: {\"type\":\"string\"}"
+ "Provide a value matching the following JSON schema: {\"type\":\"string\"}. Encode non-string values as JSON."
```

### 6. No change where one might be expected

- **Annotations**: identical on the wire although Python now builds them with
  snake_case fields (`read_only_hint`, …).
- **Task metadata**: MCP 2026-07-28 drops `Tool.execution` from the wire (task support
  is negotiated through the `io.modelcontextprotocol/tasks` extension), so the snapshot
  does not record it. `tests/test_task_extension.py` instead pins the 9 (default) / 13
  (Gemini) task-capable tools server-side, the extension advertisement on 2026-07-28,
  and `execution.taskSupport: "optional"` on legacy (2025-11-25) listings.
- **Tool descriptions**: replacing client-facing `ctx.info/warning` logging with server
  logging and the shared confirmation gate changed no tool definition.
- **Flat `ui/resourceUri` alias and legacy dashboard URI**: retained for W6.

## Regressions found and fixed rather than regenerated

None of the snapshot diffs was a regression. The catalog comparison did surface one
contract gap that W2 fixed in code (category 4) and one newly published schema that
W2 tightened (category 1).
