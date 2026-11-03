# W3 — Catalog contract diff (explicit application state)

Scope: `tests/contracts/catalog_default.json` and `tests/contracts/catalog_all_optional.json`,
regenerated with `uv run python scripts/snapshot_catalog.py` after W3. Base: the W2 snapshots
(commit `173208d`).

## Counts

No tool, resource, template, or prompt was added, removed, or renamed. Resources,
templates, prompts, and both `discovery_view` lists are byte-identical.

| Configuration | Tools | Resources | Templates | Prompts | `discovery_view` |
| --- | --- | --- | --- | --- | --- |
| Default | 140 (unchanged) | 7 | 2 | 3 | 15 |
| All optional | 192 (unchanged) | 14 | 9 | 7 | 16 |

Changed definitions: 5 `files_*` tools (both configurations) and 11 `apps_*` tools
(all-optional only, because the dashboard is behind `ENABLE_APPS_DASHBOARD`).

## 1. Dashboard: `session_id` replaced by server-issued `view_handle` — intended

The transport-session fallback and caller-chosen `session_id` are removed (owner decision:
no application-level compatibility). Every dashboard view now has a handle minted by the
server (`wsv_` + 43 URL-safe characters, 256 random bits).

| Tool | Input change | Output change |
| --- | --- | --- |
| `apps_get_dashboard` | `session_id` → optional `view_handle` (omit to open a new view) | `+ view` (descriptor) |
| `apps_get_weekly_calendar_view` | `session_id` → optional `view_handle` (omit to open a new view) | `+ view` (descriptor) |
| `apps_get_state` | `session_id`, `timezone` removed; **required** `view_handle` | open object → closed `{state, view}` |
| `apps_set_state` | `session_id` → **required** `view_handle`; `+ expected_revision`; `view` now the `agenda/day/week/month` enum | open object → closed `{state, view}` |
| `apps_patch_state` | `session_id` → **required** `view_handle`; `+ expected_revision` | open object → closed `{state, view}` |
| `apps_next_range` | `session_id` → **required** `view_handle`; `+ expected_revision` | open object → closed `{state, view}` |
| `apps_prev_range` | `session_id` → **required** `view_handle`; `+ expected_revision` | open object → closed `{state, view}` |
| `apps_today` | `session_id` → **required** `view_handle`; `+ expected_revision` | open object → closed `{state, view}` |
| `apps_get_event_detail` | `session_id` → optional `view_handle` (validated when supplied) | unchanged |
| `apps_get_email_detail` | `session_id` → optional `view_handle` (validated when supplied) | unchanged |
| `apps_get_email_attachment` | `session_id` → optional `view_handle` (validated when supplied) | unchanged |

New input properties:

```json
"view_handle": {"type": "string", "maxLength": 256,
  "description": "Server-issued dashboard view handle (wsv_...) returned by apps_get_dashboard or ..."}
"expected_revision": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "default": null,
  "description": "view.revision the caller last observed. When set, the update is applied only if ..."}
```

The handle's exact format is validated in code, not in the schema, so a malformed handle
gets the same clear `view_handle_invalid` tool error as an unknown one instead of a generic
input-validation failure.

`view` descriptor (in `outputSchema.properties.view` and in the result
`_meta["mcp-google-workspace/view"]`):

```json
{"type": "object", "additionalProperties": false,
 "required": ["handle", "revision", "expires_at", "ttl_seconds"],
 "properties": {"handle": {"type": "string"}, "revision": {"type": "integer", "minimum": 1},
                "expires_at": {"type": "integer"}, "ttl_seconds": {"type": "integer", "minimum": 1}}}
```

The six state tools previously published FastMCP's default `{"type": "object",
"additionalProperties": true}` (from a `dict[str, Any]` return). They now publish a closed
`{state, view}` schema whose `state` lists exactly the persisted fields (`view`,
`anchor_date`, `timezone`, `selected_calendars`, `inbox_query`, `include_weekend`); the
old `state.session_id` field no longer exists. Tool descriptions were reworded from
"caller session" to "one dashboard view".

Error results are `isError: true` tool results (not JSON-RPC errors) with
`structuredContent.code`:

- `view_handle_invalid` — malformed, unknown, expired, or another principal's handle
  (`details.reason` is `malformed` or `unknown_or_expired`; a foreign handle is
  indistinguishable from an unknown one). `required_action` opens a new view.
- `view_state_conflict` — `expected_revision` is stale; carries the current `state`,
  `view`, and `details.{expected_revision,current_revision}`. Nothing was written.

## 2. Workspace Files: upload IDs everywhere — intended

Local stdio uploads moved from the connection-scoped, filename-keyed FastMCP default store
to the trusted-local principal, keyed by opaque `upl_` upload IDs like the remote stores.
Only descriptions changed:

| Tool / field | Before | After |
| --- | --- | --- |
| `files_read_file`, `files_delete_file` input `name` | "Uploaded filename in the current user session." | "Opaque upload ID (upl_...) returned by the picker or files_list_files." |
| `files_delete_file` description | "Delete one uploaded file from the current scoped upload store." | "Delete one uploaded file of the current user by its upload ID." |
| `files_delete_file` output `name` | "Uploaded filename requested for deletion." | "Upload ID requested for deletion." |
| `files_list_files`, `files_store_files` output `result` | "Files stored in the current user session." | "Files currently stored for the calling user." |
| same, `remaining_quota_bytes` | "... remote upload quota. Present for remote uploads." | "Bytes still available in the calling user's upload quota." (now always present) |
| `files_list_files_page` item `name` / `upload_id` / `expires_at` / `remaining_quota_bytes` | "Opaque upload handle or local filename." / "Opaque remote upload ID." / "Remote handle expiry epoch." / "Remaining remote upload quota in bytes." | "Opaque upload handle (same value as upload_id)." / "Opaque upload ID (upl_...)." / "Upload handle expiry epoch." / "Remaining upload quota of the current user in bytes." |

## 3. `files_read_file` output wrapping — regression fix

```diff
 "files_read_file".outputSchema:
+  "x-fastmcp-wrap-result": true
```

The tool declared a `{"result": {...}}` envelope but returned a plain dict without asking
FastMCP to wrap it, so every successful read failed client-side output validation
(`'result' is a required property`). No test had called it over the protocol; the new
cross-request tests did. `files_list_files` / `files_store_files` already set the flag.
The published envelope shape is unchanged.

## Not changed

- Tool names, annotations, `_meta.ui` (resource URIs, visibility), the flat
  `ui/resourceUri` alias, and the legacy dashboard URI (W6 owns those).
- Any non-apps, non-files tool.
