# MCP Google Workspace — Whole-Codebase Review

**Review date:** 2026-07-12  
**Branch:** `codex/standard-file-picker`  
**Reviewed commit:** `a9002b545b054e6faa30e2ebbf8a9d5cc8d99c8d`  
**Scope:** correctness, security, latency, operational readiness, MCP contracts, MCP Apps, and model/agent ergonomics

**Remediation status:** implemented and validated in the current working tree on 2026-07-12. The detailed finding sections below preserve the original baseline evidence; this status block and the validation table describe the current implementation.

## Executive verdict

The complete remediation sequence has been implemented. Approved commits now preserve the full middleware chain; remote OAuth credentials, PKCE state and refresh locks have a Redis backend; upload contracts validate; object storage uses staged finalization, compensation and tombstones; identity-derived runtime state is bounded; file listing is linear and paginated; and FastMCP BM25 Tool Search reduces the normal model-visible catalog from 137 tools to 15.

Claude is an intentional compatibility exception: `MCP_CLIENT_MODEL=claude` disables Tool Search in auto mode, and the official MCPB manifest sets it. The Apps renderer is self-contained, the exact bundled stdio harness executes the callback address emitted by the Prefab view, and `get_mcp_apps_diagnostics(run_self_test=true)` verifies store/delete callbacks. A final interactive Claude Desktop acceptance test is still host-dependent and cannot be proven by the repository suite.

The repository is now a **production release candidate**. Promotion should still require live deployment acceptance against the target Redis/S3/OIDC environment and the target Claude Desktop build.

### Current ratings

| Area | Rating | Summary |
|---|---:|---|
| Correctness | 9.1/10 | Commit, schema, pagination and upload lifecycle invariants are implemented and failure-tested. |
| Security | 9.0/10 | Distributed encrypted OAuth state, full commit admission, clean dependency audits and Bandit gate. |
| Latency/scale | 8.9/10 | 15-tool progressive catalog, bounded principal state and linear paginated upload listing. |
| Agent ergonomics | 8.9/10 | Dynamic discovery, compact nucleus, Apps diagnostics and actionable schemas; live host behavior still varies. |
| Operational readiness | 8.8/10 | Strong CI/release gates and readiness checks; real target-infrastructure acceptance remains deployment work. |
| Overall | **8.9/10** | Production release candidate pending live Redis/S3/OIDC and Claude Desktop acceptance. |

### Remediation matrix

| Baseline finding | Current implementation |
|---|---|
| Commit bypassed middleware | **Resolved:** nested dispatch uses the normal middleware chain; `COMMIT_ACTIVE` bypasses only the prepare gate. |
| OAuth state was process-local | **Resolved:** encrypted Redis credentials/state, atomic consume, bounded attempts, CAS deletion and distributed refresh lock; readiness requires Redis for multiple workers. |
| Remote upload schema drift | **Resolved:** quota metadata is declared and representative real results are validated against Draft 2020-12 schemas. |
| Apps bridge lacked coverage/diagnostics | **Resolved in server scope:** bundled renderer, emitted-action stdio test, hidden callback self-test and diagnostic metadata. Live Claude acceptance remains external. |
| Upload objects could be orphaned | **Resolved:** quota reservation precedes S3 writes, staged records are hidden, failures compensate, deletes tombstone, and local blobs reconcile. |
| Principal state/metrics were unbounded | **Resolved:** TTL/cap eviction, weak local credential locks and backend-only aggregate upload counters. |
| Upload listing was O(n²) | **Resolved:** quota is computed once and `files_list_files_page` supports bounded cursor pagination. |
| Stdio exposed the complete catalog | **Resolved by default:** FastMCP BM25 Tool Search exposes 15 tools; Claude can opt out automatically or users can set `MCP_TOOL_SEARCH=off`. |
| Frontend validation was stale | **Resolved:** MCPB builds run `npm ci`; CI verifies the generated UI and audits npm. Lock now builds with Vite 6.4.3. |

## Baseline prioritized findings

### P1 — Committing an approved action bypasses all production and error middleware

