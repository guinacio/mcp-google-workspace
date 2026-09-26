# Migration audit evidence — 2026-09-26

This is the evidence companion to the [migration plan](MIGRATION_FASTMCP4_MCP_2026-07-28.md). It separates the original FastMCP 3 audit from the implementation that appeared in the shared checkout while the audit was paused. It is not a certification of a deployed server or host.

## 1. Revisions, scope and confidence

| Reference | Meaning |
| --- | --- |
| `fba625378725aefabec88c06e8c5bfd6a9ce391e` | Original audit baseline, application 0.3.13, FastMCP 3.4.4. All historical file:line references below refer to this revision. |
| `173208ded63e79f91cde3dfa413e75167f4b5d60` | Checkout inspected when work resumed; W0, W1 and W2 already merged. |
| Research date | 2026-09-26. Package versions and upstream draft content are time-sensitive. |
| Directly exercised | Clean locked baseline tests, isolated dependency-upgrade tests, catalog enumeration, synthetic framework HTTP requests, picker/result inspection, TypeScript checks and local browser tests. |
| Source-reviewed | Server composition, enabled integrations, schemas/annotations, auth/identity/Google consent, storage, middleware, approvals, task declarations, search, Apps resources/frontend/host fixtures, CI and packaging. |
| Not exercised | Live Google account operations, production IdP/proxy/TLS, distributed Redis/S3 workers, cross-replica traffic, real supported desktop/web hosts, deployment or rollback. |

The audit used no live mail, calendar data or Google mutations. The rendering probe used a harmless synthetic filename. The resumed work preserves the implementation already merged by other work and changes documentation only; it does not claim authorship of W0–W2.

Evidence labels used below:

- **Reproduced:** observed in a local executable experiment.
- **Source-confirmed:** found in this repository or the installed/pinned upstream implementation.
- **Qualification gap:** required behavior has not been established by the existing tests; this alone is not proof of a protocol violation.

## 2. Version authority

