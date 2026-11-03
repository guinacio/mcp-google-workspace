# W4 — Confirmation adapter, operation records and mutation recovery

Work packages **W4a** and **W4b** of `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md`
(sections 3.2, 3.3, 3.4, 6 "W4", 8, 9.1). W4a replaced the W2 fail-closed gate
with one confirmation adapter that has a multi-round-trip (MRTR) branch for MCP
2026-07-28 and a thin `ctx.elicit` branch for handshake-era clients (sections
1–5). W4b replaced destructive commit-token consumption and the W4a replay set
with durable operation records, saved-result replay, and `outcome_unknown`
reporting and reconciliation for non-idempotent Google calls (sections 6–9).

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
| Invalid retry (tampered, expired, other principal, other tool, changed arguments or preview, a different answer than the one already recorded, wrong/missing answer, answer without state) | `isError` tool result, code `confirmation_invalid`, `required_action.action = restart_confirmation`, no mutation. The FastMCP wire seal rejects tampered/expired/changed-argument state even earlier with JSON-RPC `-32602 Invalid or expired requestState`. |
| Repeat of an answered retry with the same answer (W4b) | The operation record answers: the saved result (`_meta["mcp-google-workspace/operation"].replayed = true`), `operation_in_progress`, the saved failure, or `outcome_unknown`. The tool body never runs twice. |
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
| `op` | random operation id (`secrets.token_urlsafe(18)`); keys the operation record (section 6) and is never stored or shown raw |
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
then an atomic claim of the `op` operation record (section 6), which also
re-checks the argument and preview digests and the recorded answer. Rejections
before the claim do not consume the continuation.

### 1.3 Replay protection (W4b: operation records)

The asking round opens an `awaiting_input` operation record keyed by
`(principal, SHA-256(op))`; the answering round claims it. One continuation
carries one answer: a decline (or cancel, or unticked box) cannot later become
an accept, nor an accept a decline (`confirmation_invalid`, reason
`replayed`). A repeat of the same answer returns the saved outcome instead of
running the tool again. The W4a `ContinuationReplayStore` (`SET NX` of used ids)
is removed. See section 6.

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
| `commit_workspace_action` + operation record | Claims the record; an ask **parks** it (`awaiting_input`) and returns the question unwrapped (not `status: committed`); a nested `isError` result is returned as-is (previously wrapped as `committed`). | `test_commit_asking_round_keeps_the_approval_token`, `test_commit_decline_consumes_the_token_without_sending` |
| `OperationOutcomeMiddleware` (W4b) | Sits between the error envelope and `ProductionControlMiddleware`; a failure or deadline while a non-repeatable Google call may have run becomes an `outcome_unknown` tool result. An ask passes through. | `test_deadline_while_a_send_executes_reports_outcome_unknown` |
| BM25 `call_tool` proxy | Returns the nested `ToolResult` object, so the `InputRequiredToolResult` reaches the wire intact; the wire seal binds `call_tool` + its arguments, the app state binds the inner tool. | `test_bm25_call_tool_proxy_passes_the_ask_through_intact` |
| Tasks extension | See section 4. | `test_tasked_tool_parks_for_input_and_resumes_via_tasks_update` |

### 2.1 Prepare/commit lifecycle

W4a added claim → release | complete to the SQLite/Redis approval stores. W4b
removed both stores (no migration of old tokens, per owner decision):
`prepare_workspace_action` opens a `prepared` operation record, and
`commit_workspace_action` claims it. The confirmation guard around the commit
settles the claim from what actually happened:

| Nested outcome | Record |
| --- | --- |
| `InputRequiredToolResult` (asked a question) | `awaiting_input` (TTL restarts with the question) |
| completed (including a declined confirmation) | `succeeded`, minimized result saved |
| failure where no non-repeatable Google call started, or a code in `PRE_EXECUTION_ERROR_CODES` | back to `prepared` (the same token can be committed again) |
| Google definitively rejected (4xx) a non-repeatable call | `failed`, short error saved |
| a non-repeatable call may have been applied (timeout, disconnect, cancellation, 5xx, lost worker) | `outcome_unknown` |

A repeated commit of a finished operation returns the saved result (or the
saved failure, or `outcome_unknown`); it never executes again. Two
simultaneous commits: one claims, the other gets `operation_in_progress`.

## 3. Confirmation bypass flags (inventory only — behavior unchanged)

Flags a caller can set so that a site runs **without** asking. Defaults are the
published input-schema defaults. Policy is an owner decision (plan section 9.1:
the flags stay model-controlled); defaults and behavior are unchanged. W4b only
fixed the stale descriptions and removed the dead `gmail_delete_thread.force`.

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
- Fixed in W4b: `DeleteEventRequest.force` documented a default of `true`
  while the published schema defaults to `false`, and
  `DeleteFileRequest.confirm_permanent` described a default of `false` while it
  is `true`. Both models and the published parameter descriptions now match.
