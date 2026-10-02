# Changelog

All notable changes to `mcp-google-workspace` are recorded here. Dates are
UTC. Entries trace to `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` (the
migration plan; sections cited below are `9.x` implementation records unless
noted) and its `docs/migration/W*_CATALOG_DIFF.md` contract diffs.

## 1.0.0 — 2026-09-27

The first stable release. This version migrates the server from FastMCP
3.4.4 / MCP SDK 1.28.1 (protocol `2025-11-25`) to **FastMCP 4.0.10 / MCP SDK
2.2.0**, adds full support for **MCP 2026-07-28** while keeping legacy
protocol connectivity (`2024-11-05` through `2025-11-25`), and upgrades the
MCP Apps dashboard/picker to **Apps SDK 2.0.3**. See
`docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` for the full plan and
`docs/migration/W0_BASELINE.md` for the pre-migration baseline
(`fba6253`, application `0.3.13`).

### Breaking changes

- **MCPB no longer asks for a token encryption key.** The
  `token_encryption_key` setting is removed from the bundle manifest. The
  local stdio runtime now generates its key on first use and keeps it in the
  OS keychain (see Security). Google connections saved with a previously
  pasted key cannot be decrypted with the new one: reconnect Google once
  after upgrading. Headless setups without a secure keychain set
  `MCP_TOKEN_ENCRYPTION_KEY` in the environment instead.
- **Removed tools.** `chat_summarize_space_messages` and `keep_summarize_note`
  are deleted, not stubbed. Both depended on FastMCP's `ctx.sample()`
  (Sampling), which the 2026-07-28 spec deprecates and this project does not
  replace with a server-side summarization provider. The Chat/Keep read
  tools and the user-invoked summary *prompts*
  (`chat_summarize_chat_thread_prompt`, `keep_summarize_keep_note_prompt`)
  are unchanged. (§9.1, §9.2, `docs/migration/W2_CATALOG_DIFF.md` §2.)
- **Removed `session_id` everywhere.** The MCP Apps dashboard no longer
  accepts a caller-chosen `session_id`; every dashboard tool
  (`apps_get_dashboard`, `apps_get_weekly_calendar_view`, `apps_get_state`,
  `apps_set_state`, `apps_patch_state`, `apps_next_range`, `apps_prev_range`,
  `apps_today`, `apps_get_event_detail`, `apps_get_email_detail`,
  `apps_get_email_attachment`) instead uses a server-issued `view_handle`
  (`wsv_` + 256 random bits), required on the six state-mutating tools and
  optional (validated when supplied) elsewhere. Old dashboard state,
  transport-session fallback, and the browser `localStorage`/`Math.random()`
  view id are not migrated — there is no application-level backward
  compatibility (owner decision, §9.1). (§9.3, `docs/migration/W3_CATALOG_DIFF.md` §1.)
- **Removed URI aliases.** The flat `ui/resourceUri` tool metadata key, the
  legacy `ui://dashboard-ui` root registration, the double-mounted
  `ui://apps/apps/dashboard-ui` resource, and the `apps://apps/dashboard/ui`
  text-only copy are all removed. Only the canonical nested
  `_meta.ui.resourceUri` (`ui://dashboard-ui` on a direct subserver client,
  `ui://apps/dashboard-ui` composed at the root) remains. (§9.1, §9.7,
  `docs/migration/W6_CATALOG_DIFF.md` §1.)
- **Removed parameter.** `gmail_delete_thread.force` is deleted (it was
  accepted and silently ignored — thread deletion was always confirmed
  interactively). The input schema is closed
  (`additionalProperties: false`), so a client still sending `force` now
  gets an input-validation error instead of having it dropped.
  (`docs/migration/W4B_CATALOG_DIFF.md` §4.)
