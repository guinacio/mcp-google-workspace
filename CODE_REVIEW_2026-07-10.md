# Complete code review: correctness, security, latency, and agent usability

Date: 2026-07-10  
Baseline commit: `c54d18d1adc9fbc302eafed7864df461eab82881` (`main`)  
Current reviewed state: baseline plus the complete production-hardening implementation described below  
Scope: Python MCP server, OAuth/runtime, all Google service namespaces, MCP Apps UI, tests, manifests, and locked dependencies.

## Executive assessment

The codebase has a strong functional base: its architecture is service-oriented, its default catalog is well described, Google tokens are encrypted and separated by principal, OAuth state is one-time and PKCE-protected, and the automated Python checks are healthy.

The critical remote-filesystem issue found in the baseline has been remediated in the current working tree. Authenticated remote callers are now prevented from supplying host-local input/output paths, while trusted local/stdio callers retain backward compatibility. A Prefab/FastMCP MCP Apps picker stores remote uploads in encrypted principal-scoped shared storage and passes uploaded filenames to Gmail, Drive, and Gemini without routing binary bytes through model context. The FastMCP Apps upgrade also moved the exposed Starlette stack to a fixed major line.

The reviewed findings and the production-readiness backlog have been remediated in the current working tree. Remote service uses session-aware MCP Streamable HTTP, strict capability-aware catalogs, bounded admission, graceful draining, health/metrics endpoints, distributed Redis state, S3-compatible encrypted object storage, hardened uploads, versioned encryption keys, recoverable error actions, resource handles, prepare/commit safety, and native MCP tasks. Item 9 from the requested backlog—the model-behavior evaluation suite—was intentionally excluded by instruction.

Ratings for the current reviewed state:

| Area | Rating | Summary |
| --- | ---: | --- |
| Correctness | 9/10 | Strict recursive contracts, retries, distributed idempotency, credential generations, prepare/commit, and lifecycle behavior are explicit and tested. |
| Security (local stdio) | 9/10 | Encrypted isolation, PKCE, incremental scopes, grant revocation, strict inputs, and mandatory destructive confirmation are in place. |
| Security (remote HTTP) | 9/10 | Host paths are hidden and rejected, uploads are scanned and principal scoped, capabilities filter the catalog, and revocation fails closed. |
| Latency / concurrency | 9/10 | Blocking provider work is offloaded; admission, deadlines, circuit breaking, native tasks, and provider-attempt metrics bound tail risk. |
| Agent friendliness | 9.5/10 | Picker, opaque handles, progressive discovery, typed schemas, recovery actions, prepare/commit, resource URIs, and tasks are implemented. |

## Findings summary

| ID | Severity | Area | Finding |
| --- | --- | --- | --- |
| C-01 | Critical | Security | **Remediated:** remote host paths are rejected; MCP Apps uploads use encrypted principal-scoped shared storage. |
| H-01 | High | Security | **Remediated:** framework and all audited vulnerable transitive packages upgraded; fresh audit reports no known vulnerabilities. |
| H-02 | High | Latency / availability | **Remediated:** lazy clients and synchronous tools execute credential, discovery, construction, and API work in worker threads. |
| H-03 | High | Correctness / reliability | **Remediated:** every `HttpRequest.execute()` applies `MCP_GOOGLE_HTTP_RETRIES`. |
| H-04 | High | Agent safety | **Remediated:** destructive tools require host-mediated confirmation and filesystem writers have mutating annotations. |
| M-01 | Medium | Availability / latency | **Remediated:** Drive/Calendar/Gemini media streams with configurable byte limits; App-only Gmail downloads enforce the same limit. |
| M-02 | Medium | Correctness | **Remediated:** SQLite claims are atomic, durable, request-hashed, expiring, and safe across workers; state caches are bounded. |
| M-03 | Medium | Correctness | **Remediated:** refreshes are principal-serialized and stale failures can only delete their matching credential generation. |
| M-04 | Medium | Security / UX | **Remediated:** OAuth grants selected capabilities incrementally; clients are built with service scopes; disconnect revokes the grant. |
| M-05 | Medium | Correctness | **Remediated:** month navigation and windows use calendar-month boundaries. |
| L-01 | Low | Correctness / UX | **Remediated:** status reports structured connection state, expiry, scopes, capabilities, and required action. |
| L-02 | Low | Security / availability | **Remediated:** expired/corrupt state is pruned and each principal is limited to ten outstanding attempts. |

