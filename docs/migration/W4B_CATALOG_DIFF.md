# W4b — Catalog contract diff

Regenerated deliberately with `uv run python scripts/snapshot_catalog.py`. The
only changes to `tests/contracts/catalog_default.json` and
`tests/contracts/catalog_all_optional.json` are listed below. Tool, resource,
template and prompt counts are unchanged (140 default / 192 all-optional tools).
No output schema changed: saved-result replays and `outcome_unknown` results use
the existing envelopes (replays carry `_meta["mcp-google-workspace/operation"]`,
which is not part of `structuredContent`).

## 1. `commit_workspace_action.commit_token` description (both snapshots)

The token now lives as long as a confirmation question
(`MCP_CONFIRMATION_TTL_SECONDS`, default 600 s; W4a had 300 s tokens and 600 s
questions), and a repeated commit returns the saved result instead of failing.

```diff
-"...expires 5 minutes after issuance and is consumed once the bound action runs (a confirmation question keeps it valid for the answering call)."
+"...expires 10 minutes after issuance by default (MCP_CONFIRMATION_TTL_SECONDS). The bound action runs at most once: repeating a commit that already ran returns its saved result, and a confirmation question keeps the token valid for the answering call."
```

## 2. `calendar_delete_event.force` description (both snapshots)

Owner decision 9.1: the flag keeps its default (`false`) and behavior; only the
description was fixed. The published text was the generic generated one; the
`DeleteEventRequest.force` model field also claimed a default of `true` (now
`false`, matching the tool signature, which always passes the value).

```diff
 "force": {
   "default": false,
-  "description": "Whether to enable force.",
+  "description": "Skip the interactive confirmation when true. Default false: the deletion is confirmed with the user first (clients that cannot confirm get a confirmation_required result and nothing is deleted).",
   "type": "boolean"
 }
```

## 3. `drive_delete_file.confirm_permanent` description (both snapshots)

Default (`true`) and behavior unchanged. The model field described a default of
`false` and a confirmation it does not control.

```diff
 "confirm_permanent": {
   "default": true,
-  "description": "Whether to enable confirm permanent.",
+  "description": "Must stay true for delete_mode='permanent' (false is rejected); a permanent delete is always confirmed interactively. Ignored for delete_mode='trash'.",
   "type": "boolean"
 }
```

## 4. `gmail_delete_thread.force` removed (both snapshots)

The parameter was accepted and ignored (the thread deletion is always
confirmed). Because the input schema is closed (`additionalProperties: false`),
a client that still sends `force` now gets an input-validation error instead
of having it silently ignored.

```diff
 "properties": {
-  "force": {
-    "default": false,
-    "description": "Whether to enable force.",
-    "type": "boolean"
-  },
   "thread_id": {
```

## 5. `chat_create_message` `request.request_id` description (all-optional only)

The parameter already existed and was already passed to Google Chat as
`requestId`; the description now says what reusing it does. When it is omitted
the server now sends a per-call `requestId` (`mcpcall-<uuid>`) so transport
retries cannot post twice (runtime change, not visible in the catalog).

```diff
-"description": "Idempotency key for message creation."
+"description": "Idempotency key for message creation: Google Chat returns the existing message instead of posting again when the same request_id is reused, so reuse it when retrying after an unknown outcome."
```

## Not in the catalog

- `prepare_workspace_action` became an `async` tool (it writes the operation
  record); its published schemas are unchanged.
- New runtime results: `outcome_unknown`, `operation_in_progress`,
  `operation_failed`, `operation_already_succeeded` (`isError` tool results with
  the standard error envelope); see `W4_CONFIRMATION_POLICY.md` sections 6–8.