- **Closed schemas.** The picker's upload callback
  (`files_store_files`) now publishes a closed, bounded input schema for the
  exact Prefab `DropZone` payload (name ≤1024 chars, size 0..25 MiB, type
  ≤255 chars, base64 `data` bounded to the file-size limit,
  `additionalProperties: false`) instead of an open `list[dict]`. The six
  dashboard state tools publish a closed `{state, view}` output object
  (previously FastMCP's default `additionalProperties: true`). Several
  Google Meet space tools (`meet_create_space`, `meet_get_space`,
  `meet_update_space`, `meet_end_active_conference`) go the other way: a
  latent output-schema-inference bug had published a *closed* schema
  containing only error fields, which every successful response violated;
  they now publish FastMCP's open object schema until a typed Meet response
  model lands. (`docs/migration/W2_CATALOG_DIFF.md` §1,
  `docs/migration/W3_CATALOG_DIFF.md` §1, `docs/migration/W5_CATALOG_DIFF.md` §2.)
- **Visibility changes.** `files_list_files`, `files_list_files_page`, and
  `files_read_file` are now model-only (previously model + app);
  `apps_get_dashboard`/`apps_get_weekly_calendar_view` are explicitly
  `["model", "app"]`; the six dashboard state tools and
  `apps_get_event_detail`/`apps_get_email_detail` are app-only. Visibility is
  a host hint for `tools/list` filtering, not authorization — every call is
  still authorized server-side by principal-bound view handle, Google grant,
  and confirmation gates. (`docs/migration/W6_CATALOG_DIFF.md` §2.)
- **Error-shape changes.**
  - A Google API failure, argument-validation failure, or business-rule
    rejection is now consistently an `isError: true` **tool result** carrying
    the shared error envelope (`code`, `message`, `retryable`, `retry_after`,
    `required_action`, `provider_status`, `field_errors`, optional
    `details`) — never a JSON-RPC protocol error. 61 tool bodies that used to
    *return* `{"error": ..., "provider_status": ..., "context": ...}` as a
    **successful** result now raise a structured error instead, so their
    output schemas no longer publish `error`/`provider_status`/`context`;
    the error context moved to `details.context`.
    (`docs/migration/W5_CATALOG_DIFF.md` §1.)
  - `McpError(ErrorData(...))`'s positional constructor (a `TypeError` under
    SDK 2) is replaced with the supported keyword form.
  - The custom `-32029` rate-limit code, which collides with the
    2026-07-28 spec's newly reserved error-code range, is retired. Named
    JSON-RPC codes now live in `-32000..-32019`
    (`common/errors.py`): `-32005` rate limited, `-32006`
    draining/unavailable, `-32007` unauthorized, `-32008` authorization
    state unavailable. (§3.5 of the plan, §9.6.)
  - `commit_workspace_action` returns `resultType: input_required` (not a
    `status: committed` wrapper) when the bound action still needs
    confirmation, and passes through a nested `isError` result unwrapped
    instead of relabeling it `committed`. (`docs/migration/W4_CATALOG_DIFF.md` §2.)
  - `refresh_workspace_catalog`'s `notification_sent` field changed type
    from a string (`"tools/list_changed"`) to a boolean (now always
    `false`, since the server sends no such notification and grants are
    re-evaluated on every request instead). (`docs/migration/W5_CATALOG_DIFF.md` §3.)
- **Confirmation flow changes.** Destructive/consequential tools on MCP
  2026-07-28 now return `resultType: input_required` **before any mutation**
  and resume on a retry carrying the answer and a sealed continuation
  (`requestState`), instead of the legacy imperative `ctx.elicit()` flow.
  Legacy (pre-2026-07-28) clients keep the `ctx.elicit()` branch through the
  same policy adapter. A modern host that declares no elicitation capability
  gets a fail-closed `confirmation_required` result — never an automatic
  mutation. `commit_workspace_action`'s prepared-token semantics changed: a
  repeated commit for a `succeeded` operation now replays the saved result
  instead of failing, and its token lifetime changed from a 300s
  token / 600s question split to one `MCP_CONFIRMATION_TTL_SECONDS` (default
  600s) covering both. The `ApprovalStore.consume()`-before-execution design
  is replaced by durable operation records
  (`prepared -> awaiting_input -> executing -> succeeded | failed |
  outcome_unknown`); old approval/replay tokens are not migrated. Bypass
  flags (`confirm_send`, `notify`, `confirm_create`, `confirm_delete`,
  `force`, `delete_mode`, `permanent`) keep their existing defaults and
  behavior — only stale descriptions were corrected (owner decision, §9.1).
  (§3.3 of the plan, §9.4, §9.5, `docs/migration/W4_CONFIRMATION_POLICY.md`.)
- **Removed deprecated-protocol dependencies.** No application code depends
  on Sampling (`ctx.sample`), Roots (`ctx.list_roots`), client-facing Logging
  (`ctx.info/debug/warning/...` now go to server logs at `DEBUG`; progress
  notifications are unaffected), or dynamic client registration. The Apps
  `ui/initialize` handshake is retained — it is a separate, non-deprecated
  protocol. (Owner decision §9.1, §9.2.)
- **Python attribute reads.** Internal SDK model reads moved from SDK-1-style
  camelCase attributes to SDK 2 snake_case
  (`tool.input_schema`, `resource.mime_type`, `result.is_error`,
  `result.structured_content`, ...); wire JSON and Google API payload keys
  remain camelCase. CI now runs with `FASTMCP_MCP_CAMELCASE_COMPAT=false`, so
  a regression fails loudly instead of silently working through FastMCP's
  compatibility shim. (§3.5 of the plan, §9.2.)

### New features