## Detailed findings

### C-01 — Remote tools could read and overwrite arbitrary host files — remediated

**Baseline evidence**

- Drive upload and content update expose raw `local_path` parameters (`src/mcp_google_workspace/drive/schemas.py:64-75`, `90-97`) and pass them to `MediaFileUpload` without a containment policy (`src/mcp_google_workspace/drive/tools/files.py:259-315`).
- Gmail attachments accept local paths and call `Path.read_bytes()` (`src/mcp_google_workspace/gmail/mime_utils.py:46-52`; caller at `gmail/tools/messages.py:30-89`).
- Gemini accepts arbitrary `input_path`, resolves it, and reads it (`src/mcp_google_workspace/gemini/tools.py:54-63`, `105-120`). Its caller can also override the output directory (`gemini/schemas.py:47-69`; `gemini/storage.py:51-70`).
- Drive and Calendar downloads create arbitrary parent directories and write caller-selected output paths (`drive/tools/files.py:583-664`; `calendar/tools.py:906-998`).

**Impact**

In local stdio mode, the MCP and the user normally share a trust boundary. In remote HTTP mode, they do not. Any authenticated tenant could otherwise use the server process's filesystem permissions to:

- exfiltrate readable config, source, credentials, service files, or Linux process data by uploading/attaching them to Google services;
- overwrite or create files anywhere writable by the service account;
- target another tenant's files or the MCP's own token/config directories;
- exploit symlink and check-then-write races around `exists()`, `mkdir()`, and later writes.

On Linux, reading a path such as `/proc/self/environ` could disclose environment secrets, including the token-encryption key, if the runtime permits it. This turns a single authenticated account into a possible cross-tenant compromise.

**Implemented remediation**

- `require_local_filesystem()` rejects caller-selected server paths whenever the request principal is authenticated remotely; local/stdio mode remains compatible.
- The guard is applied to Gmail attachment reads/download writes, Drive upload/update/download/export paths, Calendar attachment downloads, Gemini local inputs, and Gemini output-directory overrides.
- `WorkspaceFileUpload` provides a 25 MiB-per-file Prefab/FastMCP picker. Remote storage uses the hashed verified issuer/subject as its stable tenant key; local stdio storage remains session scoped.
- Gmail, Drive, and Gemini consume picker bytes server-side through `uploaded_file`; binary content does not enter model context or require a temporary path for Gmail/Drive.
- Tests cover principal isolation, session isolation, remote-path rejection, exactly-one-source validation, and byte-preserving Gmail MIME construction.

**Residual risk**

Authenticated remote uploads use opaque IDs, Redis metadata and quota transactions, and encrypted S3-compatible object blobs in production. Single-node mode retains an encrypted SQLite/blob backend, and local stdio remains session scoped. Generated/downloaded artifacts use App/resource paths or bounded local streaming rather than remote host paths.

### H-01 — Locked runtime dependencies had a large advisory backlog — remediated

The baseline production pins exported from `uv.lock` produced **41 pip-audit advisory rows across 13 packages**. The implementation upgrade changed FastMCP `3.0.2` to `3.4.4`, Starlette `0.52.1` to `1.3.1`, Authlib `1.6.8` to `1.7.2`, and python-multipart `0.0.22` to `0.0.32`, and added the `fastmcp[apps]`/Prefab runtime required by the picker.

The most directly relevant baseline item was Starlette CVE-2026-48710: versions before 1.0.1 do not validate `Host` before reconstructing `request.url`. Starlette 1.3.1 is beyond that fixed boundary. A fresh installed-environment audit was run after all upgrades and reported no known vulnerabilities.

FastMCP is now on 3.4.4, which also supplies MCP Apps and the tool-search facilities recommended later.