| Component | Verified target | Source / qualification |
| --- | --- | --- |
| FastMCP | 4.0.10, released September 25 | [Release tag](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.0.10), [PyPI metadata](https://pypi.org/pypi/fastmcp/json). Latest at research time. |
| Python SDK | `mcp` / `mcp-types` 2.2.0 | Resolved in the isolated FastMCP 4 environment and subsequently locked by W2. This records the tested combination, not an independently required protocol version. |
| MCP core | 2026-07-28 | [Dated changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog), [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http), [MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr). |
| MCP Apps SDK | 2.0.3 | [Release](https://github.com/modelcontextprotocol/ext-apps/releases/tag/v2.0.3), [npm metadata](https://registry.npmjs.org/@modelcontextprotocol/ext-apps). `latest` was 2.0.0 while `release-2.0` was 2.0.3; select an explicit version. |
| TypeScript client/core | 2.1.0 | [Client metadata](https://registry.npmjs.org/@modelcontextprotocol/client), [core metadata](https://registry.npmjs.org/@modelcontextprotocol/core). Split SDK dependency migration; Node >=20, already compatible with CI Node 24. |
| Apps stable wire protocol | 2026-01-26 | [Upstream version table](https://github.com/modelcontextprotocol/ext-apps#specification), [dated specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx). This date is independent of core MCP. |
| Apps newer optional behavior | Draft, not a new dated stable protocol | [Draft specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx). `ui/download-file` and its host capability are draft features; capability negotiation remains necessary. |

Context7 was available and used. Its indexed examples included older FastMCP material. Direct registry responses, tagged 4.0.10 code and dated specifications took precedence over those examples. Apps draft URLs track a moving branch; archive the chosen draft revision when implementing any draft-only behavior.

## 3. Tests and experiments

### 3.1 Original baseline and upgrade experiment

| Experiment | Result | What it establishes / limits |
| --- | --- | --- |
| Clean Python 3.12.14 environment installed from baseline `uv.lock` | **216 passed**, 38.07 seconds | Reproducible original unit/integration baseline with mocked providers. |
| Isolated Python 3.14.7 environment with original source and `fastmcp[apps,tasks]==4.0.10` | **192 passed, 24 failed**, 30.64 seconds | A dependency-only upgrade is insufficient. Other dependencies and Python also differed, so this is a diagnostic experiment, not a controlled attribution of every failure to FastMCP. |
| Baseline frontend `tsc --noEmit` | Passed | Production `src` type checking only; the tsconfig excludes the test host. |
| Baseline `npm test` | **7 passed**, 10.2 seconds | Existing mock AppBridge discovery, pushed-data and theme behavior. Tests used the committed UI bundle and a permissive local iframe. |
| Temporary UI copy upgraded to Apps 2.0.3 / client/core 2.1.0 / Zod 4 | Old SDK import failed compilation; changing the production schema import to core made `tsc --noEmit` pass | The dependency/import path change is necessary. No SDK 2 browser runtime or excluded test-host type check was performed. |
| Benign HTML attribute probe using the original `esc()` implementation | A second attribute was parsed from `report" data-audit-injected="yes` | Demonstrates unsafe attribute interpolation. No active script or live user content was used. W1 later replaced the vulnerable implementation. |

An earlier run in the pre-existing local Python environment produced six failures, including missing installed package data/metadata. The clean locked run above replaced it as the baseline; those six were not classified as repository defects. Bundle tests originally spawned `uv run` against the active environment, creating a synchronization risk; W0 now uses `--no-sync --frozen` for that child.

The 24 isolated upgrade failures are **not 24 independent defects**. Many clients failed at startup because task-enabled servers lacked the newly required extension. Others exercised changed dashboard session behavior, picker metadata/schema, the error constructor, or the deliberately version-specific test assertion.

Historical full-suite failure locations:

| Test module | Failed cases |
| --- | ---: |
| `tests/apps/test_mcp_server.py` | 1 |
| `tests/test_annotations_and_startup.py` | 3 |
| `tests/test_bundle_runtime.py` | 2 |
| `tests/test_calendar_tools.py` | 3 |
| `tests/test_file_uploads.py` | 3 |
| `tests/test_gmail_tools.py` | 7 |
| `tests/test_output_schema_inference.py` | 2 |
| `tests/test_production_features.py` | 2 |
| `tests/test_sheets_tools.py` | 1 |

These historical results must not be presented as failures of the current W2 checkout.

### 3.2 Focused framework and provider probes

The minimal HTTP experiment used FastMCP 4.0.10 / SDK 2.2.0 with an in-process ASGI transport and no live external identity provider. It deliberately retained `stateless_http=False` and `json_response=True`.

- Modern `server/discover` returned HTTP 200, supported version `2026-07-28`, server metadata and `resultType: "complete"`.
- Modern `tools/list` returned HTTP 200, `ttlMs: 0`, `cacheScope: "private"` and no MCP session header.
- A modern GET returned HTTP 405.

This confirms the framework can supply the modern behavior and that the old configuration arguments are not themselves removed. It does **not** establish correct request forwarding, JWT discovery, CORS, headers or streaming through this application's production proxy.

With the original application imported into the isolated v4 process, startup failed until a `TasksExtension` was added in memory. No production file was edited for this probe. Afterwards:

- The default raw catalog listed 140 tools, adding `files_store_files` to the old 139.
- The main picker advertised nested `ui.resourceUri` and model visibility; the added storage callback advertised app-only visibility.
- The renderer resource resolved with the standard Apps MIME type and a CSP resource-domain allowance for `https://cdn.jsdelivr.net`.
- Calling the picker failed its custom closed output schema: the framework's added `_meta` was rejected as an unexpected property. W2 now accepts the exact `_meta.fastmcp.toolNames` envelope.

Installed-code inspection also confirmed that `ctx.sample` and `fastmcp.settings.docket` are absent, while `ctx.elicit`, `ctx.reset_visibility`, `ctx.session_id` and `mcp.types.LATEST_PROTOCOL_VERSION` still exist. Existence does not imply modern semantic suitability: imperative elicitation is legacy-only, and a transport-derived session ID is not durable application state.

### 3.3 Current checkout verification

At `173208d`, the resumed audit ran:

```powershell
# Repository root; use the already synchronized W2 environment.
uv run --no-sync --frozen python -m pytest -q

# src/mcp_google_workspace/apps/ui
npm ci --ignore-scripts --no-audit --no-fund
npx tsc --noEmit
npm test
```

The Python 3.12.14 suite passed **316 tests in 34.63 seconds**, using FastMCP / fastmcp-tasks 4.0.10 and mcp / mcp-types 2.2.0. `tests/conftest.py` disables FastMCP's camelCase compatibility before importing the framework. Production TypeScript checking passed. After dependency synchronization, **all 13 browser tests passed in 11.2 seconds**, including the six added security cases.

The first browser attempt, before installing the committed lock, had 12 passes and one failure: the development preview could not resolve the newly added DOMPurify dependency in stale `node_modules`. The lock already declared the package. `npm ci` corrected the environment without changing the manifest or lock; the result after synchronization is the authoritative browser result.

These checks are not a production release qualification. They do not exercise MRTR completion, distributed state, a real sandbox proxy or actual supported hosts. The current modern confirmation gate intentionally fails closed until W4.

## 4. Repository findings and current disposition

Paths in this table are relative to `src/mcp_google_workspace/` unless identified as tests. Historical lines belong to `fba6253`; use the linked commit, not the current shifted line numbers, when reproducing the original finding.

| ID | Evidence and impact | Current disposition / work package |
| --- | --- | --- |
| A01 | **Reproduced:** task-enabled root/direct subservers refuse v4 startup without `TasksExtension`; `server_http.py:41` writes a removed Docket setting. | W2 adds shared bootstrap in `common/task_backend.py`, root registration, worker entrypoint and direct-subserver test setup. Distributed identity/recovery qualification remains W5/W7. |
| A02 | **Source-confirmed and tested:** 14 `.elicit()` locations, including the helper, plus 10 helper callers. Modern protocol cannot use imperative server requests. | W2 consolidates confirmations into a legacy-aware gate. W4 must return and resume input-required results; modern confirmation-dependent operations are currently unavailable, safely. |
| A03 | **Source-confirmed:** `common/approvals.py` consumes/deletes an approval token before the nested `server.py` commit finishes. | Outstanding W4: durable operation record, atomic claim, saved-result replay and uncertain-outcome reconciliation. |
| A04 | **Reproduced/source-confirmed:** dashboard tests expecting connection-state persistence regress; `apps/state.py` uses a process-local map, while local `file_uploads.py` keys by `ctx.session_id`. | Outstanding W3: explicit authenticated handles, shared remote store, TTL/revision rules and a trusted local scope. |
| A05 | **Reproduced:** picker `_meta` rejects against the application's old closed schema; added callback lacks the application's published bounds/descriptions. | W2 addresses both with a narrow schema adapter. Renderer-in-sandbox and stable upload scope remain W3/W6. |
| A06 | **Reproduced:** `common/errors.py:152` passes `ErrorData` positionally to the new `McpError`. Historical `-32029` allocation conflicts with the new reserved range. | W2 changes constructor/allocation. Consistent business `isError`, partial-success semantics and UI recovery still need W4–W6 review. |
| A07 | **Source-confirmed:** annotation Python reads use deprecated camelCase at `common/component_annotations.py:331–334`. | W2 migrates reads and runs tests with compatibility disabled. Wire and Google dictionary keys remain camelCase as appropriate. |
| A08 | **Source-confirmed:** `chat/server.py:32`, `keep/server.py:34` call removed `ctx.sample()`. | W2 removes both summary tools under the recorded owner decision. Read tools and summary prompts remain; no replacement model provider is planned. |
| A09 | **Source-confirmed:** `auth/google_oauth.py:247` resets visibility and claims a notification; state is not reliable modern grant/catalog invalidation. | Outstanding W5; current code explicitly marks the debt. Auth-based filtering itself is permitted by the specification. |
| A10 | **Source-confirmed:** `server_http.py` supplies a bare `JWTVerifier`; verifier-only auth has no protected-resource metadata routes. | Outstanding W5: wrap with correctly configured remote auth discovery and verify real challenge/resource URLs. Google OAuth remains a separate grant flow. |
| A11 | **Source-confirmed:** readiness requires transport affinity for replicas, request-size middleware buffers the body before measuring it, admission limits are process-local. | Outstanding W5; replace affinity assumptions for modern traffic, bound streamed input, document/enforce actual fleet limits. |
| A12 | **Source-confirmed:** blocking Google calls use `abandon_on_cancel=True`; cancelling the wait cannot undo the provider call. | Outstanding W4/W5: outcome tracking and provider-aware reconciliation before retrying non-idempotent mutations. |
| A13 | **Reproduced:** original `apps/ui/src/render.ts:14` escapes text but not quotes used in attributes; custom email reconstruction reused it. | W1 replaces the mechanism with escaped templates, DOMPurify and URL checks. Retain security regression tests in W6. |
| A14 | **Source-confirmed:** original standalone bridge used wildcard messaging without sender validation; direct conference links bypassed host mediation. | W1 restricts development sender/origin, removes the standalone path from production, and mediates links. Reduced-capability host behavior remains W6. |
| A15 | **Source-confirmed:** UI calls `downloadFile` without host capability checks; discovery guesses all operation names after absent/partial listing. | Outstanding W6: capability negotiation, explicit server operation manifest, typed errors and meaningful alternatives. Draft feature absence is not itself a host defect. |
| A16 | **Source-confirmed:** UI lacks invocation-input/cancellation handlers and effective teardown; browser-minted reusable localStorage ID governs views. | Outstanding W3/W6: server-issued per-view handle, authoritative input/result lifecycle, cancellation and stale-response suppression. |
| A17 | **Qualification gap:** test host uses a same-origin iframe and fake tool results; production tsconfig excludes test host. | Outstanding W6/W7: type-check host fixtures, different-origin sandbox/CSP policy tests, actual host versions. |
| A18 | **Source-confirmed:** canonical/legacy dashboard resource declarations interact with the root `apps` namespace, so an alias currently supplies the advertised address. | Outstanding W6: normalize the canonical URI and remove the old alias atomically, per owner decision; assert root and subserver resolution. |
| A19 | **Source-confirmed:** task snapshots may contain caller credentials; current W2 shared-queue setup warns when no encryption key exists. | Require encrypted production snapshots and enforce the production readiness policy in W5/W7; task arguments/results require separate protection. Never call a warning a fail-closed control. |

[Browse the original audited source](https://github.com/guinacio/mcp-google-workspace/tree/fba625378725aefabec88c06e8c5bfd6a9ce391e/src/mcp_google_workspace). The [W0 inventory](migration/W0_BASELINE.md) lists the individual confirmation sites; the [W2 diff](migration/W2_CATALOG_DIFF.md) records intentional catalog changes. This companion does not replace those implementation records.

## 5. Task and catalog coverage

The original `task=True` inventory contains nine default tools and four optional Gemini tools. These are MCP background executions, not the Google Tasks namespace:

| Tool | Historical declaration |
| --- | --- |
| `calendar_download_event_attachment` | `calendar/tools.py:1072` |
| `docs_batch_update_document` | `docs/tools.py:169` |
| `forms_batch_update_form` | `forms/tools.py:151` |
| `sheets_batch_update_spreadsheet` | `sheets/tools.py:276` |
| `slides_batch_update_presentation` | `slides/tools.py:183` |
| `gmail_download_attachment` | `gmail/tools/attachments.py:52` |
| `drive_upload_file` | `drive/tools/files.py:264` |
| `drive_download_file` | `drive/tools/files.py:635` |
| `drive_export_file` | `drive/tools/files.py:674` |
| `gemini_generate_image` | `gemini/tools.py:257` |
| `gemini_edit_image` | `gemini/tools.py:287` |
| `gemini_describe_video` | `gemini/tools.py:331` |
| `gemini_analyze_audio` | `gemini/tools.py:368` |

Baseline service namespaces are Gmail, Calendar, Drive, Sheets, Docs, Google Tasks, People, Forms and Slides, with optional Apps, Chat, Gemini, Keep and Meet. Root authorization/capability/approval tools and the file provider are included in the totals.

| Configuration | Baseline tools/resources/templates/prompts | W2 tools/resources/templates/prompts |
| --- | --- | --- |
| Default | 139 / 7 / 2 / 3 | 140 / 7 / 2 / 3 |
| All optional | 193 / 14 / 9 / 7 | 192 / 14 / 9 / 7 |

The new app-only callback is intentionally listed in the raw catalog and must be filtered by a conforming host before model exposure. Visibility metadata is not a replacement for authorization. W2's forced-BM25 discovery snapshots remain 15 default / 16 all-optional visible entries. This does not prove every host routes hidden callbacks or task invocations correctly.

## 6. Interpretation guardrails

- The removal of core initialization does **not** remove the separate Apps `ui/initialize` handshake.
- Logging, Roots, Sampling, old HTTP+SSE and DCR are protocol-deprecated. The repository owner chose to remove application dependencies on them; that policy is stricter than saying the protocol already deleted every deprecated method.
- FastMCP's removed Python sampling API is a concrete library break even while protocol Sampling is merely deprecated.
- `stateless_http=False`, `json_response=True`, `ctx.session_id` and `ctx.reset_visibility` still exist; their presence alone is not a conformance failure. Application reliance on their old semantics must be addressed.
- Authorization-dependent catalogs are allowed. Connection-history-dependent catalogs and caches shared across principals are the concern.
- Missing optional Apps CSP metadata invokes restrictive host defaults; it is not automatically a violation. The Prefab resource already supplies CSP metadata and must not be described as missing it.
- Downloads exposed by an SDK are not automatically stable wire features or supported by every host.
- No removed proxy/OpenAPI/import-server/serializer API usage was found in the targeted production scan; the plan does not invent a rewrite of those components.
- Passing 316 Python tests and local browser fixtures does not establish full core MCP or Apps conformance. The outstanding release gates in the plan remain necessary.

## 7. Reproduction and evidence retention

The original experiment environments and fetched upstream documents are under the local temporary directory `mcp-google-workspace-migration-20260926`. The important outcomes are recorded here because that directory is not a portable or durable project artifact. Full historical outputs were saved as `baseline-pytest.txt` and `v4-pytest.txt` there.

To reproduce the historical comparison, obtain a separate clean checkout of `fba6253`; do not run the baseline commands against the current upgraded source. Use one dedicated environment for the frozen lock on Python 3.12 and another for the diagnostic upgrade. Set `UV_PROJECT_ENVIRONMENT` to the corresponding environment, and disable synchronization in bundle-test subprocesses so they cannot replace packages in the active interpreter. Repeating with the same Python and minimally changed lock is preferable for causal attribution.

For release qualification, save exact server/client/Apps package locks, wire fixtures, host names/versions, sanitized test logs and backend configuration fingerprints. Do not store Google data, authorization headers, keys or continuation secrets in evidence artifacts. Record actual results against each required gate rather than treating a checklist as a completed test.
