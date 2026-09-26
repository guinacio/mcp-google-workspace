# W4a — Confirmation adapter and confirmation policy inventory

Work package **W4a** of `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` (sections 3.3,
3.4, 6 "W4", 9.1). It replaces the W2 fail-closed gate with one confirmation
adapter that has a multi-round-trip (MRTR) branch for MCP 2026-07-28 and a thin
`ctx.elicit` branch for handshake-era clients. Durable operation records,
result replay and uncertain-outcome reconciliation are **W4b**.

## 1. Adapter

Module: `src/mcp_google_workspace/common/confirmation.py`. Every one of the 24
confirmation sites (W0 inventory, section 5.1/5.2) calls
`confirm_destructive_action(ctx, action_name, preview, *, explicit_confirm_field=False)`
after validating its arguments and computing the exact preview, and before any
provider mutation. The only change at each call site is the import (the old
gate in `common/async_ops.py` is removed).

| Request | Behavior |
| --- | --- |
| 2026-07-28, client declared `elicitation`, no answer yet | Returns `InputRequiredResult` with one form elicitation (key `confirm`) and a continuation in `requestState`. **No mutation.** |
| 2026-07-28 retry with `inputResponses` + `requestState` | Verifies the continuation and the answer. `accept` + `true` executes once; `decline`, `cancel`, or an unticked box returns the site's `status: cancelled` result with no mutation. |
| Invalid retry (tampered, expired, other principal, other tool, changed arguments or preview, replayed, wrong/missing answer, answer without state) | `isError` tool result, code `confirmation_invalid`, `required_action.action = restart_confirmation`, no mutation. The FastMCP wire seal rejects tampered/expired/changed-argument state even earlier with JSON-RPC `-32602 Invalid or expired requestState`. |
| Handshake-era version, client declared `elicitation` | `ctx.elicit(..., response_type=bool)` or the explicit `Confirmation{confirm: bool}` schema (send/reply), unchanged from before. |
| No elicitation capability, unknown/missing version, no request | `isError` tool result, code `confirmation_required`, with the exact prompt. Never consent. |
| Background task (Tasks extension) | Guard pattern: the task parks in `input_required`, the client answers through `tasks/update`, the worker re-runs with the answer (section 4). |

### 1.1 How the ask leaves the tool body

`apply_default_tool_annotations` (already applied to every namespace server and
the root) installs a **confirmation guard** around every async tool body
(`install_confirmation_guard`). The guard:

1. binds the tool identity (`<module>:<registered name>`) and the validated
   call arguments in a context variable for the adapter;
2. catches this call's `ConfirmationInputRequired` and returns its
   `InputRequiredResult` as the tool's return value, which FastMCP 4.0.10 wraps
   as `InputRequiredToolResult`.

`ConfirmationInputRequired` subclasses `ConfirmationRequiredError`, so if it
ever escaped a guard the call would still fail closed. A call to the adapter
outside a guard cannot bind a continuation and also fails closed. The guard
uses `functools.wraps` and is installed after output-schema inference, so the
published input/output schemas are unchanged (catalog snapshot confirms it).

### 1.2 Continuation state

Plaintext (what the tool sees in `ctx.request_state`):
`cw1.<base64url(JSON claims)>.<key id>.<HMAC-SHA256>`, where the claims are:

| Claim | Meaning |
| --- | --- |
| `v` | format version (1) |
| `op` | random operation id (`secrets.token_urlsafe(18)`), single use |
| `tool` | `<module>:<registered tool name>` of the guarded tool (the *inner* tool, even behind `call_tool` or `commit_workspace_action`) |
| `action` | the site's action name (`reply_email` vs `reply_all_email`) |
| `args` | SHA-256 of the canonical validated arguments |
| `preview` | SHA-256 of `action \0 exact prompt text` (a reply whose recipients changed between rounds is not the action the user saw) |
| `sub` | principal digest: SHA-256 of `issuer \0 subject` from `auth/identity.py` (`Principal.storage_key`); stdio uses the trusted local principal `("local", MCP_LOCAL_PRINCIPAL)` |
| `kind` | answer schema asked (`value` or `confirm`) |
| `iat`, `exp` | issue time and expiry (`MCP_CONFIRMATION_TTL_SECONDS`, default 600) |