- **MCP 2026-07-28 support**, including `server/discover`, first-call
  `tools/list`/`tools/call` with no `initialize` handshake, `resultType` on
  every tool/list result, request-scoped SSE for progress and long-running
  calls, and multi-round-trip confirmations (MRTR). Legacy protocol
  connectivity (`2024-11-05`..`2025-11-25`) is kept through FastMCP's
  built-in compatibility path. (`docs/migration/W0_BASELINE.md`,
  `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` §3.1–§3.2.)
- **MCP Tasks extension** (`io.modelcontextprotocol/tasks`, replacing the
  removed core experimental tasks and `tasks/result`/`tasks/list`): task
  tools now negotiate through `tasks/get`/`tasks/update`, support answering a
  parked confirmation via `tasks/update`, and restore the caller's identity
  (bearer token, principal revocation, current Google grant) at execution
  time on every worker, not only at submission. (§3.4 of the plan, §9.6.)
- **Durable, replay-safe mutations.** A repeated commit or confirmed
  continuation for an operation that already `succeeded` returns the saved
  (size-capped) result instead of re-executing; simultaneous claims execute
  exactly one and return `operation_in_progress` for the rest; a call whose
  outcome could not be determined (timeout, disconnect, crash) returns
  `outcome_unknown` with `required_action.verify` steps instead of being
  retried automatically. Calendar event creation and Chat message creation
  use provider-native idempotency keys; Gmail send/reply reconciles an
  unknown outcome via the RFC 822 `Message-ID` it always sets. (§9.5,
  `docs/migration/W4_CONFIRMATION_POLICY.md` §6–§8.)
- **Operation manifest.** Dashboard launch results now carry
  `_meta["mcp-google-workspace/operations"]`, listing exactly the write
  operations available to the current view given registered tools, app
  visibility, and the principal's granted Google capabilities — the
  dashboard no longer guesses which mutating actions are safe to offer.
  (§9.7, `docs/RICH_OUTPUTS.md`.)
- **Cached Prefab picker resource.** The picker's rendered HTML/CSP
  (previously rebuilt from scratch, including a fresh disk read of the ~6.6 MB
  bundled renderer, on every `resources/list`/`resources/read`) is now built
  once per process and reused, keyed by tool identity, the installed
  `prefab-ui` version, the resolved renderer mode/`PREFAB_RENDERER_URL`, and
  the tool's CSP/permissions metadata (`common/prefab_render_cache.py`).
  Measured 8.8x faster listings (41.1ms -> 4.7ms per call, one build plus
  cache hits thereafter) in `tests/test_prefab_render_cache.py`.
- **Golden wire-contract fixtures** (`tests/wire/`) pin the exact JSON-RPC
  shape of modern discovery/listing/call/error/MRTR exchanges and the legacy
  initialize/list/call/confirm sequence, over both raw HTTP and the in-memory
  transport that stands in for stdio.
- **Feature-flag matrix smoke test** exercises startup, listing, and one
  safe call for every optional integration (Apps, Keep, Chat, Meet, Gemini)
  independently and together, through both the root composition and a
  direct client to each namespace's own subserver
  (`tests/test_feature_flag_matrix.py`).

### Security fixes

- **Local token encryption key lives in the OS keychain.** The stdio/MCPB
  runtime stores its Fernet key in Windows Credential Manager, macOS Keychain
  or the Linux Secret Service via `keyring`, instead of a value the user
  generated and pasted into (unencrypted) extension settings. Insecure
  `keyring` backends (fail/null/`keyrings.alt` files) are refused; the key
  is never written to disk. HTTP deployments keep operator-managed key rings.
  The MCPB `gemini_api_key` setting is now marked `sensitive`, so hosts store
  it in the OS keychain too.
- **Picker uploads over ~750 KB were rejected.** The generic 1,000,000-character
  argument limit applied to the picker's base64 file field. String limits now
  honor a larger `maxLength` the called tool declares (only the upload field),
  and rejections name the argument path. The default `MCP_MAX_REQUEST_BYTES`
  rises from 30 to 36 MiB so one 25 MiB upload fits after base64 encoding.
- **Dashboard rendering (W1).** `render.ts` used `textContent`-derived values
  interpolated into HTML attributes/URLs without attribute-safe escaping, so
  a crafted filename/subject/title (e.g. containing `" data-audit-injected="yes`)
  could inject a new DOM attribute. Values are now set through DOM
  APIs/attribute setters rather than string interpolation, URL schemes are
  validated separately, and the custom email-HTML sanitizer is hardened
  against event handlers, active embedded content, external tracking
  requests, and CSS escapes. Adversarial filename/title/URL/encoded-quote/
  malformed-HTML fixtures are covered in the browser test suite. (Plan §4.2,
  status table row "W1".)
- **Origin enforcement was silently disabled.** FastMCP 4.0.10 defaults
  `host_origin_protection` to *off*, so this project's existing
  `allowed_hosts`/`allowed_origins` configuration was not actually being
  enforced. `server_http.HttpServingPolicy` now sets
  `host_origin_protection=True` explicitly. (§9.6.)