- Removed in W4b: `gmail_delete_thread.force` was accepted and ignored.
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

## 5. Tests (W4a)

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

## 6. Operation records (W4b)

Module: `src/mcp_google_workspace/common/operations.py`. One store for
prepare/commit tokens, confirmation continuations and evidence of uncertain
plain calls.

### 6.1 Lifecycle

```
prepared ──claim──► executing ──► succeeded | failed | outcome_unknown
awaiting_input ──claim──►   │  ▲
      ▲                     │  └── release (nothing non-repeatable ran)
      └──── park (asked) ───┘
```

| State | Meaning | Reached by |
| --- | --- | --- |
| `prepared` | commit token issued, not committed | `prepare_workspace_action`; release |
| `awaiting_input` | a confirmation question is open | adapter ask round; a commit whose tool asked (park); release |
| `executing` | claimed by one request (`claim_id`) until `lease_until` | claim (compare-and-set on the record revision) |
| `succeeded` | done; minimized result saved; a declined confirmation also ends here (`answer: decline`, the tool's `status: cancelled`) | completion; positive reconciliation; a late completion of an `outcome_unknown` claim |
| `failed` | Google definitively rejected a non-repeatable call | completion |
| `outcome_unknown` | a non-repeatable call may or may not have been applied | uncertain completion; a retry that finds `executing` after its lease expired |

Claims and completions are atomic: every transition reads the record and
writes it back with compare-and-set on its revision (W3 `AppStateStore`: a lock
in memory, one Lua script per write in Redis). Of simultaneous claims exactly
one succeeds; the others see `executing` and get `operation_in_progress`
(retryable, `after_seconds: 2`). Completions only apply to the claim that owns
the record (`claim_id`), so a superseded claimant cannot overwrite a newer
outcome, but a late claimant may still turn its own `outcome_unknown` into the
real result.

A crash, lost worker or stuck thread leaves `executing`. The first retry
after `lease_until` moves the record to `outcome_unknown` and reports it; it is
never re-executed automatically.

### 6.2 Backends, retention and configuration

| Backend | When | Notes |
| --- | --- | --- |
| Memory (`MemoryAppStateStore`, 100 000 entries, evicts nearest-expiry first) | stdio, single process, tests; always in the stdio bundle | In-process only; a restart forgets records (a restarted stdio server cannot replay an earlier result). |
| Redis (`RedisAppStateStore`, prefix `mcp:operation:v1`) | `MCP_REDIS_URL` set, `MCP_RUNTIME_MODE` ≠ `bundle` | Bodies Fernet-encrypted with the token key ring (`MCP_SECRET_FILE` / `MCP_TOKEN_ENCRYPTION_KEYS` / `MCP_TOKEN_ENCRYPTION_KEY`); missing ring → error at first use. |

| Variable | Default | Governs |
| --- | --- | --- |
| `MCP_CONFIRMATION_TTL_SECONDS` | 600 | `prepared` and `awaiting_input` records (and the continuation). The W4a mismatch — 300 s tokens, 600 s questions — is gone: a commit token and the question asked while committing it share one TTL, restarted when the question is asked. |
| `MCP_OPERATION_LEASE_SECONDS` | 900 | `executing` lease; keep above `MCP_TOOL_DEADLINE_SECONDS` (120) and `MCP_EXPENSIVE_DEADLINE_SECONDS` (600). The record itself is kept lease + retention. |
| `MCP_OPERATION_RETENTION_SECONDS` | 86 400 (24 h) | `succeeded` / `failed` / `outcome_unknown` records, for replay, reconciliation and operator evidence. |
| `MCP_OPERATION_RESULT_MAX_BYTES` | 65 536 | Largest saved result; larger results are not retained and a repeat reports `operation_already_succeeded` (not re-executed). |

A continuation cannot be replayed after its own expiry (the wire seal and the
application MAC both expire at `MCP_CONFIRMATION_TTL_SECONDS`), so saved
confirmation results are replayable within that window; commit replays work
for the whole retention period.

### 6.3 Stored fields

Key: `<principal storage key>:<SHA-256(operation id)>` — the raw commit token or
continuation id is never stored. Public reference shown to the model:
`op_` + 24 hex of `SHA-256("ref" ‖ id)`.

| Field | Content |
| --- | --- |
| `v`, `kind` (`commit` / `confirmation` / `call`), `state` | format and lifecycle |
| `tool`, `action` | public tool name (commit) or `<module>:<tool>` (confirmation); the site's action name |
| `args_digest`, `preview_digest` | SHA-256 digests only |
| `arguments` | commit records only, while `prepared`/`awaiting_input`/`executing` (needed to execute); dropped at every terminal state |
| `answer` | `accept` / `decline` (never the elicitation content) |
| `claim_id`, `lease_until`, `resume_state`, `pending_expires_at`, `created_at`, `updated_at` | concurrency and expiry |
| `result`, `result_retained` | saved result: the tool's structured result with the text of `raw`, `text`, `text_body`, `html_body`, `body`, `snippet`, `formattedText`, `argumentText`, `textContent` replaced by a placeholder (types kept, so replays still match the output schema); IDs, statuses and metadata are kept |
| `error` | `{code, message}` (message truncated to 300 characters) |
| `uncertain_calls`, `reconcile`, `lease_expired`, `reconciled` | Google method ids, identifier-only reconciliation hints (e.g. the RFC 822 Message-ID) |

Never stored: Google tokens, MCP bearer tokens, continuation strings, the
confirmation prompt, message bodies, argument values of confirmation sites.
Logs carry only tool names, public references, method ids and reason codes.

## 7. Repeat safety of Google calls (W4b)

Module: `src/mcp_google_workspace/common/repeat_safety.py`, table
`METHOD_POLICIES` keyed by discovery `methodId`; tested by
`tests/test_operation_records.py::test_repeat_safety_is_classified_from_the_google_method`
and `test_every_mutating_method_the_server_calls_is_in_the_table` (an AST scan of
every service package). Classification comes from the Google method and its
parameters, not the tool name. Unknown methods: names starting
`get`/`list`/`search`/`query`/`batchGet`/`export`/`find` are reads, anything
else is non-idempotent.

| Class | Meaning | Methods (summary) |
| --- | --- | --- |
| `read` | no change | get/list/search/query/… |
| `idempotent` | repeat gives the same end state | Gmail modify/batchModify/trash/untrash/delete/batchDelete, threads.*, labels patch/delete, filters/forwarding delete, updateVacation, drafts.update/delete; Calendar events.patch/update/delete/move; Drive files.update/delete, permissions.update/delete, drives.update/hide/unhide; Sheets values.update/batchUpdate/clear; Tasks patch/update/delete/move; People updateContact (etag), deleteContact, contactGroups.members.modify; Keep notes.delete, permissions batch*; Chat messages.patch/delete; Meet spaces.patch/endActiveConference; Forms setPublishSettings |
| `caller_keyed` | the provider deduplicates on a key the caller resends | Calendar `events.insert` **with** body `id` (from `idempotency_key`); Chat `spaces.messages.create` with a caller `request_id`; Drive `drives.create` `requestId`; Docs/Slides/Forms `batchUpdate` with `writeControl.requiredRevisionId` (a repeat is rejected, not applied; not sent by this server today); Drive `files.create`/`copy` with a `generateIds` id (not used today) |
| `transport_keyed` | the key is generated per tool call | Chat `spaces.messages.create` with the server's `mcpcall-<uuid>` `requestId` |
| `non_idempotent` | a repeat can duplicate | Gmail `messages.send`, `drafts.send`, `drafts.create`, `labels.create`, `filters.create`, `forwardingAddresses.create`; Calendar `events.insert` without id, `quickAdd`, `import`; Drive `files.create`/`copy` (no id), `permissions.create` (notification email), comments/replies; Sheets `spreadsheets.create`, `batchUpdate`, `values.append`; Docs/Slides/Forms `create` and `batchUpdate`; Tasks `insert`; People `createContact`, `contactGroups.create`; Keep `notes.create`; Chat `spaces.setup`, members/reactions create; Meet `spaces.create` |

Provider idempotency checked against the API references (2026-09-26): Chat
`spaces.messages.create` `requestId` ("multiple identical requests with the same
request ID result in only a single message being created"), now always sent —
the caller's `request_id` when given, otherwise a per-call id; Drive
`drives.create` `requestId`; Drive `files.generateIds` ids usable in
create/copy; Docs/Slides/Forms `writeControl.requiredRevisionId`. **No**
idempotency key exists for Gmail send, Drive `files.create` itself, Sheets
`batchUpdate`/`append` (the Sheets reference has neither a request id nor write
control), Tasks, People, Keep or Meet creates.

Consequences:

1. **Transport retries** (`RetryingHttpRequest`, googleapiclient `num_retries`
   for 5xx/429/socket errors) are limited to `read`, `idempotent`,
   `caller_keyed` and `transport_keyed` calls. Before W4b a socket timeout on
   `messages.send` was silently resent up to `MCP_GOOGLE_HTTP_RETRIES` times.
2. **Tracking.** `LazyGoogleRequest.execute` (every Workspace client call) and
   the Drive resumable-upload loop record each non-read call on a per-tool-call
   `MutationTracker`: in flight, applied, definitively rejected (HTTP 4xx other
   than 408), or ended ambiguously (5xx, timeout, connection error,
   cancellation). A tracker is closed when its tool call ends; a thread
   abandoned on cancellation that only then reaches a mutating call is refused
   (`LateProviderCallError`), so no send starts unaccounted for.

## 8. Uncertain outcomes and reconciliation (W4b)

When a tool call fails, times out (`MCP_TOOL_DEADLINE_SECONDS`), is cancelled
or disconnected — or returns an error payload it built after swallowing such a
failure — while a non-repeatable (`non_idempotent` or `transport_keyed`) call
was in flight, ended ambiguously, or had already been applied, the result is an
`isError` tool result:

```json
{"code": "outcome_unknown", "retryable": false,
 "required_action": {"action": "verify_before_retry", "operation_ref": "op_…",
   "uncertain_calls": ["gmail.users.messages.send"],
   "verify": [{"tool": "gmail_search_emails", "arguments": {"query": "in:sent rfc822msgid:<…@mcp-google-workspace.local>"}, "check": "…"}],
   "instructions": "…"}}
```

The claimed operation (or, for a plain call, a new `call` evidence record) is
set to `outcome_unknown`. Repeat-safe failures (for example a timeout on a
Calendar insert with `idempotency_key`, or on a delete) stay ordinary
retryable errors.

| Operation | Reconciliation | Where |
| --- | --- | --- |
| Gmail `send_email`, `reply_email`, `reply_all_email` (`users.messages.send`) | **Implemented.** Every outgoing message gets a fresh `Message-ID` (`<…@mcp-google-workspace.local>`); a repeat of the operation searches `in:sent rfc822msgid:<id>`. A match resolves it to `succeeded` with the found ids; no match leaves `outcome_unknown` (search can lag; absence is not proof) and never re-sends. Plain calls get the same query as `verify` guidance. Caveat: if Gmail replaces a client Message-ID (reported for malformed ids) the search finds nothing and the outcome stays unknown — safe, never a duplicate. | `common/reconciliation.py` |
| Calendar `create_event` with `idempotency_key` | **Implemented (retry is the check).** The deterministic event id makes the insert `caller_keyed`: a lost response is an ordinary retryable error, and the retry's `events.get` by that id (or the insert's 409) returns the existing event with `deduplicated: true`. | `calendar/tools.py` |
| Calendar `create_event` without key, quickAdd | Guidance: `calendar_search_events` in the window; pass `idempotency_key` next time. | `operations.verification_steps` |
| Gmail drafts.send / drafts.create, labels/filters/forwarding creates | Guidance: `gmail_search_emails` `in:sent`, `gmail_list_drafts`, `gmail_list_labels`, `gmail_list_filters`, `gmail_list_forwarding_addresses`. | 〃 |
| Chat posts (`create_message`, `post_message_simple`, `reply_to_message`) | Guidance: `chat_list_messages` in the space. Passing `request_id` to `chat_create_message` and reusing it makes the retry safe (Chat returns the existing message). | 〃 |
| Drive create/upload/copy | Guidance: `drive_list_files` ordered by `createdTime desc`. | 〃 |
| Drive `permissions.create` | Guidance: `drive_list_permissions` for the file. | 〃 |
| Sheets `batch_update_spreadsheet`, `append_sheet_values`, `create_spreadsheet` | Guidance: `sheets_get_spreadsheet` for the spreadsheet id (or `drive_list_files` for a create). | 〃 |
| Docs / Slides / Forms `batch_update_*` and creates | Guidance: `docs_get_document`, `slides_get_presentation`, `forms_get_form`. | 〃 |
| Tasks / People / Keep / Meet creates | Guidance: `tasks_list_tasks` / `tasks_list_tasklists`, `people_list_contacts` / `people_list_contact_groups`, `keep_list_notes`; otherwise "inspect the target". | 〃 |

## 9. Tests (W4b)

`tests/test_operation_records.py` (memory **and** Redis/burner-redis backends):
lost response after provider success → saved result; duplicate commit;
simultaneous claims (8 contenders, one winner) and simultaneous commits over
the full composition (`operation_in_progress`, one send); deadline while the
send thread executes → `outcome_unknown` with the Message-ID query; a timed-out
confirmed send is never re-executed; crash between claim and completion →
`operation_in_progress` during the lease, `outcome_unknown` after it, late
completion still recorded; late provider call refused; saved-result replay
across two server instances sharing the store (continuation and commit);
changed payload rejected; decline/accept cannot be flipped either way;
expiry of continuations, commit tokens and records; one TTL for tokens and
questions; records hold no continuation, id, prompt or argument values (Redis
values encrypted); Gmail Message-ID reconciliation (not found → still unknown,
found → resolved); Calendar idempotent create after a lost insert response;
Calendar create without key → `outcome_unknown`; transport retry budget and
`RetryingHttpRequest`; the repeat-safety table and source scan; tracker
verdicts; result minimization and non-retained results.
