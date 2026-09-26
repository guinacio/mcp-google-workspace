# W4a — Catalog contract diff

Regenerated with `uv run python scripts/snapshot_catalog.py`; the only changes
to `tests/contracts/catalog_default.json` and
`tests/contracts/catalog_all_optional.json` are listed below. Tool, resource,
template and prompt counts are unchanged. The confirmation guard installed
around every async tool body (`common/confirmation.py`) does not change any
published input or output schema.

## 1. Chat `message` output field (pre-existing bug found in W2)

`chat_create_message`, `chat_post_message_simple`, `chat_reply_to_message`
**and `chat_update_message`** (same defect, not in the W2 note) declared
`message` as `["string", "null"]` in their output schema, but return the Google
Chat `Message` object, so strict clients rejected successful results.

Cause: output-schema inference could not type the value
(`execute_google_request` returns `Any`) and fell back to the field-name
heuristic, which treats `message` as a string. Fix: the four tools execute
their create/patch call through `_execute_message_request(...) -> dict[str, Any]`
in `chat/tools.py`, so inference reads an object.

```diff
 "message": {
   "description": "Response field: message.",
-  "type": ["string", "null"]
+  "type": "object"
 }
```

Only in `catalog_all_optional.json` (Chat is optional).

## 2. `commit_workspace_action` descriptions

The token is no longer destroyed before the bound tool runs (claim →
release on a confirmation question or pre-execution rejection → consume on
completion), so the wording changed. Both snapshots:

```diff
-"description": "Atomically consume a prepared action token and execute its exact bound arguments."
+"description": "Claim a prepared action token and execute its exact bound arguments once."
```

```diff
-"...expires 5 minutes after issuance and is consumed on first use."
+"...expires 5 minutes after issuance and is consumed once the bound action runs (a confirmation question keeps it valid for the answering call)."
```

The output schema (registered in `common/output_schemas.py`) is unchanged. At
runtime an asking round now returns `resultType: input_required` instead of a
`status: committed` wrapper, and a nested `isError` result is returned as-is
instead of being wrapped as `committed`.
