# W5 — Catalog contract diff

Regenerated with `uv run python scripts/snapshot_catalog.py` after merging W4b
(`568c75d`); the base is the post-W4b snapshots. Tool, resource, template and
prompt counts are unchanged (**140 default / 192 all-optional tools**); resources,
templates, prompts and both `discovery_view` lists are byte-identical. No input
schema, annotation, title or `_meta` changed.

| Configuration | Changed tool definitions |
| --- | --- |
| Default | 49 (48 output schemas + `refresh_workspace_catalog`) |
| All optional | 71 (66 output schemas, 4 Meet schemas, `refresh_workspace_catalog`) |

Snapshot entries are sorted by the snapshot tool; the live `tools/list` is now
also sorted by name (the snapshot cannot show that, see
`tests/test_http_live.py::test_modern_requests_create_no_session` and the
replica test).

## 1. Output schemas describe successful results only — intended

W5 reclassifies failures (plan section 3.5): a Google API failure, argument
validation failure or business-rule rejection is an `isError: true` tool result
whose `structuredContent` is the shared error envelope (`code`, `message`,
`retryable`, `retry_after`, `required_action`, `provider_status`,
`field_errors`, optional `details`). Clients do not validate an `isError`
result against `outputSchema`. The 61 tool bodies that used to *return*
`{"error": ..., "provider_status": ..., "context": ...}` as a successful result
now raise `provider_tool_error(...)`, so the three envelope properties that
every registered schema carried for that convention are removed:

```diff
 "calendar_list_calendars".outputSchema.properties:
-  "context": {"description": "Identifying request arguments echoed back with an error.", "type": "object"},
-  "error": {"description": "Error message or structured error details when the call failed.", "type": ["object", "string"]},
-  "provider_status": {"description": "HTTP status code returned by the Google API for a failed call.", "type": "integer"},
```

Affected (default): Calendar 2, Docs 5, Forms 6, People 9, Sheets 7, Slides 6,
Google Tasks 11, `prepare_workspace_action`, `commit_workspace_action`.
Additionally in all-optional: Keep 8, Meet 5 (plus the four in section 2),
Apps 5 (`get_dashboard`, `get_weekly_calendar_view`, `get_event_detail`,
`get_email_detail`, and `get_email_attachment`, which only loses `error`). The
dashboard detail tools also dropped `error` from their documented fields; their
failures (`AppError` dictionaries before) are now `isError` results with code
`provider_error` and `details.context`.

The error context moved from top-level `context` to `details.context` in the
envelope.

## 2. Meet space tools: latent bug fixed by the same change

`meet_create_space`, `meet_get_space`, `meet_update_space` and
`meet_end_active_conference` return the Google Meet resource as-is. Output
inference could not type that value and built the schema from the only literal
it saw — the error dictionary — producing a **closed** schema (`additionalProperties: false`)
with only `error`, `provider_status`, `context` and `resource`. Every successful
Meet space response (`name`, `meetingUri`, `config`, ...) therefore violated its
own published schema. With the error dictionary gone the schema is FastMCP's
open object:

```diff
 "meet_get_space".outputSchema:
-{"type": "object", "additionalProperties": false, "title": "Get Space response",
- "description": "Structured response returned by this MCP tool.", "required": [],
- "properties": {"context": ..., "error": ..., "provider_status": ..., "resource": ...}}
+{"type": "object", "additionalProperties": true}
```

A typed Meet response model is left to a later schema-hardening pass (plan
section 3.5: replace inference incrementally with typed models).

## 3. `refresh_workspace_catalog` — intended

The tool no longer calls `ctx.reset_visibility()` (connection visibility
state) and no longer claims a `tools/list_changed` notification it did not
deliver (production HTTP had `json_response=True`, and the server advertises
`tools.listChanged: false`). `tools/list` and `tools/call` authorize against
the grant read on each request, so there is nothing to refresh.

```diff
-"description": "Refresh capability-aware tools after Google consent or disconnection."
+"description": "Report the caller's current Google grants after consent or disconnection.\n\ntools/list is evaluated against the caller's current grants on every\nrequest, so no refresh is needed for the catalog to change: list tools\nagain. No tools/list_changed notification is sent."
```

Output: `status` is now `"catalog_current"`; `notification_sent` changed from
the string `"tools/list_changed"` to the boolean `false`; added `connected`,
`grant_revision` (opaque digest of the stored grant, or null) and
`next_action`.

```diff
-"notification_sent": {"description": "Response field: notification sent.", "type": "string"}
+"notification_sent": {"description": "Response field: notification sent.", "type": "boolean"}
+"connected": {...}, "grant_revision": {...}, "next_action": {...}
```

## Not changed

- Remotely, `prepare_workspace_action`, `commit_workspace_action` and other
  capability-free root tools are now listed for authenticated callers (the old
  filter hid every root tool that was not on an explicit allow list). The
  snapshots are taken unauthenticated, so they do not show this.
- `ttlMs: 0` / `cacheScope: "private"` on every list result (FastMCP default,
  kept deliberately).