The baseline UI lock pinned Vite 6.4.1, affected by CVE-2026-39363 when the development server is exposed with WebSocket/HMR enabled. The lock now pins fixed Vite 6.4.2 with its published tarball integrity.

**Remediation**

- Keep FastMCP and its HTTP/auth dependency graph current; the immediate framework upgrade is complete.
- Raise direct lower bounds for packages the application relies on for security, especially `cryptography`.
- Add CI jobs for `pip-audit` against an exported locked production set and `npm audit` against `package-lock.json`.
- Triage every remaining advisory for actual code-path applicability and document temporary ignores with expiry dates.

References: [Starlette CVE-2026-48710](https://nvd.nist.gov/vuln/detail/CVE-2026-48710), [FastMCP CVE-2026-27124](https://nvd.nist.gov/vuln/detail/CVE-2026-27124), [Vite CVE-2026-39363](https://github.com/advisories/GHSA-p9ff-h696-f583).

### H-02 — Blocking Google work ran inside async tools — remediated

**Current implementation:** service factories now return `LazyGoogleRequest` plans. Credential loading/refresh, discovery construction, request construction, and execution materialize in worker threads. Pure synchronous namespace tools are registered as synchronous FastMCP tools (and therefore use FastMCP's thread runner); mixed async tools explicitly offload their blocking payload units.

The Google discovery clients, credential refresh, and `httplib2` transport are synchronous. Several tool families invoke synchronous payload functions directly from `async def` functions. For example, Docs calls `.execute()` synchronously (`docs/tools.py:34-75`) and its async wrappers call those functions directly (`docs/tools.py:78-129`). Tasks follows the same pattern (`tasks/tools.py:115-190`). The same pattern occurs in Sheets, Forms, People, Slides, and Meet.

Namespaces using `execute_google_request()` offload `.execute()` but still construct the service first on the event loop. Service construction calls `get_credentials()`, which can perform a synchronous network token refresh, followed by discovery client construction (`auth/google_auth.py:198-277`).

**Impact**

- One slow token refresh or API request could stall unrelated remote users.
- The configured timeout permits a blocked call to last up to 120 seconds.
- Tail latency and throughput degrade sharply under concurrency.
- Cancellation does not stop a request already running in a worker thread.

**Remediation**

- Offload the complete unit of work—credential load/refresh, service construction, request construction, and execution—not just `.execute()`.
- Do not share one `httplib2.Http` across worker threads. Google's client documentation says it is not thread-safe; create a transport per worker/request.
- Cache immutable discovery documents and build per-request/per-thread authorized transports from them rather than repeatedly doing full discovery construction.
- Add per-principal and global concurrency limits, rate limiting, deadlines, and latency metrics by service/tool.

Reference: [google-api-python-client thread safety](https://googleapis.github.io/google-api-python-client/docs/thread_safety.html).

### H-03 — Retry configuration did not cover normal API calls — remediated

**Current implementation:** `RetryingHttpRequest` and lazy request execution apply the configured retry count to every normal API request, not only discovery retrieval. Non-idempotent Apps workflows are protected by durable request-hashed idempotency claims.

`_build_service()` passes `num_retries=settings.http_retries` to `googleapiclient.discovery.build()` (`auth/google_auth.py:261-277`). That option controls retries while retrieving the discovery document. Normal requests are executed as `request.execute()` without a retry value (`common/async_ops.py:36-38`), and many direct payload helpers also call `.execute()` with no retries.

The logs and environment name imply business-request retries, but transient 429/5xx/transport failures are not retried as intended.

**Remediation**

- Pass an explicit retry policy at request execution, not only at discovery construction.
- Retry reads and idempotent writes with capped exponential backoff and jitter; honor `Retry-After`.
- For non-idempotent writes, require provider-supported request IDs, stable resource IDs, or a durable idempotency record before retrying.
- Return structured `retryable`, `retry_after`, and provider status fields to agents.

References: [`discovery.build` documentation](https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.discovery-module.html), [Google client performance guidance](https://googleapis.github.io/google-api-python-client/docs/performance.html).

### H-04 — Destructive actions and safety annotations were inconsistent — remediated

**Current implementation:** permanent message/thread/file deletion and every other tool marked destructive require host-mediated elicitation. The former `force=True` defaults are gone and cannot bypass confirmation. Download/export tools that mutate local storage are annotated as mutating, and race-prone meeting creation is no longer advertised as intrinsically idempotent.

**Baseline evidence:**

- Calendar event deletion defaults `force=True`, so confirmation is skipped (`calendar/tools.py:1014-1045`).
- Permanent thread deletion also defaults `force=True` (`gmail/tools/threads.py:187-211`).
- Permanent Drive deletion proceeds without elicitation when `confirm_permanent=False`, which is the default (`drive/tools/files.py:537-581`).
- Contact, task, permission, and several other deletes have no explicit confirmation contract.
- `download_*` and `export_*` are inferred as `readOnlyHint=True` (`common/component_annotations.py:50-60`, `289-306`) even though Drive/Calendar variants write to the host filesystem. Runtime introspection confirmed this false annotation for `drive_download_file`, `drive_export_google_file`, and `calendar_download_event_attachment`.
- `create_meeting_from_slot` is labeled idempotent (`component_annotations.py:115-121`) even though its protection is process-local and race-prone.

Annotations are hints, not enforcement, but agents and clients use them to decide whether calls are safe.

**Remediation**

- Make irreversible operations two-phase: `prepare_*` returns a short-lived, principal-bound confirmation token and impact preview; `commit_*` consumes it.
- Default to reversible operations such as trash/archive. Require explicit confirmation for permanent delete regardless of client elicitation support.
- Use explicit annotations per tool; do not infer safety solely from verb prefixes.
- Add semantic tests for every filesystem-writing, destructive, and externally mutating tool.

### M-01 — Complete files were buffered in memory — remediated

**Current implementation:** Drive and Calendar downloads stream to exclusive same-directory temporary files, enforce `MCP_MAX_DOWNLOAD_BYTES`, fsync, and publish atomically. Gemini Drive inputs stream to bounded temporary media. The Gmail dashboard attachment path is App-only and rejects declared or observed content above the same limit.

**Baseline evidence:** Drive downloads accumulated all chunks in `io.BytesIO()` and only then wrote the result (`drive/tools/files.py:606-624`, `647-664`). Calendar attachment downloads followed the same pattern. Gemini downloaded Drive media into bytes and image editing read the whole local input (`gemini/tools.py:31-50`, `112-120`). The Apps attachment tool returned base64 in the tool response (`apps/tools.py:622-635`), adding roughly one-third encoding overhead.

That baseline design allowed concurrent large downloads to exhaust process memory and increase time-to-first-byte.

**Remediation**

- Stream to a bounded temporary file or directly to a client resource using a fixed chunk size; atomically rename on success.
- Check Drive metadata size before downloading, enforce per-call/per-principal quotas, and abort when the observed byte count exceeds the limit.
- Return file/resource links with `size`, `mimeType`, checksum, and expiry rather than base64 for large artifacts.

Reference: [Google `MediaIoBaseDownload` file-handle pattern](https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.http.MediaIoBaseDownload-class.html).

### M-02 — Apps idempotency was not concurrency-safe or durable — remediated

**Current implementation:** Apps mutations acquire an atomic SQLite claim keyed by principal-scoped session and idempotency key, bind the key to a request hash, persist completed results, expire abandoned claims, and reject simultaneous or argument-mismatched reuse. Dashboard and in-memory hot caches have TTL and cardinality bounds.

**Baseline evidence:** Apps correctly prefixed explicit/client session IDs with the current principal (`apps/tools.py:67-75`), so the reviewed version avoided a cross-tenant key collision. However, `_IDEMPOTENCY_RESULTS` was an unbounded process-local dictionary, and `_from_cache()` and the side effect were separated (`apps/actions.py:26-38`, `135-179`). Two simultaneous calls with the same key could both miss the cache and create duplicate events. A restart also forgot all keys, making a retried call duplicate the action despite the idempotent annotation.

`_STATE_BY_SESSION` was likewise unbounded and had no TTL (`apps/state.py:11-59`).

**Remediation**

- Use a principal-bound durable idempotency store with atomic “in progress / completed / failed” transitions and TTL.
- Deduplicate before side effects and store a request hash so reusing a key with different arguments fails.
- Add eviction/TTL and quotas to dashboard state.

### M-03 — Credential refresh and 401 invalidation could race — remediated

**Current implementation:** credential load/refresh/save is serialized per principal. Each credential generation is fingerprinted, and an authentication failure can delete storage only if that exact generation is still current; stale in-flight failures preserve newer refreshes.

Each request independently loads and may refresh credentials (`auth/google_auth.py:198-250`). Later, any API request that receives a 401 deletes the principal's cached token (`common/async_ops.py:36-50`). A stale in-flight request can therefore delete credentials that another concurrent request has just refreshed and saved.

**Remediation**

- Serialize refresh per principal across load/refresh/save.
- Associate requests with a credential generation/fingerprint and invalidate only if the stored generation is still the one that failed.
- Use compare-and-swap storage or a transactional database for multi-worker deployments.

### M-04 — OAuth requested more access than the current task needed — remediated

**Current implementation:** consent accepts named capabilities and defaults to Gmail only. OAuth state carries the requested scope set through callback completion, API clients request only their service scopes, status reports granted capabilities, and disconnect calls Google's revocation endpoint before removing encrypted local credentials.

All default Gmail, Calendar, Drive, Sheets, Docs, Tasks, People, Forms, and Slides scopes are requested together (`auth/google_auth.py:24-78`, `138-156`). Several are broad restricted/sensitive scopes, including full Drive and Gmail modification access. Optional namespaces add more scopes to the same credential.

This increases consent friction, blast radius, verification burden, and the consequences of token compromise.

**Remediation**

- Define capability profiles (mail-read, mail-send, calendar-read, calendar-write, files-read, files-write, etc.).
- Request scopes incrementally when an agent first attempts a capability, preserve previously granted scopes, and dynamically hide tools whose scopes were denied.
- Prefer narrower read-only or file-specific scopes where functionality permits.
- Make `disconnect_google_workspace` revoke the grant at Google before deleting the local token; currently it only deletes local state (`auth/google_oauth.py:66-71`).

References: [Google OAuth best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices), [Gmail scope guidance](https://developers.google.com/workspace/gmail/api/auth/scopes).

### M-05 — Month arithmetic was not calendar-month arithmetic — remediated

**Current implementation:** month navigation uses `relativedelta(months=...)`, and month view windows run from the first day of one month to the first day of the next in the account timezone.

Month navigation adds or subtracts 30 days (`apps/state.py:15-24`), and the month fetch window is also fixed to 30 days (`apps/tools.py:97-113`). This skips or overlaps dates depending on month length—for example, advancing from January 31 by 30 days lands in March.

Use the first day of the current/next calendar month (or `relativedelta(months=1)`) and test 28-, 29-, 30-, and 31-day months across year boundaries.

### L-01 — Connection status could report unusable credentials as connected — remediated

**Current implementation:** status distinguishes `not_connected`, `connected`, `missing_scopes`, `refresh_required`, `reauth_required`, and `error`, with expiry, granted scopes/capabilities, the checked capability, and a safe required action.

`google_connection_status()` only checks whether encrypted credential JSON exists (`auth/google_oauth.py:60-63`). It does not report missing scopes, expiry, revoked refresh tokens, or whether reauthentication is required.

Return a structured state such as `not_connected`, `connected`, `missing_scopes`, `refresh_required`, or `error`, plus granted capabilities and expiry metadata without exposing tokens.

### L-02 — OAuth state files could accumulate — remediated

**Current implementation:** create/consume prunes expired and corrupt state, and no principal may hold more than ten live authorization attempts.

OAuth state is written per connection attempt and removed only when that exact state is consumed (`auth/token_store.py:103-150`). Expired, abandoned states are not pruned, and connection attempts are not rate-limited. A valid MCP user can create unbounded encrypted state files.

Prune expired states on create/consume and periodically; enforce per-principal outstanding-state and request-rate limits.

## Agent-friendliness improvements

### 1. MCP Apps file picker — implemented

The server now exposes `files_file_manager`, backed by FastMCP's Prefab `FileUpload` provider. It gives Claude and other MCP Apps-capable hosts a drag-and-drop/native picker, stores bytes outside model context, and returns filenames that agents can pass as `uploaded_file` to Gmail, Drive, and Gemini tools.

This removes several sources of agent friction at once: the agent no longer needs to invent a path on a remote machine, binary/base64 payloads do not consume context, integration tools share one source convention, and invalid mixed sources fail schema validation. The picker tools carry titles, tags, parameter descriptions, and safety annotations so they remain legible in tool catalogs.

Uploads now use stable opaque IDs with separate display names, expiry, checksum, MIME, size, and remaining quota. Explicit deletion and TTL cleanup are implemented. MIME sniffing, declared/detected type checks, ZIP expansion controls, and optional or mandatory ClamAV scanning harden ingestion. Redis metadata plus S3-compatible encrypted blobs provide the multi-worker backend.

### 2. Progressive discovery — implemented for remote Streamable HTTP

The full local catalog contains 140 model-visible tools. The authenticated Streamable HTTP entrypoint applies FastMCP BM25 search and exposes a compact set of workflow/discovery entry points. Capability middleware dynamically hides namespaces lacking granted OAuth scopes and hides local-only filesystem tools and fields remotely. Catalog refresh resets session visibility and emits the protocol's tools-list change notification.

Recommended default after upgrading FastMCP:

- Always visible: connection status/connect, capability overview, unified search, and help.
- Search-discovered: low-level service tools and raw batch-update escape hatches.
- Dynamically visible: only namespaces/scopes enabled for the current principal.

Reference: [FastMCP tool search](https://gofastmcp.com/servers/transforms/tool-search).

### 3. Strict, useful output schemas — implemented

Runtime inspection now finds **140 of 140 tools** with closed object schemas, documented fields, inferred or explicit field types, and no generic open root. Recursive catalog tests validate JSON Schema Draft 2020-12, reject unreviewed open nested objects, and require structural bounds throughout input schemas.

The shared registration layer derives closed schemas from implementation return paths and uses explicit Google API field registries where raw provider responses are returned. Stable list/search metadata follows this envelope:

```json
{
  "status": "ok",
  "items": [],
  "count": 0,
  "next_page_token": null,
  "truncated": false,
  "warnings": [],
  "provenance": {"service": "drive", "fetched_at": "..."},
  "next_actions": []
}
```

Typed output schemas help clients validate results and help agents plan field access. Reference: [MCP tool output schemas and structured content](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

### 4. Return compact context first, details by resource — implemented

List/search tools use bounded results and normalized envelopes; Gmail defaults to hygienic clean content and explicit full-body follow-ups. Large files use picker/App/resource paths rather than model-visible base64, and remote HTTP uses progressive discovery so low-level detail tools consume context only when selected.

### 5. Cross-service discovery and resolution — implemented

`search_workspace(query, services, max_results_per_service)` concurrently resolves Drive files, contacts, and Gmail message IDs into normalized references with canonical `gdrive:`, `gmail-message:`, and other workspace resource URIs. `resolve_workspace_resource` dereferences them. `get_workspace_capabilities` reports the live principal-specific catalog and recovery path.

### 6. Safe workflow-level tools — implemented

Raw tools remain available as escape hatches. Existing workflow tools cover:

- prepare/send email with recipient and attachment preview;
- find slots/create meeting with a durable idempotency key and conflict snapshot;

Meeting workflows use durable request-hashed idempotency and conflict-aware scheduling. High-impact email, sharing, recurring-calendar, and large batch mutations use principal-bound, encrypted, one-time prepare/commit tokens with impact previews. Destructive workflows require explicit host confirmation.

### 7. Machine-readable errors — implemented

`StructuredToolErrorMiddleware` now maps uncaught failures consistently:

```json
{
  "code": "reauth_required",
  "message": "Google authorization expired.",
  "retryable": false,
  "required_action": {"tool": "connect_google_workspace"},
  "provider_status": 401,
  "field_errors": []
}
```

It distinguishes invalid input, permission, not found, authentication, rate limit, timeout, provider outage, circuit-open, upload, confirmation, and internal failures while suppressing unexpected internal details. Recoverable cases carry executable or tightly constrained `required_action` objects.

### 8. Uniform pagination and freshness — implemented

The shared tool wrapper adds `has_more`, normalized `next_page_token`, `count`, and RFC3339 `fetched_at` to every list/search response while preserving provider tokens for compatibility. Existing effective-query/window fields remain intact.

## Positive observations

- Principal storage keys hash issuer + subject, preventing raw identity path injection (`auth/identity.py:17-28`).
- Per-principal credential JSON and OAuth state are Fernet-encrypted, written atomically, and permission-restricted on POSIX (`auth/token_store.py:35-75`).
- OAuth state is random, one-time, short-lived, principal-bound, and paired with PKCE (`auth/google_oauth.py:38-57`; `auth/token_store.py:103-156`).
- Remote HTTP refuses incomplete OIDC/HTTPS configuration; trusted hosts and origins are explicit. Readiness fails closed when distributed-state, object-storage, token, keyring, or multi-worker affinity contracts are unmet.
- Tool inputs generally have bounds, useful descriptions, pagination, and partial-response field controls.
- The Apps UI has a deliberate email HTML allowlist and URL/style sanitization (`apps/ui/src/render.ts:89-173`, `194-323`).
- Tests cover composition, auth scopes, remote principal isolation, HTTP startup behavior, tool schemas, annotations, production controls, and most service families.

## Validation performed

| Check | Result |
| --- | --- |
| `python -m pytest -q --basetemp .test-tmp-final-audit` | 139 passed in 10.22 seconds; one non-code pytest cache ACL warning. |
| `ruff check src tests scripts` | Passed. |
| `python -m mypy src` | Passed across 130 source files. |
| Output/input schema catalog audit | 140/140 tools have closed documented output schemas; recursive bounded input validation and Draft 2020-12 validation passed. |
| Remote progressive-discovery smoke test | Streamable HTTP catalog is capability filtered and retains workflow, discovery, connection, search, and picker entry points. |
| MCP client render smoke test | `files_file_manager` listed with UI resource metadata and returned `[Rendered Prefab UI]`. |
| Apps UI TypeScript compile | Passed. |
| Apps UI Vite production build | Passed; 145 modules, 484.57 kB single-file output (117.08 kB gzip). |
| `git diff --check` | Passed. |
| Tracked secret-pattern review | No committed credential/token/private-key file found; matches were configuration placeholders/tests. |
| Python installed dependency advisory scan | `pip-audit --path .venv/Lib/site-packages`: no known vulnerabilities; only the local unpublished project was skipped. |
| JavaScript development lock | Vite upgraded from affected 6.4.1 to fixed 6.4.2; lock version, tarball URL, and published integrity were verified. |

The first pytest attempt and UV-based commands encountered local Windows cache/temp ACL problems; rerunning the virtualenv test suite with an approved writable temporary directory produced the clean result above. No live Google account/API calls were made, so Google provider behavior was reviewed from code and mocked tests rather than destructive live validation.

## Operational follow-ups (not unresolved review findings)

1. Provision Redis and S3-compatible object storage, enable session affinity, and tune concurrency/rate/deadline values from measured production traffic.
2. Set production-specific upload/download quotas and retention; configure ClamAV and set `MCP_REQUIRE_MALWARE_SCAN=true` where uploads are accepted.
3. Wire Prometheus/OpenTelemetry, privacy-safe audit logs, health probes, emergency revocation, and versioned secret files into the deployment platform.
4. Add live non-destructive Google sandbox smoke tests in CI; this review intentionally did not access a real account.