No message body, prompt text, argument value, token, or answer is stored in
the state or logged. Rejections log only `action` and a reason code.

**Canonical arguments:** the guarded tool's keyword arguments as FastMCP
validated them, with defaults applied and the injected `Context` parameter
removed, converted with `pydantic_core.to_jsonable_python` and serialized as
sorted-key, compact, ASCII JSON with `allow_nan=False`. The answer is never
part of the arguments; it arrives in `inputResponses`.

**Two layers of integrity.** On the wire FastMCP's `RequestStateBoundary`
encrypts and authenticates the whole string (AES-256-GCM) and binds it to the
wire method, wire tool name, wire argument digest, audience
(`google-workspace-mcp`), authenticated token principal, and TTL. The
application HMAC keeps the guarantees where the wire seal does not apply (the
Tasks extension stores in-task state server side) and binds the inner tool and
arguments when the wire call is a proxy.

**Verification order on retry:** HMAC, version, expiry (60 s future skew),
principal, tool/action/kind, argument digest, preview digest; then the answer
(must be an `ElicitResult`; `accept` content must be exactly `{field: bool}`);
then an atomic claim of `op` in the replay store. Rejections before the claim
do not consume the continuation.

### 1.3 Replay store

`ContinuationReplayStore.claim(operation_id, ttl_seconds) -> bool` (async), one
method. `MemoryReplayStore` for stdio, single-process servers and tests;
`RedisReplayStore` (`SET mcp:confirmation:used:<sha256(op)> 1 NX EX ttl`) when
`MCP_REDIS_URL` is set, except in the stdio bundle (`MCP_RUNTIME_MODE=bundle`).
Any answered continuation (accept, decline or cancel) is claimed, so it cannot
be replayed or flipped from decline to accept. W4b replaces this with durable
operation records.

### 1.4 Sealing keys

`MCP_REQUEST_STATE_KEYS`: comma-separated secrets, first = active (seals), all
listed keys verify. Each entry is at least 32 bytes and is used verbatim as
FastMCP `RequestStateSecurity(keys=[...])` key material (the application HMAC
key is derived from the same ring with HKDF under a separate label). Generate
with `python -c "import secrets; print(secrets.token_hex(32))"`. Without the
variable each process uses an ephemeral key: correct for stdio; HTTP logs a
warning; `/health/ready` fails when `MCP_WORKERS > 1`
(`checks.continuation_keys`). Rotation (independent of
`MCP_TOKEN_ENCRYPTION_KEY`/`MCP_SECRET_FILE` and `FASTMCP_TASKS_ENCRYPTION_KEY`):
`old` → `new,old` (fleet-wide) → wait one TTL → `new`. See `.env.example`.

## 2. Wrapper threading