**Location:** `src/mcp_google_workspace/server.py:333-344`

`commit_workspace_action` sets `COMMIT_ACTIVE`, then dispatches the approved tool with:

```python
await workspace_mcp.call_tool(tool_name, arguments, run_middleware=False)
```

This skips much more than the second approval check. It also bypasses:

- principal revocation;
- per-principal rate limits and global/per-principal concurrency limits;
- execution deadlines;
- structural input limits;
- production tracing and metrics;
- resource-handle resolution; and
- structured/recoverable tool-error conversion.

The token is consumed before dispatch, so a transient execution failure also cannot be retried with the same approval.

**Security consequence:** a principal can prepare an action, be revoked, and still commit during the approval token's lifetime because the commit path never re-runs revocation middleware.

**Recommended fix:** call the tool with middleware enabled and use the already-set `COMMIT_ACTIVE` context variable to bypass only the consequential-action gate. If FastMCP cannot safely re-enter the full chain, introduce an explicit narrowly scoped approval middleware bypass rather than disabling the entire chain. Decide separately whether approval tokens should be consumed before execution, after successful admission, or support a bounded retry state.

**Required tests:** revoked principal at commit time, rate-limited commit, oversized approved arguments, deadline enforcement during commit, structured error propagation, and exactly-once/retry behavior.

### P1 — Multi-replica OAuth state is not actually distributed

**Locations:** `src/mcp_google_workspace/auth/token_store.py`, `src/mcp_google_workspace/common/production.py:465-512`, `README.md:400`

OAuth credentials and PKCE authorization states are encrypted, which is good, but they are stored as local files. Coordination is a module-level `threading.Lock`, which only protects threads in one Python process.

The readiness contract for `MCP_WORKERS > 1` requires Redis, S3, and declared session affinity, but it does not require or verify a shared OAuth credential/state backend. This creates several multi-process and multi-replica failure modes:

- the browser callback may reach a replica that does not have the one-time state file;
- concurrent credential refresh/save operations can race across processes;
- shared filesystem deployments still lack a distributed lock or compare-and-swap primitive; and
- local ephemeral files disappear on replica replacement.

Session affinity is insufficient protection for the browser callback because it is not inherently the same MCP session request stream.

**Recommended fix:** add a Redis or database-backed credential and OAuth-state store with encrypted values, atomic one-time state consumption, expiry, and a distributed per-principal refresh lock/CAS. Make readiness fail for multiple workers unless that backend is configured and reachable. Keep the filesystem implementation for local stdio and single-instance development only.

### P1 — Remote upload responses violate the advertised output schema

**Locations:** `src/mcp_google_workspace/file_uploads.py:400-432`, `src/mcp_google_workspace/file_uploads.py:203-230`, `src/mcp_google_workspace/common/s3_uploads.py:120-130`

Both remote upload stores add `remaining_quota_bytes` to every returned file item. The `files_list_files` and `files_store_files` output schema has `additionalProperties: false`, but does not declare that field.

A direct Draft 2020-12 validation of a representative remote result fails with:

```text
Additional properties are not allowed ('remaining_quota_bytes' was unexpected)
```

Local/session uploads do not return this field, so the current in-process picker tests miss the remote-only contract failure.

**Recommended fix:** add a described non-negative integer `remaining_quota_bytes` property, or move quota into a stable top-level envelope such as `{files, quota}`. The latter is cleaner because the same account-level value should not be repeated on every item. Validate every tool result against its declared output schema in tests for both local and remote backends.

### P1 — The Claude Desktop MCP Apps path remains broken and is not covered end-to-end

**Locations:** `src/mcp_google_workspace/file_uploads.py:350-590`, `tests/test_file_uploads.py:29-72`, `tests/test_bundle_runtime.py:142-170`

The current tests establish useful but incomplete facts:

- `files_file_manager` carries a `ui://` resource URI;
- the resource has `text/html;profile=mcp-app`;
- the declarative Prefab response is returned; and
- FastMCP's own `Client` can directly invoke the hidden hashed `store_files` tool.

