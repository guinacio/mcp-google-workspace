# Google Workspace MCP migration plan

**Baseline:** `fba625378725aefabec88c06e8c5bfd6a9ce391e`, package `0.3.13`  
**Research and verification date:** 2026-09-26  
**Targets:** FastMCP **4.0.10**, MCP **2026-07-28**, MCP Apps SDK **2.0.3**  
**Status:** implementation plan; the production source and dependency locks have not been migrated.

The server needs a coordinated migration of execution, state, and UI behavior. Changing the dependency requirement alone fails: the isolated FastMCP 4 experiment produced **24 failures / 192 passes**, compared with **216 passes** in a clean environment installed from the existing lock. The dashboard's seven existing browser tests pass, but they do not establish conformance under a real sandbox, authentication, or the new protocol. See `docs/MIGRATION_AUDIT_2026-09-26.md` for the evidence, scope, and experimental limitations.

The recommended destination is one FastMCP 4 application supporting modern clients and a tested compatibility path for older clients. Business state must survive independently of transport connections. Modern confirmations must finish a request and resume through a new request. The dashboard and Prefab file picker both need changes. A confirmed HTML attribute-escaping defect should be fixed before releasing either UI migration.

## 1. Version baseline and source authority

| Layer | Repository baseline | Migration target / decision |
| --- | --- | --- |
| Application | `0.3.13` | Choose a new application release separately from protocol/library versions. |
| FastMCP / slim | `3.4.4` in `uv.lock`; declaration `fastmcp[apps,tasks]>=3.4.4` | Start with `fastmcp[apps,tasks]>=4.0.10,<5`; lock exactly `4.0.10` for the first release. |
| Python MCP SDK | `mcp==1.28.1` | SDK 2.x; isolated resolution selected `mcp==2.2.0` and `mcp-types==2.2.0`. Lock and validate that combination. |
| Background tasks | FastMCP 3 built-in behavior; `pydocket==0.23.0` | `fastmcp-tasks==4.0.10`, explicit `TasksExtension`; isolated resolution selected Docket `0.25.2`. |
| Prefab | `prefab-ui==0.20.2` | Retain this version initially; it also resolved under FastMCP 4. Validate the changed provider result and tool surfaces. |
| Pydantic / Starlette | `2.12.5` / `1.3.1` locked | Existing floors satisfy FastMCP 4's `>=2.12` / `>=1.0.1` requirements. Avoid unrelated upgrades in the implementation lock refresh. |
| MCP core protocol | `2025-11-25` asserted in a test | `2026-07-28`; retain explicitly tested legacy interoperability. |
| MCP Apps SDK | `@modelcontextprotocol/ext-apps==1.1.2` locked | Exact `2.0.3` first, then controlled compatible updates. |
| TypeScript MCP SDK | Monolithic `@modelcontextprotocol/sdk==1.30.1` | Split `@modelcontextprotocol/client` and `@modelcontextprotocol/core`, initially `2.1.0`; use Zod `>=4.2,<5`. |
| Apps wire protocol | Existing SDK 1.x | Stable **2026-01-26**. Do not substitute the core MCP date for the Apps UI protocol version. |
| Python / Node | Python `>=3.12`; CI Node 24 | Keep Python 3.12 as the primary deployment target and Node 24 for builds; test newer Python separately. |

FastMCP 4.0.10 was published September 25 and fixes task registration behind search transforms and nested tool execution—both relevant to this repository's BM25 discovery and prepare/commit proxy. [FastMCP 4.0.10 release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.0.10).