- **JWT hardening.** Incoming MCP bearer tokens are verified through a
  `RemoteAuthProvider` wrapping the JWT verifier, which now serves real MCP
  protected-resource discovery metadata and challenge routes; `exp`/`iss`/
  `sub` are mandatory, a future `nbf`/`iat` is refused, and an unknown JWKS
  `kid` triggers at most one refetch per 30 seconds. MCP bearer tokens are
  never reused as Google credentials (tested). (§9.6.)

### Operations

- **New environment variables** (see `.env.example` for full descriptions
  and defaults): `MCP_REQUEST_STATE_KEYS` (confirmation-continuation sealing
  key ring), `MCP_CONFIRMATION_TTL_SECONDS`, `MCP_OPERATION_RETENTION_SECONDS`,
  `MCP_OPERATION_LEASE_SECONDS`, `MCP_OPERATION_RESULT_MAX_BYTES`,
  `MCP_APP_VIEW_TTL_SECONDS`, `MCP_UPLOAD_TTL_SECONDS`,
  `MCP_UPLOAD_QUOTA_BYTES`, `FASTMCP_TASKS_ENCRYPTION_KEY`,
  `FASTMCP_DOCKET_URL`/`FASTMCP_DOCKET_NAME`/`FASTMCP_DOCKET_CONCURRENCY`,
  `MCP_WORKERS`, `MCP_REPLICAS`, `MCP_SESSION_AFFINITY`,
  `MCP_HTTP_RESPONSE_MODE`, `MCP_ADMISSION_BACKEND`,
  `MCP_PRINCIPAL_QUEUE_SECONDS`, `MCP_TOOL_DEADLINE_SECONDS`,
  `MCP_EXPENSIVE_DEADLINE_SECONDS`. `fastmcp.settings.docket.url` (FastMCP 3)
  is gone; queue configuration now goes through the `TasksExtension`
  (`FASTMCP_DOCKET_*`) or `MCP_REDIS_URL`. (§3.4 of the plan, §9.6.)
- **Readiness checks.** `/health/ready` declares a fleet when `MCP_WORKERS >
  1`, `MCP_REPLICAS > 1`, or `MCP_REDIS_URL` is set, and then requires shared
  app state, a shared snapshot-encrypted task queue, shared operation
  records, shared continuation keys, and Redis/S3-backed OAuth/upload
  storage. Modern (2026-07-28) traffic needs no session affinity — that
  requirement is removed from readiness; `MCP_SESSION_AFFINITY` is now an
  advisory `legacy_session_affinity` check for handshake-era clients pinned
  behind a load balancer. (§9.6.)
- **Redis is optional.** stdio and single-process HTTP deployments use
  in-memory backends for app state, uploads, operation records, and the task
  queue; nothing requires Redis. A multi-replica/multi-worker fleet requires
  Redis (app state, operation records, task queue, OAuth token storage) and
  S3-compatible storage for uploads at scale — `MCP_REDIS_URL` alone implies
  fleet mode. (Owner decision §9.1, §9.6, README "Run (Streamable HTTP)".)
- **New worker entrypoint.** `uv run mcp-google-workspace-worker` runs
  additional MCP Tasks workers against the same Redis-backed queue as the
  HTTP server, sharing `FASTMCP_TASKS_ENCRYPTION_KEY`. (README "Production
  operations".)

### Upgrade notes

- **Drain task queues before cutover.** Do not run the old (FastMCP 3 /
  pydocket) and new (FastMCP 4 / `fastmcp-tasks`) task queues side by side.
  Let in-flight FastMCP-3 tasks finish, then deploy 1.0.0; the new deployment
  starts against an empty queue.
- **Old application state is discarded, not migrated.** Dashboard sessions
  keyed by the old `session_id`/browser `localStorage` id, old approval/
  replay tokens, and the removed URI aliases are not readable by 1.0.0. Any
  open dashboard view or in-flight confirmation from before the upgrade must
  be restarted by the user after cutover (owner decision, §9.1; do not build
  a compatibility reader for old application state, plan §8/W8).
- **New required secrets for a fleet deployment** (single-process/stdio
  deployments need none of these): `MCP_REQUEST_STATE_KEYS` (confirmation
  continuations) and `FASTMCP_TASKS_ENCRYPTION_KEY` (task credential
  snapshots) must be set identically on every replica and worker before
  `/health/ready` will pass; generate and rotate them independently of
  `MCP_TOKEN_ENCRYPTION_KEY`/`MCP_SECRET_FILE` (see the rotation procedure in
  `.env.example`).
- Encrypted Google grants and durable uploads/operation evidence from a
  previous release are retained across the upgrade (they are not part of the
  removed application-state surface above); reconnect Google accounts only
  if an actual token/provider migration requires it.