They do not exercise a host sandbox reading the resource, rendering it, receiving a user-selected file, and calling the backend through the host's MCP Apps bridge. Claude Desktop currently reports a globally qualified file-manager tool as not found. In the supplied log archive, neither that name nor the generated backend name appears in the Google Workspace MCP server log, so the failed lookup is occurring in Claude's host/router before it reaches this server.

The uncommitted manifest setting `PREFAB_BUNDLED_RENDERER=1` is recognized by the installed Prefab source and correctly makes the renderer self-contained. It removes CDN/CSP fragility, but it does not address host tool-name routing.

**Recommended fix:** build a protocol harness that behaves like an MCP Apps host, not just a direct FastMCP client. It should:

1. list tools and resources over the exact bundled stdio command;
2. resolve `ui/resourceUri`;
3. load and inspect the bundled app HTML;
4. execute the picker action name emitted in the returned view through `tools/call`;
5. verify an uploaded file can then be consumed by Gmail/Drive; and
6. capture both client-to-server JSON-RPC and app-bridge diagnostics.

Also add explicit startup logging for registered model-visible tools, hidden app-callable tools, resource URIs, and generated action addresses. A host-side lookup failure is otherwise invisible in server logs.

### P2 — Upload storage can leak encrypted objects when metadata operations fail

**Locations:** `src/mcp_google_workspace/common/s3_uploads.py:55-118`, `src/mcp_google_workspace/common/s3_uploads.py:166-175`, `src/mcp_google_workspace/file_uploads.py:143-201`

The S3 backend writes objects before the watched Redis transaction commits. A `WatchError`, quota change, Redis failure, or partial S3 failure can leave encrypted objects without metadata. Retrying the loop may upload again, and a later quota rejection does not clean up the earlier object. Deletion removes Redis metadata before deleting S3; an S3 error then leaves an untracked object.

The local encrypted backend has the same invariant in smaller form: it writes the encrypted blob before inserting SQLite metadata, so a database failure can orphan the blob.

Encryption limits confidentiality impact, but orphaned objects still violate retention, deletion, cost, and compliance expectations.

**Recommended fix:** use staged object keys plus atomic metadata finalization, compensate every failed path, and add a periodic reconciler. For deletes, use a tombstone/outbox so object deletion can be retried without losing the reference. Test injected Redis conflicts, SQLite failures, partial S3 failures, and delete failures.

### P2 — Per-principal admission state and Prometheus series are unbounded

**Location:** `src/mcp_google_workspace/common/production.py:123-128`, `src/mcp_google_workspace/common/production.py:239-290`

`ProductionControlMiddleware` keeps a rate window and semaphore for every principal ever observed. Neither dictionary evicts idle entries. The live-upload gauge is also labeled by `principal_hash`; Prometheus client children remain allocated even when a user's uploads expire or are deleted.

Large tenant counts, token churn, or hostile identities can therefore grow process memory and metrics cardinality indefinitely.

**Recommended fix:** use a bounded TTL/LRU structure and remove entries only when idle/no waiters. Remove zero-value gauge children, or preferably publish aggregate upload bytes/counts without a principal label. Keep per-principal details in bounded logs/traces, not metric dimensions.

### P2 — Local upload listing is quadratic and unpaginated

**Location:** `src/mcp_google_workspace/file_uploads.py:203-230`

The list comprehension recomputes the total uploaded bytes for every row:

```python
self.quota_bytes - sum(int(row[3]) for row in rows)
```

This makes listing `n` files O(n²). The same repeated quota value also bloats the response, and the tool has no pagination or response limit.

**Recommended fix:** calculate `used_bytes` once, return quota once in a top-level envelope, and add cursor/limit pagination. This also produces a smaller, clearer agent response.

### P2 — The default stdio catalog is too large for a model-facing surface

**Locations:** `src/mcp_google_workspace/server.py`, `src/mcp_google_workspace/server_http.py`

The default catalog currently exposes **135 tools**. Serialized tool definitions are approximately **329 KB**, including approximately **98 KB** of input schemas. HTTP already has BM25 progressive discovery, but local stdio/MCPB exposes the full catalog.