The Apps registry had `latest=2.0.0` and `release-2.0=2.0.3` at audit time. Version 2.0.3 was published and is the latest GitHub release; use an explicit version rather than assuming the npm tag selects it. Its patch notes concern examples, not changes to the library itself. [Apps 2.0.3 release](https://github.com/modelcontextprotocol/ext-apps/releases/tag/v2.0.3), [npm package metadata](https://registry.npmjs.org/@modelcontextprotocol/ext-apps).

Apps SDK 2 changes dependencies and TypeScript APIs while preserving the existing `ui/*` channel's wire compatibility. The upstream repository still marks the dated 2026-01-26 Apps specification stable; later additions are in `specification/draft`. [Apps version table](https://github.com/modelcontextprotocol/ext-apps#specification), [SDK 2 migration guide](https://apps.extensions.modelcontextprotocol.io/api/documents/migrate-to-v2.html).

Context7 was used to resolve FastMCP and MCP Apps and retrieve documentation. Its results included older examples and FastMCP 3 version entries. Release tags, package registries, the dated MCP specification, and installed 4.0.10 code took precedence. Recheck exact releases at implementation start, and record any changed target in this document.

## 2. Current architecture and what to retain

The default composition exposes **139 tools, seven concrete resources, two resource templates, and three prompts** before progressive discovery and authorization filtering. Enabling all optional integrations produces **193 tools, 14 resources, nine templates, and seven prompts**. These counts describe the FastMCP catalog, including app-visible entries; they are not the model-visible list under every host.

| Area | Current design | Migration disposition |
| --- | --- | --- |
| Composition | Root in `server.py`; Gmail, Calendar, Drive, Sheets, Docs, Tasks, People, Forms, Slides mounted with `namespace=` | Retain composition and existing public business tool names. Current mounts already use the supported API. |
| Optional services | Apps, Keep, Chat, Meet, Gemini feature flags | Preserve opt-in semantics and test every flag independently and together. |
| Remote identity | JWT verification; encrypted Google grants keyed by a hash of `iss` + `sub` | Retain isolation and encryption. Add complete MCP authorization discovery and validate task identity restoration. |
| Google consent | Incremental scopes, one-time OAuth state, PKCE, encrypted refresh tokens | Preserve; integrate URL elicitation only where host capability is present. |
| Middleware | Errors, admission/telemetry, authorized catalog, prepare/commit, resource handles | Retain policy intent; adapt result types, error semantics, continuation rounds, and worker execution. |
| File uploads | Prefab provider; local connection-scoped memory; remote principal-scoped SQLite/blob or Redis/S3 storage | Retain remote opaque handles and quotas; replace connection-scoped local storage and private provider assumptions. |
| Dashboard | Eleven tools, shared HTML, Python state dictionaries, browser localStorage ID | Move to explicit application handles and durable state, with per-view lifecycle. |
| Discovery | BM25 `search_tools`/`call_tool`; process-wide environment switch; Claude exception | Retain as an optional optimization. Verify app callbacks and worker tasks through the transform; do not tie search results to connection visibility. |
| Packaging | uv lock, MCPB build, committed single-file UI, Docker/GHCR, CI | Retain reproducibility, provenance, non-root container, and generated-UI drift checks. |
| Existing protections | Origin/Host configuration, payload limits, encrypted key rotation, principal revocation, circuit breaker, deadlines | Preserve and regression-test. Tighten only the specific gaps identified below. |

## 3. Protocol changes: application consequences

### 3.1 Stateless requests and discovery

Modern MCP removes the core `initialize`/`notifications/initialized` exchange and `Mcp-Session-Id`. Requests carry version and capabilities in `_meta`; servers implement `server/discover` and return `resultType`. FastMCP/SDK should own those mechanics. Application code should consume the negotiated request context, not recreate the wire protocol. [Core overview](https://modelcontextprotocol.io/specification/2026-07-28/basic), [versioning and compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning).

Actions:

- Replace implicit dashboard and local-upload session scope with explicit, server-issued application handles.
- Remove connection visibility reset from `refresh_workspace_catalog`; query current grants on each relevant request and invalidate application catalog caches by principal/grant revision.
- Preserve authorization-based catalog filtering. The protocol permits a catalog to vary by request authorization; it forbids dependence on previous requests on that connection. Sort catalogs deterministically. [Tool listing rules](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#listing-tools).
- Keep `/health/live`, `/health/ready`, and `/version`. Update the hard-coded `2025-11-25` test and report tested protocol support without confusing it with the package version.
- Do not claim that `stateless_http=False` itself is removed. An isolated FastMCP 4.0.10 HTTP probe using that setting still served modern requests without a session header and rejected modern GET with 405. The flag continues to affect legacy handling.

### 3.2 HTTP routing, streaming, cancellation, and notifications

Modern POSTs require matching version/method headers and a name header where applicable. There is no modern GET notification channel or SSE replay. Request-scoped SSE remains supported; subscriptions use a POST response stream. Closing an HTTP response stream is the cancellation signal; stdio retains its cancellation notification. [Streamable HTTP specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).

Actions:

- Verify `MCP-Protocol-Version`, `Mcp-Method`, and conditional `Mcp-Name` through the real reverse proxy; reject mismatches before executing a tool. Preserve header encoding rules through intermediaries.
- Do not add `x-mcp-header` to email bodies, filenames, Google tokens, approval tokens, or personal data. Adopt it only for a demonstrated nonsensitive routing requirement.
- Replace blanket `json_response=True` with a tested response policy that allows request-scoped progress and long-lived subscription streams. A JSON response is valid, but it cannot carry intermediate progress.
- Keep Origin validation and the localhost default. If browser clients directly access HTTP, configure CORS separately and include the new routing headers; this is distinct from the iframe's Apps channel.
- Rework readiness: shared credentials, uploads, task queue, application state, and continuation keys must be checked for a modern replica fleet. Remove the unconditional `MCP_SESSION_AFFINITY` requirement for modern traffic. Preserve legacy affinity only if the supported legacy mode still needs it.
- Implement `subscriptions/listen` only when advertising change subscriptions; otherwise return accurate unsupported capabilities and use fresh listing/short-lived private caching. `refresh_workspace_catalog` must not promise a notification it did not deliver.
- Test actual disconnects. `run_blocking(..., abandon_on_cancel=True)` stops waiting for a thread but cannot undo a Google API operation already executing. Record an uncertain mutation outcome instead of encouraging an unsafe blind retry.

### 3.3 Multi Round-Trip Requests and action safety

There are **14 direct `.elicit()` call sites** and **ten additional callers of the shared confirmation helper**. The helper's call is included in the 14. They affect Gmail, Calendar, Drive, Chat, Keep, People, and Google Tasks. FastMCP 4 retains imperative elicitation for older protocols; modern requests must return `InputRequiredResult` and examine `ctx.input_responses` on the next invocation. [FastMCP elicitation](https://gofastmcp.com/servers/elicitation).

Create one shared action-confirmation adapter with two protocol branches:

1. Validate arguments and the caller's current authorization; compute the exact action and preview.
2. For modern requests without an accepted answer, return an input-required result **before any mutation**. Bind principal, tool, canonical argument digest, expiry, and operation identifier in continuation state.
3. On retry, validate the answer's type and `action`, verify the continuation and current permissions, and reject changed arguments or another principal. Decline/cancel terminates without executing.
4. For a supported legacy request, use `ctx.elicit(..., response_type=...)` through the same policy adapter.
5. If the host cannot elicit, return an actionable unsupported/confirmation-required result. Never turn unavailable confirmation into automatic consent.

FastMCP seals `request_state`; configure a shared `RequestStateSecurity` key ring for replicas rather than relying on ephemeral per-process keys. Framework integrity protection does not replace application replay prevention or argument binding. The MCP continuation rules specifically treat returned state as attacker-controlled. [MRTR requirements](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr), [FastMCP continuation security](https://gofastmcp.com/servers/elicitation#carrying-state-across-rounds).

The current `ApprovalStore.consume()` deletes a token before the nested tool completes. Under MRTR, that nested tool may only have returned a question; under network failure, the mutation may already have succeeded. Replace destructive consume with a durable operation record:

`prepared -> awaiting_input -> executing -> succeeded | failed | outcome_unknown`

Use atomic claim and completion transitions. A repeated commit for a completed operation returns its saved result. A changed payload is rejected. An uncertain non-idempotent Google operation is reconciled before retry. Do not describe this as exactly-once execution across Google: local transactions cannot atomically commit Google's side effect. Calendar creation already has an idempotent event ID; retain it and add equivalent provider-aware handling where possible.

All wrappers must preserve interim results. In particular, review `ResourceHandleMiddleware`, structured error handling, pagination/output-schema inference, `commit_workspace_action`, and the BM25 `call_tool` proxy. FastMCP represents an asking tool round as an `InputRequiredToolResult`; do not wrap it in a successful business payload, attach resource handles to it, or treat it as a completed commit.

### 3.4 Tasks extension

The task protocol moved from the core into `io.modelcontextprotocol/tasks`. Its stable schema is dated 2026-07-28. The new lifecycle uses `tasks/get`, supports `tasks/update` for answers, and removes the old `tasks/result` and `tasks/list` interfaces. This is unrelated to the repository's Google Tasks service namespace. [Official Tasks extension](https://github.com/modelcontextprotocol/ext-tasks), [MCP change record](https://modelcontextprotocol.io/specification/2026-07-28/changelog).

Register `TasksExtension` at each runnable server composition that exposes task tools. The root has nine by default and 13 with Gemini. Tests also connect directly to subservers, so fixtures or runnable subserver factories must install the extension too. Prefer one root-managed queue in production; do not accidentally start an unrelated queue per mounted namespace.

Replace `fastmcp.settings.docket.url` in `server_http.py:41`: that setting is absent in 4.0.10. Pass `url`, queue name, and concurrency to `TasksExtension`, or its documented environment defaults. Preserve `MCP_REDIS_URL` as an application configuration input mapped by one shared factory for HTTP, stdio, and workers.

The Tasks extension captures caller context for workers. Enable `FASTMCP_TASKS_ENCRYPTION_KEY` consistently across the fleet; drain before a rotation that invalidates queued snapshots. This protects the credential snapshot, **not tool arguments and results**. Redis still holds sensitive work data and needs access control, retention, backups, and encryption appropriate to this application. [FastMCP tasks and credentials at rest](https://gofastmcp.com/servers/tasks#credentials-at-rest).

Require tests for:

- Correct caller on worker execution, expired/revoked grants, failed snapshot decryption, cross-user task-handle access, restart recovery, expiry, and cancellation.
- A host without the extension and a legacy host: synchronous fallback must be bounded and usable.
- Search-discovered tasks and nested calls: FastMCP 4.0.10 runs a task tool invoked by another tool in the foreground. Account for that in deadlines and in the UI; do not promise a background receipt on those paths.
- Admission and authorization at execution time, not only submission. Verify which middleware hooks the worker executes and place indispensable checks in the service/action layer if necessary.
- All existing progress calls inside tasked execution, using the supported task progress API where the foreground Context behavior is insufficient.

### 3.5 Error, schema, and caching contracts

Fix `McpError(ErrorData(...))` to the supported keyword constructor. Preserve machine-readable recovery information, preferably in structured error data rather than JSON encoded inside `message`. The audit reproduced its `TypeError` independently.

The current custom rate-limit code `-32029` falls in the newly reserved MCP specification range. Retire that allocation; select an application code in `-32000..-32019`, or use a tool execution error envelope with the stable string code `rate_limited`. Do not override SDK errors such as `HeaderMismatch=-32020`, `MissingRequiredClientCapability=-32021`, or `UnsupportedProtocolVersion=-32022`. [MCP error allocation and changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog#minor-changes).

Separate malformed protocol requests from failed tool execution. A Google API failure should ordinarily be a tool result with `isError: true`, meaningful text, and structured recovery details. Several tools currently return an `error` dictionary as a successful result, while the global middleware raises JSON-RPC errors for all uncaught failures. Make the distinction consistent, including partial-success batch tools. [Tool error handling](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling).

Python SDK 2 uses snake_case attributes. Migrate annotation reads in `common/component_annotations.py:331` and SDK model reads in tests; keep camelCase on the wire and in Google API dictionaries. Run CI with `FASTMCP_MCP_CAMELCASE_COMPAT=false`. Imports from `mcp.types` remain supported. Standardize framework component imports on public modules such as `fastmcp.tools`; remove private component mutation where a supported registration/transform API exists. [FastMCP 3-to-4 guide](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3).

Do not remove strong output schemas merely because the protocol now allows any JSON value in `structuredContent` and broader JSON Schema. Keep stable business envelopes, validate JSON Schema 2020-12, and test actual serialized results. Replace fragile AST inference incrementally with typed response models at migration hotspots. Test URI templates against the new path-screening behavior, including encoded resource IDs and normal dotted values.

The isolated SDK probe emitted `ttlMs: 0`, `cacheScope: "private"`, and `resultType: "complete"` without custom code. Preserve that safe default initially. Positive cache lifetimes require deliberate freshness policy and must never share Google data or grant-filtered catalogs across principals. FastMCP's constructor-level cache hint applies broadly, including resource reads; it is not a per-tool catalog knob. Do not set a blanket public TTL to optimize listing. [Installed FastMCP cache implementation](https://github.com/PrefectHQ/fastmcp/blob/v4.0.10/src/fastmcp/server/caching.py).

### 3.6 Authentication, sampling, and observability

`JWTVerifier` verifies tokens but supplies no discovery routes. Wrap it with `RemoteAuthProvider`, configured with the external authorization server, so MCP protected-resource metadata and its challenge URL are actually served. Verify resource URL/audience configuration with the IdP, issuer/JWKS rotation, scoped challenges, and proxy base paths. Keep incoming MCP tokens separate from stored Google tokens; no token passthrough. [Authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [RemoteAuthProvider source](https://github.com/PrefectHQ/fastmcp/blob/v4.0.10/src/fastmcp/server/auth/auth.py).

Client-registration changes primarily belong to the external authorization server and MCP clients. This repository does not implement dynamic client registration; do not build an authorization server solely to migrate. Verify the IdP/client integration supports Client ID Metadata Documents where available, correct issuer binding, and validation of a returned authorization `iss`. Review the separate Google callback's issuer handling and move its blocking token exchange off the event loop. Preserve its existing one-time state and PKCE protections.

`chat/server.py:32` and `keep/server.py:34` use the removed FastMCP `ctx.sample()`. Recommended replacement: optional, configured server-side summarization using an explicit provider; when unconfigured, return an actionable unavailable result and keep the underlying read tools usable. Preserve summary tool names where practical. Do not silently move private Chat/Keep content to Gemini merely because the optional media integration is enabled. Selection of provider, data handling, and cost is a deployment decision. Core Sampling is deprecated, rather than already deleted from the protocol. [FastMCP sampling migration](https://gofastmcp.com/servers/sampling), [deprecated features](https://modelcontextprotocol.io/specification/2026-07-28/deprecated).

Retain ordinary server logging and existing metrics. Reduce dependency on client-facing `ctx.info/debug/warning`, because protocol Logging is deprecated and modern logging notifications require request opt-in. Keep progress as a separate supported feature. Extract allowed `traceparent`/`tracestate` metadata into spans, bound or drop untrusted baggage, and distinguish an MRTR round from a completed logical operation. Never put tokens, raw mail, or continuation secrets into logs. Verify httpx2 exception handling, OS trust-store behavior, and logger names at framework boundaries; Google libraries may still use httpx/requests independently.

## 4. MCP Apps compliance and best-practice review

**Verdict:** both UIs have useful standard foundations, but neither is ready to be certified for the migration target. The dashboard needs security and lifecycle work; the file picker has a reproduced FastMCP 4 result-schema incompatibility and connection-scoped local storage. Existing browser tests demonstrate selected behavior, not full host conformance.

### 4.1 Compliance matrix

“Pass” below means the audited source or local test satisfies the stated limited property. “Gap” is a concrete implementation or verification gap. “Optional” is not a protocol violation.

| Contract / practice | Dashboard | Prefab file picker | Required work |
| --- | --- | --- | --- |
| `ui://` resource, standard MIME, HTML | Pass: registered HTML resources | Pass: fetched standard MIME in v4 probe | Keep wire contract tests. |
| Nested `_meta.ui.resourceUri` | Pass, alongside old flat alias | Pass, generated by provider | Make nested metadata canonical; remove flat alias only after supported hosts are verified. |
| URI resolves after composition | Currently works through alias; mounted canonical URI also becomes `ui://apps/apps/dashboard-ui` | Provider-generated hashed URI resolves | Normalize dashboard declaration/namespace, retain old resource URIs during transition. Do not simply delete the alias. |
| Text fallback | View models include fallback text; dictionaries become text content | Declarative picker output is not a useful upload fallback for every host | Provide concise meaningful `content` for all launch tools and clear alternatives for hosts without UI. |
| App-only visibility | Explicit only on email attachment callback; omitted elsewhere defaults to model + app | Main picker model-only; store/delete app-only metadata supplied by provider | Review intended exposure for every callback; server authorization must remain authoritative. |
| Initialization | Uses official App SDK; handlers before `connect()` | Delegated to Prefab renderer | Keep Apps `ui/initialize` handshake. Core handshake removal does not remove the UI handshake. |
| Tool input and completion lifecycle | Result handler exists; no `ontoolinput` or cancellation handler | Dependency-owned | Use input/result as the authoritative invocation context; test cancellation, partial results, and restart. |
| Host capabilities | UI infers operations from names; no `getHostCapabilities()` checks | Dependency-owned | Gate optional actions on actual host support and current server-granted capabilities. |
| Tool discovery | Paginates up to five pages; falls back to guessed names for all operations | Hashed callbacks/provider addressing | Keep no-`tools/list` fallback, but never present guessed write operations as confirmed available. Supply an explicit operation manifest. |
| Downloads | Calls draft `downloadFile` unconditionally; error fallback assumes `isError` | Upload interaction rather than downloads | Capability-gate; handle rejected promises and host-denied actions; bound inline bytes. |
| Styling | Applies theme, host variables/fonts, safe area | Dependency-owned | Existing tests pass; add fixed/flexible dimensions, narrow screens, keyboard/focus and resize tests. |
| Teardown | Returns `{}`; delayed initial load is not cancelled | Dependency-owned | Cancel timers/requests/listeners; ignore late results; dispose per-view state. |
| CSP | No declared CSP; bundled dashboard can use restrictive default | Resource allows jsDelivr | Dashboard omission is not itself a violation. Audit actual resource-read metadata and render with restrictive CSP. Pin or bundle Prefab renderer for controlled deployments. |
| Sandbox | Test host uses same-origin iframe without sandbox | No complete renderer-in-host E2E coverage | Add a different-origin sandbox proxy and policy-enforcing host tests. |
| Untrusted content | Confirmed quoted-attribute injection in `esc()` use; custom email sanitizer reuses it | Renderer/schema supplied by dependencies | Release blocker: safe DOM construction or correct context-specific escaping; adversarial payload tests. |
| State ownership | Transport session fallback + process-local map + reusable browser ID | Local session key; remote principal handles | Introduce server-issued per-view handles; shared state/TTL for replicas; independent local trusted scope. |
| SDK 2 migration | Old SDK import fails compilation | Python upgrade changes emitted payload | Split TS dependencies and migrate import; adapt picker schema and newly generated tool definitions. |
| Chat/model context | Chat buttons intentionally do nothing in MCP mode; no explicit context updates | Dependency-owned | Implement user-triggered `sendMessage` only if supported; optional minimal `updateModelContext` for relevant state. |

The standard mandates meaningful content, resource discovery, visibility enforcement, and the Apps initialization sequence. A web host owns the different-origin sandbox proxy and CSP enforcement; server authors must supply compatible HTML/metadata and test with such a host. Missing CSP metadata invokes a restrictive host default, so adding a broad allowlist would make this dashboard less safe. [Stable Apps specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx).

### 4.2 Security and lifecycle changes

1. **Repair rendering first.** `render.ts:14` uses `textContent -> innerHTML`, which leaves quote characters untouched. The result is interpolated into `data-filename`, form values, link attributes, and reconstructed email HTML. A benign `report" data-audit-injected="yes` filename created a second DOM attribute in the audit. Use DOM APIs for values/attributes, sanitize URL schemes separately, and replace or rigorously harden the custom email HTML sanitizer. Block event handlers, active embedded content, external tracking requests, CSS escapes, and malformed markup. Do not rely on the surrounding sandbox to protect the App's own tool access.
2. **Gate optional features.** Read `app.getHostCapabilities()` after connect. Respect server-tools, links, downloads, messages, and display-mode support. A denied host action is a denial, not permission to force an alternate download. Offer an explicit supported alternative to the user.
3. **Use tool input/result lifecycle.** Register input, result, cancellation, error, context-change and teardown handlers before connect. Cancel the 300 ms fallback load on teardown or receipt of the real invocation result. Use generation counters/abort signals so old navigation responses cannot overwrite a newer selection.
4. **Represent execution errors correctly.** `callServerTool` may resolve a result with `isError`, or reject with a protocol/SDK error. Distinguish both before optimistic success messages. Migrate string-prefix error matching to typed error codes; restore or refresh optimistic UI state on failure.
5. **Supply operation metadata.** Return only the applicable operation names and app handle in server-generated UI metadata, with validated capabilities. Tool visibility, Google scopes, and host features are separate checks. Keep direct tool-name fallbacks for reads only where safely supportable.
6. **Keep identities out of browser authority.** The UI can remember a server-issued handle; the server authenticates every use. Browser localStorage and `Math.random()` are not the handle minting authority. A new conversation/view should not inherit another view's transient state by sharing one storage key.
7. **Isolate the standalone adapter.** The `mode=standalone` path has unvalidated message sources and wildcard postMessage. Give trusted development previews explicit allowed origin/source checks, or omit this adapter from the production artifact. It is a separate nonstandard channel, not part of Apps compliance.
8. **Make external navigation host-mediated.** Attachment links already use `app.openLink`; route conference links and sanitized email links through the same adapter and validate supported schemes. Test hosts that block popups and direct anchors.

### 4.3 Updated Apps SDK and draft features

Install the View/host dependency set, not the Node MCP server adapters: this application has a Python server. Replace `@modelcontextprotocol/sdk/types.js` in both `src/mcp-app.ts` and `tests/host.ts`. Schemas move to `@modelcontextprotocol/core`; use the split client for protocol types/APIs. Upgrade custom request handlers to method-keyed SDK 2 signatures. The temporary UI copy compiled after only the production schema import was moved, but this does not validate the excluded test host or runtime semantics. Include tests in a dedicated TypeScript check.

The stable dated Apps spec does not contain `ui/download-file`; the draft does, with a `downloadFile` host capability. The existing UI already uses that API. Keep it as a negotiated optional enhancement and provide a meaningful fallback; do not label it universally supported merely because SDK 2 exports it. [Apps draft download contract](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx#requests-view--host).

The draft also expands app-provided tools and their lifecycle. They could expose the current calendar selection or displayed state, but are not required for this migration. Defer them until a concrete host workflow needs them, then capability-negotiate and constrain them. The current `new App(..., {})` does not falsely claim this support. Track draft changes separately from the stable release gate.

Do not add React, WebMCP, CodeMode, an OpenAPI rewrite, or app-side tools solely because they are available. The existing vanilla TypeScript, mounted Python services, and BM25 transform can meet the target protocol with less change.

## 5. Target design decisions

| Decision | Recommended design and rationale |
| --- | --- |
| Core protocol | Let FastMCP/SDK 2 serialize, negotiate and dispatch the modern protocol; validate it with raw-wire tests. |
| Compatibility | Support 2026-07-28 plus selected legacy versions through FastMCP's compatibility implementation. Keep application confirmation branches in one adapter. Legacy retirement is based on supported-client evidence. |
| Dashboard state | Use a server-issued per-view `SessionId`/application handle; shared Redis state with explicit TTL remotely, explicit trusted local storage for stdio. FastMCP `SessionProvider` is a candidate. |
| State identity | Preserve Google-token identity `(issuer, subject)`. FastMCP sessions additionally scope by client ID; document whether cross-client UI state should be separate. Do not silently migrate existing storage keys. |
| State concurrency | Use atomic revision/CAS or a per-view serialization policy. FastMCP's simple session get/set uses read-modify-write and is insufficient by itself for concurrent navigation updates. |
| Preferences | `UserSession` is suitable for authenticated per-user preferences, not as an unexamined replacement for unauthenticated stdio. |
| Confirmation | Modern guard results, legacy adapter, shared sealed request state, durable replay-aware action records. |
| Tasks | Registered extension, explicit Redis queue configuration, encrypted snapshots, deliberate synchronous fallback. |
| Catalog | Deterministic current-authority filtering; zero/private caching first; progressive discovery must preserve Apps addressing. |
| UI delivery | Versioned single-file dashboard; exact Prefab renderer version/bundle; nested Apps metadata; narrow CSP. |
| Errors | Stable application envelopes with correct protocol-vs-tool classification and SDK 2 constructors. |
| Summaries | Optional explicitly configured server-side provider; no dependency on removed Context methods. |

FastMCP supplies both per-user injected state and explicit session handles, but storage retention is configured on the store. Set a default TTL wrapper rather than assuming FastMCP expires those records automatically. These are **application sessions**, not the removed transport session. [FastMCP state APIs](https://gofastmcp.com/servers/sessions).

## 6. Ordered implementation work packages

The work packages are reviewable PR-sized outcomes, not instructions to deploy immediately. Owners are roles to assign. Engineering effort is a planning estimate, not a measured schedule: approximately **18–28 engineer-days**, plus host/IdP qualification and rollout time. Security/rendering can proceed independently of the protocol foundation.

| ID | Work package | Depends on | Owner | Estimate |
| --- | --- | --- | --- | --- |
| W0 | Freeze contracts and reproducible baseline | None | Backend + QA | 1 day |
| W1 | Fix dashboard rendering and isolate standalone bridge | W0 | Frontend | 1–2 days |
| W2 | FastMCP/SDK foundation, task bootstrap, schema adapter | W0 | Backend | 2–3 days |
| W3 | Explicit state and upload lifecycle | W2 | Backend + frontend | 3–4 days |
| W4 | Confirmation, commit replay, mutation recovery | W2, W3 | Backend | 3–5 days |
| W5 | HTTP, auth discovery, catalog and observability | W2–W4 | Backend/platform | 2–3 days |
| W6 | Apps SDK 2 and host lifecycle | W1–W4 | Frontend + backend | 2–3 days |
| W7 | Conformance, release packaging, host qualification | W3–W6 | QA/platform | 3–5 days |
| W8 | Canary, rollback drill and cleanup | W7 | Platform | 1–2 days |

### W0 — Freeze the contracts

- Save source revision, versions, enabled-service combinations, tool/resource/prompt catalogs and schemas, UI metadata, and current authorization/confirmation policies.
- Reproduce the 216-test baseline from `uv.lock`. Use a dedicated environment for subprocess bundle tests so `uv run` does not mutate an interpreter running another test suite.
- Record the supported host versions and whether each offers modern core MCP, stable Apps, elicitation, Tasks, downloads, and sandboxed UI. Local mock-host results are not substitutes.
- Keep future generated catalog snapshots free of user data or credentials.

**Exit:** baseline tests pass, documented snapshots exist, expected compatibility targets are enumerated, and the migration branch has no unintended catalog changes.

### W1 — Repair the dashboard security boundary

- Files: `apps/ui/src/render.ts`, `apps/ui/src/mcp-app.ts`, `apps/ui/tests/*`.
- Correct text/attribute/URL handling; use a maintained sanitizer if rich email HTML remains necessary, with restrictive configuration and version lock.
- Add adversarial filenames, event titles, sender/subject strings, URLs, encoded quotes, data images, CSS and malformed HTML tests.
- Validate standalone message sender/origin; reject unrelated frames.

**Exit:** injected attributes/handlers cannot be constructed, external image tracking remains blocked, normal mail/calendar rendering and seven existing browser scenarios still pass. This fix can ship before the major migration.

### W2 — Establish the FastMCP 4 foundation

- Files: `pyproject.toml`, `uv.lock`, `server.py`, `server_http.py`, subserver factories/fixtures, `common/errors.py`, `common/component_annotations.py`, `file_uploads.py`.
- Constrain FastMCP to the validated major and regenerate the lock with minimal unrelated updates. Declare directly imported runtime dependencies rather than relying entirely on transitive inclusion: review `anyio`, `starlette`, `redis`, `opentelemetry-api`, `prometheus-client`, and `httplib2`; declare test-only `jsonschema` explicitly in development dependencies.
- Add the extension factory and replace removed Docket configuration. Test default and all-option startup, including direct subserver clients.
- Fix exception constructors and Python field access; make compatibility-shim-off tests pass.
- Fix the picker output schema for the actual 4.0.10 Prefab envelope, including `_meta`, without blindly accepting arbitrary fields. Validate newly generated `files_store_files` input documentation/limits and app-only visibility.
- Replace private registry manipulation with supported registration/transform APIs where practical; isolate unavoidable version-specific provider code in a tested adapter.
- Replace Chat/Keep sampling or explicitly gate it until the provider integration is configured.

**Exit:** import, startup/shutdown, discovery, read-only smoke tests, picker launch and safe callback round trips work under 4.0.10. No incidental import breakages are waived as warnings. Confirmation-dependent tools are not released yet.

### W3 — Move state out of transport connections

- Files: `apps/state.py`, `apps/schemas.py`, `apps/tools.py`, `file_uploads.py`, `apps/ui/src/mcp-app.ts`, runtime configuration and state tests.
- Mint per-view handles and return them in initial tool results; UI interactions pass the handle. Resolve and authorize every handle; reject unknown, expired and wrong-principal handles.
- Use explicit revisioned state and TTL. Back remote state with shared storage; prevent races and stale UI overwrites.
- Move local picker store/read/delete/get_file to the same stable explicit application scope. Preserve remote `upload_id`, quota, encryption, TTL and MIME checks.
- Migrate or expire old dashboard state deliberately. Do not invalidate encrypted Google grants or durable uploads merely to remove transport state.
- Document local stdio's trusted-user boundary and avoid pretending unauthenticated handle secrecy gives multitenant isolation.

**Exit:** state and uploads work across independent modern requests and two HTTP replicas; two users and two views of one user remain isolated; expiry and concurrent updates have deterministic results.

### W4 — Make confirmations and mutations resumable

- Files: shared confirmation/approval modules, all listed elicitation callers, `server.py` commit functions, affected output models and middleware.
- Introduce the modern/legacy confirmation adapter. Thread input-required results through all wrappers, tool search, and tasks.
- Configure shared continuation sealing keys and document rotation independently of Google-token and task-snapshot keys.
- Introduce durable operation records, atomic claim, result replay and reconciliation rules; retain exact bound arguments and impact preview.
- For non-idempotent sends/creates/batch updates, classify timeout or disconnect as uncertain until reconciled. Do not label repeat safety from tool name alone.
- Preserve configured confirmation policies; a UI action cannot silently disable server-required confirmation.

**Exit:** modern and legacy accept/decline/cancel/unsupported tests pass, no mutation occurs on the asking round, tampered/replayed/wrong-user continuations fail, and a lost final response cannot cause an automatic duplicate action.

### W5 — Complete remote protocol and authorization behavior

- Files: `server_http.py`, `auth/google_oauth.py`, `auth/identity.py`, `common/production.py`, `tool_discovery.py`, deployment configuration/docs.
- Add `RemoteAuthProvider` discovery, route/challenge tests and issuer/audience validation cases.
- Validate modern requests and legacy compatibility over real HTTP, including headers, Origin, progress streams, disconnects, subscriptions and unknown versions.
- Remove reliance on `ctx.reset_visibility`; make catalog/grant freshness explicit and notification claims truthful.
- Remove modern affinity requirements from readiness; retain necessary legacy infrastructure during the transition. Require shared app state and continuation keys before declaring a fleet ready.
- Use a streaming request-size limiter or bounded ASGI receive wrapper so chunked oversized bodies are rejected before buffering the entire payload. Verify it preserves streaming/disconnect behavior.
- Distinguish per-process admission limits from fleet-wide limits; use shared enforcement where the promised policy is global. Include task work in admission/drain accounting.
- Add trace propagation and redact context; preserve health and metrics access policy.

**Exit:** an authenticated round-robin two-replica test passes without modern affinity; authorization metadata is discoverable; stale catalogs cannot grant execution; protocol-level errors and tool errors are correct.

### W6 — Migrate and harden the Apps frontend

- Files: UI package/lock, production TS, host tests, Python UI metadata/resources, picker adapter, generated `dist/index.html`.
- Upgrade exact Apps/client/core packages; remove SDK 1.x imports and unused dependency. Compile host tests as well as `src`.
- Implement host capability gating, operation metadata, input/result/cancellation lifecycle, asynchronous teardown and per-view handles.
- Resolve both result-level and thrown errors; migrate request-handler APIs and typed error detection.
- Support host-mediated links/downloads, including absent capability, rejection, size limits and usable fallback. Keep `ui/download-file` draft status documented.
- Preserve theme/fonts/safe area and test resize, narrow layouts and keyboard access. Add chat buttons only through supported, user-triggered messaging.
- Normalize resource URIs; retain old aliases until regression hosts pass. Test both subserver-only and root-composed addressing.
- Rebuild from a clean lock, inspect the shipped artifact, and validate Prefab delivery under the chosen CSP with external networks blocked when bundled mode is selected.

**Exit:** full dashboard workflows and file picker pass through a policy-enforcing sandbox host, including reduced-capability hosts, task/no-task hosts and UI/no-UI fallbacks. No action depends on guessed privileges.

### W7 — Qualify the release

- Extend CI with modern and supported legacy protocol suites, shim-off Python checks, exact frontend build/tests, and golden wire-contract fixtures.
- Run all feature flags, direct subservers and root composition. Keep provider tests mocked in CI; run live Google flows only in a dedicated test account with explicitly scoped operations.
- Build/install MCPB from a clean checkout and test stdio close/reconnect/upload; rebuild Docker images and exercise TLS/proxy paths and shared backends.
- Keep lint, type checking, dependency audit, static security scan, provenance and generated-UI drift checks. Audit both the core Python packages and Prefab/UI dependency chains.
- Update README, `docs/MCPB.md`, `docs/RICH_OUTPUTS.md`, `.env.example`, `/version`, operational runbooks and examples to match behavior.

**Exit:** every required validation in section 8 passes, host/version results are recorded, and rollback has been demonstrated with test data.

### W8 — Roll out and retire obsolete paths

- Publish a candidate and canary to a bounded group; compare tool failures, confirmation completion, task age, duplicate/uncertain mutations, reconnects, upload failures and App rendering errors with baseline.
- Use versioned backend schemas and keys; maintain compatible readers during the transition. Drain old task queues before switching task protocol/worker formats. Do not let v3 and v4 workers consume the same queue without an explicit compatibility proof.
- Roll back the whole coherent release (server, locks, UI bundle, worker image, configuration) if a release gate regresses. Do not undo user mutations as part of a software rollback.
- Retain old encrypted grants/uploads and operation evidence needed for recovery. Reconnect only if an actual token/provider migration requires it.
- Retire legacy-only adapters/aliases after the supported-client policy and observed usage allow it. Record removed public behavior in release notes.

## 7. Removal and retention ledger

| Surface | Required disposition |
| --- | --- |
| Core modern `initialize`, `notifications/initialized`, `Mcp-Session-Id`, GET channel | Absent from modern protocol; let the framework keep its tested legacy adapter. |
| Modern SSE IDs / `Last-Event-ID` replay | No resume/replay assumptions. New request ID on retry; business idempotency/reconciliation is separate. |
| Core `ping`, `logging/setLevel`, roots-change notification | Do not add/use on modern paths. Existing HTTP health endpoints remain. |
| Old resource subscribe/unsubscribe | Replace with negotiated `subscriptions/listen` where needed. |
| Core experimental tasks and old `tasks/result`, `tasks/list` | New Tasks extension; no public old-method shim invented by this repository. |
| URL elicitation completion notification / `elicitationId` | Do not introduce; correlate through verified continuation/application state. |
| `fastmcp.settings.docket` | Remove; extension configuration owns the queue. |
| `ctx.sample`, `ctx.sample_step`, `ctx.list_roots` | Removed framework methods. Two sampling users exist; no roots usage found. |
| Modern `ctx.elicit` usage | Replace; retain a localized legacy branch only. |
| Python camelCase model reads | Migrate; keep camelCase wire/Google payload keys. |
| `McpError(ErrorData(...))`, custom `-32029` | Replace constructor and reserved application code. |
| Dashboard session fallback and local picker transport scope | Remove from the business state model. |
| `@modelcontextprotocol/sdk` 1.x frontend dependency | Remove once imports/tests move to split SDK 2 packages. |
| Flat `ui/resourceUri`, legacy dashboard URI | Compatibility artifacts, not proof of a current-spec violation. Retire after URI/host migration, not prematurely. |
| Apps `ui/initialize` / `ui/notifications/initialized` | **Retain**. Separate iframe protocol lifecycle. |
| Client Logging, Roots, Sampling; old HTTP+SSE; DCR | Protocol-deprecated, not all already removed. Avoid new dependencies; retain only justified compatibility. |
| Request-scoped SSE, progress, resources, prompts, annotations, structured output | **Retain** and validate. They were not removed. |
| Google Tasks tools | **Retain**. They are Google API operations, unrelated to the MCP Tasks extension. |
| Removed FastMCP proxy/OpenAPI/import_server/exclude_args/serializer aliases | No matching production usage found in the targeted scan; keep a regression scan, do not invent migration work. |

The distinction between removed and deprecated features follows the dated specification and its lifecycle policy. [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog), [feature lifecycle](https://modelcontextprotocol.io/community/feature-lifecycle).

## 8. Validation matrix and definition of done

| Suite | Required scenarios / evidence |
| --- | --- |
| Core modern, HTTP + stdio | `server/discover`; first-call `tools/list`/safe tool without initialization; required metadata; `resultType`; missing/unsupported version; malformed/unknown method. |
| HTTP boundaries | Matching/mismatched/missing routing headers; encoded names; valid/invalid/missing Origin; authentication; proxy prefix/TLS; content type; bounded chunked body. |
| Modern transport removal | No session header; no modern GET stream/replay; disconnect/cancellation behavior; no reliance on `ping`/`logging/setLevel`. |
| Legacy compatibility | Explicit 2025-11-25 and any additionally supported host version, HTTP + stdio; confirmation branch; synchronous task behavior; reconnect; legacy-only routing/affinity behavior. |
| Catalog and caching | Sorted pagination; same caller across connections/replicas; grant change/revocation; no cross-user private cache; correct `ttlMs`/`cacheScope` on every cacheable endpoint. |
| Authorization | Protected-resource metadata and real challenge link; correct issuer/audience/expiry; wrong principal; Google grant increment/revoke; no MCP token passed to Google. |
| MRTR | Accept/decline/cancel; no host capability; changed arguments; tampered/expired/wrong-user state; repeated answer; replica switch; key rotation; no side effect before confirmation. |
| Mutation recovery | Lost response after provider success; duplicate commit; simultaneous commit; timeout while thread executes; saved result replay; uncertain-outcome reconciliation. |
| Task lifecycle | Negotiation, handle/get/update/cancel/expiry; caller restoration; wrong-user handle; restart and worker replacement; encrypted snapshots; no-capability fallback; search/nested call behavior. |
| Schema and annotations | Input/output validation at the wire; snake_case-only Python; broad schemas still bounded; accurate mutation/destructive/idempotent hints; picker `_meta` and added callback. |
| State/uploads | Cross-request and cross-replica persistence; two users; two tabs/views; expired/guessed handles; update conflict; local stdio; quota/size/MIME/checksum/delete and encrypted storage. |
| Dashboard behavior | Input-first and result-first timing; push result; no tools/list; paginated/partial catalog; no tools capability; errors; cancellation/teardown; stale response; real mutation recovery. |
| Apps host policy | Separate-origin proxy, iframe sandbox, CSP deny rules, blocked undeclared requests, visibility rejection, meaningful text fallback, optional download unsupported/denied. |
| Rendering | Attribute/HTML/URL injection fixtures, sanitized email, blocked remote images, keyboard/focus, dark/light, fonts, safe area, narrow/fixed/flexible sizing. |
| Packaging | Clean npm/uv install, shipped bundle test, MCPB lifecycle, Docker non-root, both architectures, workers, readiness/drain, dependency/security checks. |

Minimum service coverage is one read, one mutation and one provider-error case per enabled integration, plus the migration-specific cases below:

| Service | Additional migration coverage |
| --- | --- |
| Gmail | Send/reply confirmation, permanent delete, batch partial errors, attachment upload/read, duplicate-send uncertainty. |
| Calendar | Explicit date/timezone behavior, create idempotency, RSVP, recurring-event prepare/commit, cancellation, attachment task. |
| Drive | Upload task, local-vs-remote file sources, public sharing/ownership confirmation, delete, export/download. |
| Sheets / Docs / Forms / Slides | Every `batch_update_*` task, output validation, timeout/replay safety, nested commit for Sheets. |
| Google Tasks / People | Confirmation-helper deletion paths, stable output schemas, unchanged API namespaces. |
| Keep / Chat | Replacement summaries, no configured provider, sensitive-content policy, all direct elicitation cases. |
| Meet | Access isolation, consequential conference actions, feature-disabled behavior. |
| Gemini | All four tasks, upload handle persistence, provider errors, long-running cancellation and charges/retry handling. |

**Release definition of done:** existing behavior is preserved or explicitly documented as changed; the complete locked Python/browser suites pass; required modern/legacy wire and sandbox-host tests pass; no shim-dependent Python field reads remain; every blocker above is resolved; state/identity and mutation replay tests pass across replicas; task/continuation secrets and queue rollout are documented; supported host versions are qualified; generated assets match source; and a coherent rollback is demonstrated. A dependency bump, green unit suite alone, or a successful `initialize` call does not satisfy this gate.

## 9. Decisions to resolve during implementation

These do not block starting W0–W2, but must be resolved before their dependent release gates:

- Which exact deployed hosts/versions require legacy core support, and when may that support retire?
- Which explicitly approved provider/configuration should replace client-sampled Chat/Keep summaries?
- Should dashboard preferences be shared across OAuth client IDs, or should each host keep an independent view?
- What are the production TTL, retention, concurrency and reconciliation policies for app state, uploads, operations and tasks?
- Is Prefab bundled delivery required in production, or is a pinned CSP-allowed renderer origin acceptable?
- Which Apps draft features are supported by the deployed hosts? Downloads can remain optional; app-provided tools are deferred by default.

Record decisions and measured host results here as implementation proceeds. Do not silently weaken confirmation, isolation, CSP, or error handling to make an individual client appear compatible.

### 9.1 Owner decisions (2026-09-26)

These supersede conflicting guidance elsewhere in this plan.

| Decision | Consequence for the work packages |
| --- | --- |
| **Keep legacy protocol connectivity.** | Clients on older core protocol versions (e.g. 2025-11-25) must still connect, through FastMCP 4's built-in compatibility path. W4's confirmation adapter keeps a thin legacy `ctx.elicit` branch so legacy clients can still confirm mutations. W7 keeps one legacy connectivity/confirmation suite. |
| **No application-level backward compatibility.** | Old storage-key migration, old dashboard URIs and the flat `ui/resourceUri` alias are dropped, not retained. W6 removes the UI aliases. W8 does not run old and new versions side by side: before cutover, drain the task queues and discard old application state. |
| **Remove everything the 2026-07-28 spec deprecates.** | No Sampling, client Logging (`ctx.info/debug/warning/...` to the client), Roots, or DCR dependencies. Server-side logging and progress remain. |
| **No summarization provider.** | The Chat `summarize_space_messages` and Keep `summarize_note` tools are removed, not stubbed. Clients summarize from the read tools themselves. |
| Apps `ui/initialize` handshake | Retained. It is part of the separate Apps protocol and is not deprecated. |

### 9.2 Implementation record (W2, 2026-09-26)

- **Summaries:** no server-side summarization provider. `chat_summarize_space_messages` and `keep_summarize_note` are removed (catalog 193 → 191 before the app-only listing change; see `docs/migration/W2_CATALOG_DIFF.md`).
- **Deprecated features:** no dependency on Sampling, Roots, legacy HTTP+SSE, or client-facing Logging (`ctx.info`/`warning`/… now go to server logs at DEBUG; progress notifications remain).
- **Legacy protocol:** legacy connectivity is kept. FastMCP 4.0.10 / SDK 2.2.0 still negotiate `initialize` for 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25 alongside 2026-07-28; `/version` reports both sets plus the tested pair (2025-11-25, 2026-07-28).
- **Confirmations:** all 24 sites use one gate (`common.async_ops.confirm_destructive_action`): legacy requests elicit; 2026-07-28 requests fail closed with a `confirmation_required` tool result and no mutation until W4 adds the MRTR branch.
- **App aliases:** the flat `ui/resourceUri` alias and legacy dashboard URI stay until W6.

### 9.3 Implementation record (W4a, 2026-09-26)

Details: `docs/migration/W4_CONFIRMATION_POLICY.md`, `docs/migration/W4_CATALOG_DIFF.md`.

- **Confirmations:** one adapter (`common/confirmation.py`) for all 24 sites. 2026-07-28 requests from clients that declared elicitation get an `InputRequiredResult` before any mutation and resume on retry after the continuation (principal, inner tool, canonical argument digest, preview digest, expiry, single-use operation id) and the answer are verified. Legacy requests keep `ctx.elicit`. No capability or unknown version still fails closed with `confirmation_required`.
- **Keys:** `MCP_REQUEST_STATE_KEYS` (shared ring, first = active) configures FastMCP `RequestStateSecurity` and the application continuation MAC; `MCP_CONFIRMATION_TTL_SECONDS` (default 600). Ephemeral per-process key otherwise; HTTP warns, multi-worker readiness fails.
- **Replay:** used operation ids in memory (stdio/tests) or Redis (`MCP_REDIS_URL`); W4b replaces this with durable operation records.
- **Tasks:** a tasked tool that asks parks in `input_required` and resumes via `tasks/update` (supported by fastmcp-tasks 4.0.10); no current confirmation site is a task tool.
- **Prepare/commit:** claim → release (asked a question, or rejected before execution) / complete (ran, or outcome uncertain). The full `prepared → awaiting_input → executing → succeeded | failed | outcome_unknown` record is W4b.
- **Bypass flags:** inventoried, unchanged; policy is an owner decision.