| Wrapper | Result | Evidence |
| --- | --- | --- |
| `StructuredToolErrorMiddleware` | Passes `InputRequiredToolResult` through untouched; renders `ConfirmationRequiredError` and `ConfirmationRejectedError` (base `ConfirmationError`) as `isError` tool results, never protocol errors. | `test_confirmation_wrappers.py::test_middleware_passes_the_ask_through_untouched[structured-errors]` |
| `ResourceHandleMiddleware` | Skips `InputRequiredToolResult` (W2 code) — no handles attached. | `...[resource-handles]` |
| Pagination wrapper (`_paginated_result`) | Only decorates dicts; an `InputRequiredResult` passes unchanged, also under the guard. | `test_pagination_wrapper_and_guard_leave_an_ask_unchanged` |
| Output schemas / validation | The ask never carries `content`/`structuredContent` (FastMCP's `_on_call_tool` returns the raw `InputRequiredResult` before result normalization), so there is nothing for output validation to reject. All 23 site tools publish output schemas; their ask rounds and final results pass through the SDK and FastMCP clients. | `test_modern_ask_round_returns_input_required_before_any_mutation`, `test_guard_preserves_signature_and_published_schemas` |
| `ProductionControlMiddleware` (admission/telemetry) | Counts an ask as outcome `input_required` (metric label, log line, span attribute `mcp.tool.round`), not `ok`. Each round is still admitted and rate-limited as a request. | `test_admission_telemetry_counts_an_ask_as_a_round_not_a_completion` |
| `ConsequentialActionMiddleware` | Unchanged; the answering commit round runs under `COMMIT_ACTIVE`. | commit tests |
| `commit_workspace_action` + `ApprovalStore` | Claims the token; an ask **releases** it and returns the question unwrapped (not `status: committed`); a nested `isError` result is returned as-is (previously wrapped as `committed`). | `test_commit_asking_round_keeps_the_approval_token`, `test_commit_decline_consumes_the_token_without_sending` |
| BM25 `call_tool` proxy | Returns the nested `ToolResult` object, so the `InputRequiredToolResult` reaches the wire intact; the wire seal binds `call_tool` + its arguments, the app state binds the inner tool. | `test_bm25_call_tool_proxy_passes_the_ask_through_intact` |
| Tasks extension | See section 4. | `test_tasked_tool_parks_for_input_and_resumes_via_tasks_update` |

### 2.1 Prepare/commit token lifecycle (minimal fix)

`ApprovalStore`/`RedisApprovalStore` now expose `claim` → `release` | `complete`
(`consume` = claim + complete remains for single-shot callers). SQLite adds a
`claimed` column (added in place on an existing table); Redis uses a
`SET NX PX <remaining TTL>` claim marker. A claim is exclusive: a concurrent
commit of the same token is refused while one is running.

| Nested outcome | Token |
| --- | --- |
| `InputRequiredToolResult` (asked a question) | released |
| `isError` / `McpError` with a code in `PRE_EXECUTION_ERROR_CODES` (`confirmation_required`, `confirmation_invalid`, `prepare_required`, `rate_limited`, `server_draining`, `principal_revoked`, `authorization_backend_unavailable`, `reauth_required`, `missing_capability`) | released |
| completed (including a declined confirmation) | consumed |
| any other failure, cancellation or unknown exception | consumed (outcome may be uncertain; never retried blindly) |

The brief asked for "release on failure". Releasing on *every* failure would
let a lost response after a successful Google send be retried into a
duplicate, which the previous destructive consume prevented; only failures that
provably executed nothing release the token. W4b's operation records
(`prepared → awaiting_input → executing → succeeded | failed | outcome_unknown`)
plug in behind the same claim/release/complete seam.

## 3. Confirmation bypass flags (inventory only — behavior unchanged)

Flags a caller can set so that a site runs **without** asking. Defaults are the
published input-schema defaults. Policy is an owner decision; nothing here was
changed.

| Tool | Flag (default) | Runs without confirmation when | What it bypasses | Reversibility |
| --- | --- | --- | --- | --- |
| `calendar_delete_event` | `force` (`false`) | `force=true` | Event deletion (and guest cancellation notices per `send_updates`) | Event recoverable from Calendar's trash in the UI for ~30 days; cancellation emails cannot be recalled |
| `chat_create_message` | `notify` (`false`) | `notify=false` (the default) | Posting a message to a space | Irreversible delivery (message can be deleted later but may have been read/notified) |
| `chat_post_message_simple` | `notify` (`false`) | `notify=false` (the default) | Posting a message | Same as above |
| `chat_reply_to_message` | `notify` (`false`) | `notify=false` (the default) | Posting a threaded reply | Same as above |
| `chat_delete_message` | `force` (`false`) | `force=true` | Deleting a message | Irreversible |
| `drive_delete_file` | `delete_mode` (`trash`) | `delete_mode=trash` (the default) | Moving the file to trash | Reversible (Drive trash, ~30 days) |
| `drive_delete_file` | `confirm_permanent` (`true`) | never: `false` with `delete_mode=permanent` is **rejected** (`ValueError`) | — (not a bypass) | — |
| `gmail_batch_delete` | `permanent` (`false`) | `permanent=false` (the default) | Trashing each message | Reversible (Gmail trash, ~30 days) |
| `gmail_delete_email` | `permanent` (`false`) | `permanent=false` (the default) | Trashing the message | Reversible (Gmail trash) |
| `gmail_send_email` | `confirm_send` (`false`) | `confirm_send=false` (the default) | Sending the email | Irreversible. ≥10 recipients still require prepare/commit (an impact preview, not a user confirmation) |
| `gmail_reply_email` / `gmail_reply_all_email` | `confirm_send` (`false`) | `confirm_send=false` (the default) | Sending the reply | Irreversible |
| `keep_create_note` | `confirm_create` (`false`) | `confirm_create=false` (the default) | Creating a note (+ collaborator grants) | Reversible (note can be deleted; collaborators were already granted) |
| `keep_delete_note` | `confirm_delete` (`false`) | `confirm_delete=false` (the default) | Deleting a note | Irreversible via the API |
| `gmail_delete_thread` | `force` (`false`) | never — the flag is accepted but ignored | — | Permanent delete, always confirmed |

Always confirmed (no flag): `calendar_remove_event_attachment`,
`drive_create_permission`, `drive_update_permission`, `drive_delete_permission`,
`gmail_delete_draft`, `gmail_delete_filter`, `gmail_delete_label`,
`gmail_delete_forwarding_address`, `people_delete_contact`, `tasks_delete_task`,
`gmail_delete_thread`.

Observations for the owner (not acted on):

- Seven sites default to *no* confirmation (`notify`, `confirm_send`,
  `confirm_create`, `confirm_delete`, `delete_mode=trash`, `permanent=false`),
  so the model decides whether the user is asked. Three of them are
  irreversible (Chat posts, email sends, Keep deletes).
- `DeleteEventRequest.force` documents a default of `true` while the tool
  signature (the published schema) defaults to `false`; `DeleteFileRequest.confirm_permanent`
  describes a default of `false` while it is `true`. The descriptions are stale.
- `gmail_delete_thread.force` is dead: it suggests a bypass that does not exist.
- Tools that mutate without any confirmation site at all (e.g.
  `gmail_batch_modify`, `drive_upload_file`, `sheets_batch_update_spreadsheet`)
  are outside this inventory; some are covered by prepare/commit.

## 4. Tasks + input (FastMCP 4.0.10 / fastmcp-tasks 4.0.10)

Behavior implemented: **supported (guard pattern)**. None of the 24 sites is a
`task=True` tool today, so this only matters for future tasked mutations and
nested calls from a worker.

- A tasked tool that asks returns the `InputRequiredResult` from its body.
  `fastmcp_tasks.input_loop.reentrant_task_fn` stores the outstanding requests
  and `requestState` for the task and ends the Docket execution; the task is
  `input_required`. The client answers with `tasks/update`; a new execution
  re-runs the tool with `ctx.input_responses`/`ctx.request_state` injected.
  FastMCP's `Client` drives this through its elicitation handler.
- Imperative `ctx.elicit` inside a task raises in 4.0.10; the adapter never uses
  it there.
- The worker context has no live session, so the client's elicitation
  capability cannot be read. The adapter asks anyway: a client that cannot
  answer leaves the task parked until it expires, which is "not performed",
  never consent.
- The wire seal does not apply to in-task state (it is stored server side); the
  application HMAC, expiry, principal (restored from the task snapshot), tool,
  argument and replay checks still do.
- A task tool invoked by another tool (BM25 `call_tool`, commit) runs in the
  foreground in 4.0.10, so its ask follows the foreground MRTR path.

## 5. Tests

- `tests/test_confirmation_protocols.py` — all 24 sites: modern ask round (no
  mutation), accept (exactly one mutation), decline/cancel (none), the stock
  client driver end to end, no-elicitation fail-closed on both eras, legacy
  decline/accept, adapter-only elicitation scan.
- `tests/test_confirmation_adapter.py` — application-layer and full-composition
  cases: tampered, expired (and configurable TTL), wrong principal, changed
  arguments, other tool, changed preview, replayed accepted continuation,
  decline-then-accept reuse, wrong answer types, missing answer, answer without
  state, replica switch, foreign ring, key rotation (both layers), replay
  stores, key-ring validation, canonicalization.
- `tests/test_confirmation_wrappers.py` — wrapper threading (section 2),
  prepare/commit token lifecycle, SQLite/Redis claim semantics, tasks.
- `tests/test_http_wire.py` — raw 2026-07-28 HTTP: `resultType: input_required`
  with no side effect, then `inputResponses` + `requestState` completes; no
  capability fails closed.