This increases connection latency, context consumption, selection ambiguity, and host registry pressure. It also makes tool-list churn more likely to manifest as hard-to-diagnose “tool not found” behavior in desktop clients.

**Recommended fix:** use progressive discovery for stdio/MCPB too. Since there are no legacy callers to preserve, keep only a compact stable nucleus visible by default:

- capabilities and tool search;
- authentication/status;
- prepare/commit;
- resource discovery;
- file picker/upload lifecycle; and
- a small set of high-value workflow tools.

Expose service CRUD tools dynamically by task or namespace. Keep hidden MCP Apps callback tools callable without making them model-visible. Measure catalog bytes and visible-tool count in CI and enforce a budget.

### P3 — Frontend validation used a stale local Vite installation

**Locations:** `src/mcp_google_workspace/apps/web/package.json`, `src/mcp_google_workspace/apps/web/package-lock.json`, local `node_modules`

The package manifest and lock select Vite 6.4.2, but the current local `node_modules` executed Vite 6.4.1. TypeScript and the build passed, but this means the validation did not use the exact locked frontend environment.

**Recommended fix:** use `npm ci` in CI/build packaging and assert the installed version before compiling. This is validation drift, not evidence of a packaged runtime vulnerability.

## Security assessment

### What is good

- OAuth credentials and pending authorization state are encrypted at rest.
- PKCE state is principal-bound, time-limited, one-time, and bounded per principal.
- Remote authentication is issuer-based rather than accepting arbitrary bearer strings.
- Consequential action preparation binds tool name and canonical arguments to an expiring token.
- Input structures are closed and bounded in many high-risk paths.
- Remote tools reject server-local file paths.
- Uploads are encrypted, MIME-sniffed, size/quota bounded, and can require malware scanning.
- HTTP includes deadlines, rate limits, concurrency control, revocation, draining, readiness, and structured metrics.
- Locked production dependencies have no known vulnerabilities according to the completed audit.

### Remaining security priorities

1. Preserve middleware guarantees during approved commit.
2. Make token/state coordination genuinely distributed.
3. Add adversarial tests at middleware re-entry and backend-failure boundaries.
4. Bound identity-derived in-memory state and metric cardinality.
5. Treat host-level Apps interoperability and diagnostics as a security boundary, because selected file bytes cross it.

## Latency and scalability assessment

The main Google API calls are naturally network-bound, so the most valuable latency improvements are reducing repeated calls and reducing model/tool negotiation overhead.

Recommended order:

1. Reduce the visible stdio catalog from 135 tools to a small discoverable nucleus.
2. Fix O(n²) upload listing and paginate it.
3. Return compact workflow envelopes instead of raw Google payloads where agents do not need every field.
4. Add conditional requests/sync tokens consistently for Gmail, Calendar, Drive and Tasks list/check workflows.
5. Instrument API-attempt count, payload bytes, response bytes, and cache hit/miss per workflow—not per principal.
6. Add latency budgets for catalog listing, first useful tool discovery, inbox/calendar summaries, and Apps startup.

## Making the MCP more model-pleasant

### 1. Progressive, task-aware tool discovery everywhere

Make the model start with a concise capability map and search by intent. Search results should include:

- exact tool name and human title;
- one-sentence “use when” guidance;
- read/write/consequential classification;
- required scopes and auth status;
- required identifiers and how to obtain them;
- approximate cost/latency class;
- whether the tool supports pagination, sync tokens, batching, or idempotency; and
- likely next actions.

Return only the top few matches, with a continuation mechanism.

### 2. Prefer workflow tools over API-shaped tool chains

High-value examples:

- `gmail_triage_inbox` returning compact thread summaries and suggested next actions;
- `gmail_get_conversation` combining thread metadata, selected bodies, and attachment handles;
- `calendar_plan_meeting` combining participant free/busy, working hours, timezone normalization, and candidate slots;
- `calendar_reschedule_event` combining lookup, conflict check, update, and refreshed result;
- `drive_find_and_summarize` combining search, export/read capability, and bounded content; and
- `workspace_attach_and_send` accepting upload handles, validating them, preparing the email, and returning a confirmation preview.

Keep raw CRUD available through discovery for unusual tasks.

### 3. Use consistent compact envelopes

List/search/check tools should converge on a shared shape such as:

```json
{
  "items": [],
  "count": 0,
  "next_cursor": null,
  "freshness": {"checked_at": "...", "source": "google"},
  "warnings": [],
  "next_actions": []
}
```

Avoid repeating account-level metadata on every item. Prefer display names plus opaque handles over making the model shuttle raw IDs. Give every recoverable failure a stable code and a directly callable `required_action`.

### 4. Make tool schemas executable documentation

- Describe every property, including nested attachment/file objects.
- Use discriminated unions for local path vs uploaded handle only where the transport permits each option.
- Validate actual outputs against output schemas in CI.
- Generate README tool tables from the live catalog to prevent drift.
- Include small realistic examples for complex inputs, especially recurrence, attendees, attachments, and Drive upload/update modes.

### 5. Make Apps observable as a distinct subsystem

Expose a read-only diagnostic tool/resource that reports:

- whether Apps support is enabled;
- renderer mode and resource MIME type;
- model-visible app tools;
- hidden app callback addresses;
- upload backend and limits; and
- a safe self-test result that stores and deletes a zero-byte/fixture upload.

This gives both models and humans an actionable answer instead of a generic host “tool not found” message.

## Validation performed

| Check | Result |
|---|---|
| Python tests | **169 passed** in 26.81s; only the existing pytest cache ACL warning |
| Ruff | **Passed** |
| mypy | **Passed** across 129 source files |
| TypeScript `--noEmit` | **Passed** |
| Vite production build | **Passed** with locked Vite 6.4.3 |
| Locked production dependency audit | **No known vulnerabilities found** |
| npm audit | **0 vulnerabilities** after lock remediation |
| Bandit | **Passed**, with narrow documented false-positive suppressions |
| JSON Schema validation of representative remote upload result | **Passed** |
| Complete raw catalog | **137 tools**, 339,598 serialized bytes |
| Input-schema share of raw catalog | 105,334 bytes |
| Progressive catalog | **15 tools** including `search_tools` and `call_tool` |
| Claude auto-mode catalog | **137 tools**, intentional compatibility opt-out |
| Claude Desktop log inspection | No picker/backend `tools/call` or matching server error found |

The scanners ran from workspace-local uv tool/cache directories to avoid the machine's restricted global uv paths. The exact exported production lock was audited with pip installation disabled.

## Recommended remediation sequence

1. **Done — Commit safely:** full middleware re-entry plus commit-time revocation coverage.
2. **Done — Fix remote contracts:** declared quota fields and real-result schema validation.
3. **Done — Make OAuth distributed:** Redis token/state backend, atomic consume/CAS and distributed refresh lock.
4. **Done in repository scope — Apps interoperability:** emitted-action stdio harness, bundled renderer and self-test diagnostics. Interactive Claude acceptance remains a release-environment check.
5. **Done — Fix storage invariants:** staged S3 finalization, compensation, tombstones and local reconciliation with failure injection.
6. **Done — Bound runtime state:** TTL/cap admission eviction, weak credential locks and low-cardinality upload metrics.
7. **Done — Shrink the model surface:** FastMCP BM25 Tool Search for HTTP and stdio with explicit/Claude opt-out.
8. **Done — Close latency papercuts:** O(n) quota calculation and bounded upload pagination.
9. **Done — Harden reproducibility:** locked UI rebuild during packaging, CI, Python/npm audits and Bandit.

## Release gate

All repository-controlled P1 gates are implemented and automated. Before production promotion, run two environment-dependent acceptance checks: (1) a real multi-replica deployment using the target Redis, S3-compatible store, OIDC issuer and callback routing; and (2) install the rebuilt MCPB in the target Claude Desktop version, run `get_mcp_apps_diagnostics(run_self_test=true)`, then upload and consume a real file through the rendered picker.
