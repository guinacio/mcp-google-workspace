# W0 — Frozen baseline (FastMCP 3 → 4 migration)

This is the frozen, reproducible reference for `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md`
work package **W0**. It records exactly what exists today (FastMCP 3.4.4, on
`uv.lock`, no dependency changes) so W1–W8 have a precise diff base. Everything
below was verified by actually running the commands, not inferred from the
migration plan.

## 1. Source and environment

| Item | Value |
| --- | --- |
| Baseline commit | `fba625378725aefabec88c06e8c5bfd6a9ce391e` (`Release v0.3.13 (#43)`) |
| Application version | `0.3.13` |
| Branch under which this baseline was produced | `migration/w0-baseline` |
| Python (declared) | `requires-python = ">=3.12"` (`pyproject.toml`); `.python-version` lists `3.12`, `3.11` |
| Python (used to run this baseline) | CPython `3.12.14` (uv-managed `.venv`) |
| uv | `0.12.13` |
| Node | `v24.19.0` |
| OS | Windows 11 (host), commands run via `uv run` / Git Bash |

## 2. Key locked dependency versions (`uv.lock`, unchanged)

| Package | Locked version |
| --- | --- |
| `fastmcp` | `3.4.4` |
| `mcp` | `1.28.1` |
| `pydocket` | `0.23.0` |
| `prefab-ui` | `0.20.2` |
| `pydantic` | `2.12.5` |
| `starlette` | `1.3.1` |

| Frontend package (`src/mcp_google_workspace/apps/ui/package-lock.json`) | Locked version |
| --- | --- |
| `@modelcontextprotocol/ext-apps` | `1.1.2` |
| `@modelcontextprotocol/sdk` | `1.30.1` |

No dependency or lockfile changes were made for W0. `uv sync --frozen --all-groups` installs cleanly from the committed `uv.lock` with no resolution changes.

## 3. Test / lint / type results

Commands run from the repository root of the worktree, in this order:

```
uv sync --frozen --all-groups
uv run ruff check src tests scripts
uv run mypy src/mcp_google_workspace
uv run pytest -q
```

| Check | Result |
| --- | --- |
| `uv sync --frozen --all-groups` | Succeeds, no resolution/lock changes. |
| `uv run ruff check src tests scripts` | **All checks passed.** (before and after this package's changes) |
| `uv run mypy src/mcp_google_workspace` | **Success: no issues found in 129 source files.** |
| `uv run pytest -q` (pre-existing suite only) | **216 passed** in ~20–32s (timing varies by disk cache; no failures or flakes observed across multiple repeated runs). Matches the plan's claimed 216-test baseline exactly. |
| `uv run pytest -q` (including this package's new `tests/test_catalog_contract.py`) | **218 passed** in ~25s. |

No failing or flaky tests were observed in this environment across repeated runs (the full suite was run more than five times during W0, including immediately before and after the subprocess-isolation fix below, with identical pass counts each time).

### 3.1 Subprocess bundle-test isolation (plan's W0 instruction)

The plan calls out: *"Use a dedicated environment for subprocess bundle
tests so `uv run` does not mutate an interpreter running another test
suite."* One test matched this description:

- `tests/test_bundle_runtime.py::test_bundle_stdio_lists_and_calls_prefab_file_manager`,
  via its helper `_call_picker_over_bundle_stdio` (around line 138), spawns
  `uv run src/mcp_google_workspace/bundle_entry.py` as a subprocess over
  stdio.

**This was not safe as written.** `uv run` performs a package-resolution
check ("`Checked N packages in …ms`", confirmed with `uv run -v`) against the
**same project `.venv`** that the outer `pytest` process is already running
from, on every invocation — it is not an isolated environment merely
because it runs in a subprocess. The test already used a per-test
`UV_CACHE_DIR` (`tmp_path / "uv-cache"`), which isolates uv's *download*
cache but does nothing to stop `uv run` from checking/syncing the shared
project virtual environment while the parent test process has that same
`.venv`'s modules already imported. This is a latent risk for drift/races
between the child `uv run` process and the parent interpreter (e.g. a stale
lock check acquiring the project's `.venv` lock concurrently, or the child
silently installing/upgrading a package into the venv the parent has
already loaded), not an observed failure in this environment.

**Fix applied** (`tests/test_bundle_runtime.py`, minimal, behavior-preserving):
added `--no-sync --frozen` to the child's `uv run` invocation, so the
subprocess only *executes* `bundle_entry.py` against the already-synced
environment and never re-resolves or re-syncs the shared `.venv`:

```python
args=[
    "run",
    "--no-sync",
    "--frozen",
    "src/mcp_google_workspace/bundle_entry.py",
],
```

Verified: `tests/test_bundle_runtime.py` (8 tests) passes identically before
and after the change, and the full suite count is unaffected (still 216,
now 218 with the new contract test). No other test in the suite shells out
to `uv run` or otherwise spawns a build/bundle subprocess against the
shared project environment (checked via a repo-wide search for
`subprocess`, `StdioTransport`, and `command="uv"`).

## 4. Catalog contract snapshots

`scripts/snapshot_catalog.py` builds the production composition
(`mcp_google_workspace.server.workspace_mcp`, the same object
`server_http.py`/`bundle_entry.py` serve) under two configurations and lists
its catalog through an in-memory `fastmcp.Client` — i.e. exactly what a real
MCP client observes over the protocol (`tools/list`, `resources/list`,
`resources/templates/list`, `prompts/list`), not FastMCP's private
in-process registries. No Google credentials, network access, or Redis are
required (see `tests/test_annotations_and_startup.py::test_workspace_startup_does_not_fetch_google_credentials`,
which already pins this invariant).

Committed snapshots:

- `tests/contracts/catalog_default.json` — no optional integration enabled.
- `tests/contracts/catalog_all_optional.json` — `ENABLE_APPS_DASHBOARD`,
  `ENABLE_CHAT`, `ENABLE_GEMINI` (+ `GEMINI_API_KEY`), `ENABLE_KEEP`,
  `ENABLE_MEET` all set to `true`.

### 4.1 Catalog counts — plan vs. measured

| Configuration | Tools | Resources | Resource templates | Prompts |
| --- | --- | --- | --- | --- |
| Plan claim, default | 139 | 7 | 2 | 3 |
| **Measured, default** | **139** | **7** | **2** | **3** |
| Plan claim, all-optional | 193 | 14 | 9 | 7 |
| **Measured, all-optional** | **193** | **14** | **9** | **7** |

The plan's section 2 counts are confirmed exactly, with no discrepancy.

### 4.2 Progressive discovery (BM25 `search_tools`/`call_tool`)

`mcp_google_workspace.tool_discovery.configure_tool_search` is **not**
installed on `workspace_mcp` by `server.py` itself — only the HTTP and
stdio-bundle entrypoints (`server_http.py`, `bundle_entry.py`) call it on
top of the composed server. So the plain composed server used above already
returns the complete catalog; the committed snapshots are the full catalog
with discovery disabled, as the task requires.

To also record what a client sees once discovery *is* installed, each
snapshot carries a small additional field, `discovery_view`: the sorted list
of tool names visible after explicitly forcing `MCP_TOOL_SEARCH=on` (rather
than the default `auto`, which is a client-name heuristic, not a protocol
contract, and would make the snapshot depend on `MCP_CLIENT_MODEL`) and
installing the transform on a second, independent server instance.

- Default config `discovery_view`: 15 tools (`search_tools`, `call_tool`,
  plus the 13 always-visible tools from `tool_discovery._ALWAYS_VISIBLE`
  that exist in the default composition).
- All-optional config `discovery_view`: 16 tools (adds `apps_get_dashboard`,
  which `configure_tool_search` only appends to `always_visible` when Apps
  is enabled).

### 4.3 Determinism / no user data or credentials

Verified, not assumed:

- Running `scripts/snapshot_catalog.py` twice in a row produces
  byte-identical output (`diff` clean) for both files.
- Grepped both snapshot files for the local username, home directory,
  absolute Windows paths, email addresses, and ISO-8601 timestamps. The only
  date-like strings found (`2026-03-01T00:00:00Z`, `2026-01-01T00:00:00Z`)
  are **hardcoded documentation examples** in tool parameter descriptions
  (`calendar/schemas.py`, `calendar/tools.py`, `forms/tools.py`), not
  computed from the current date — confirmed by reading the source, not
  just the grep hit.
- Hashed Apps/Prefab addresses (`ui://prefab/tool/<hash>/...`,
  `<hash>_store_files`) are a plain SHA-256 of two static strings
  (`fastmcp.server.providers.addressing.hash_tool`), so they are stable
  across machines and runs, not random per-process ids.
- **Nothing needed to be stripped or normalized.** Tool/resource/prompt
  *definitions* (names, schemas, descriptions, annotations, `_meta`) are
  static at decorator time in this codebase; the only dynamic values found
  by source review (`datetime.now()`, `uuid4()`, `secrets.token_*`,
  `time.time()`) live in *runtime call results* (pagination's `fetched_at`,
  upload ids, dashboard `session_id`/`generated_at_utc`), which the snapshot
  script never touches — it only calls the `*/list` protocol methods, never
  `call_tool`/`read_resource`.

### 4.4 Regenerating the snapshots

```
uv run python scripts/snapshot_catalog.py
```

or, equivalently, via the contract test itself:

```
UPDATE_CATALOG_SNAPSHOTS=1 uv run pytest tests/test_catalog_contract.py
```

`tests/test_catalog_contract.py` regenerates the catalog in-process (same
`tests/contracts/catalog_snapshot.py` code as the script) and diffs it
against the committed JSON on every normal test run; a mismatch prints a
unified diff of the exact changed section. It is deterministic, takes under
6 seconds for both configurations together, and requires no network, Redis,
or Google credentials.

## 5. FastMCP-3-only / removal-ledger surface inventory (section 7 of the plan)

Exhaustive, file:line-level inventory for W2/W4. All line numbers were read
directly from the source at commit `fba6253` (plus the one test-only change
in section 3.1 above); none are inferred from the plan document.

### 5.1 Elicitation (MRTR / confirmation) — 14 direct `.elicit()` sites

| # | File:line | Enclosing tool/function | Via shared helper? |
| --- | --- | --- | --- |
| 1 | `common/async_ops.py:82` | `confirm_destructive_action()` (the shared helper itself) | — (this *is* the helper) |
| 2 | `calendar/tools.py:1190` | `delete_event` | direct `confirm_ctx.elicit` inline |
| 3 | `chat/tools.py:204` | `create_message` | direct `ctx.elicit` |
| 4 | `chat/tools.py:218` | `delete_message` | direct `ctx.elicit` |
| 5 | `chat/tools.py:247` | `post_message_simple` | direct `ctx.elicit` |
| 6 | `chat/tools.py:267` | `reply_to_message` | direct `ctx.elicit` |
| 7 | `drive/tools/files.py:618` | `delete_file` | direct `confirm_ctx.elicit` inline |
| 8 | `gmail/tools/batch.py:63` | `batch_delete` | direct `confirm_ctx.elicit` inline |
| 9 | `gmail/tools/messages.py:194` | `send_email` | direct `confirm_ctx.elicit` inline |
| 10 | `gmail/tools/messages.py:296` | `_send_reply` | direct `confirm_ctx.elicit` inline |
| 11 | `gmail/tools/messages.py:545` | `delete_email` | direct `confirm_ctx.elicit` inline |
| 12 | `gmail/tools/threads.py:196` | `delete_thread` | direct `confirm_ctx.elicit` inline |
| 13 | `keep/tools.py:95` | `create_note` | direct `ctx.elicit` |
| 14 | `keep/tools.py:187` | `delete_note` | direct `ctx.elicit` |

Confirms the plan's count of 14 exactly. Note that "via shared helper" here
means the site calls `.elicit()` itself with an inline confirmation preview
(a `confirm_ctx.elicit(...)` pattern duplicated per call site, not a call
*into* `confirm_destructive_action`); only row 1 is the actual shared helper
function.

### 5.2 Shared confirmation helper — 10 additional callers

`common/async_ops.py:75` defines `confirm_destructive_action(ctx, action_name, message)`,
which itself makes elicit site #1 above. These 10 sites call that helper
(none of them call `.elicit()` directly, so they are *not* in the 14 above):

| # | File:line | Tool |
| --- | --- | --- |
| 1 | `calendar/tools.py:1037` | `remove_event_attachment` |
| 2 | `drive/tools/permissions.py:146` | `create_permission` |
| 3 | `drive/tools/permissions.py:234` | `update_permission` |
| 4 | `drive/tools/permissions.py:291` | `delete_permission` |
| 5 | `gmail/tools/drafts.py:243` | `delete_draft` |
| 6 | `gmail/tools/filters.py:66` | `delete_filter` |
| 7 | `gmail/tools/labels.py:120` | `delete_label` |
| 8 | `gmail/tools/settings.py:69` | `delete_forwarding_address` |
| 9 | `people/tools.py:333` | `delete_contact` |
| 10 | `tasks/tools.py:424` | `delete_task` |

Confirms the plan's count of 10 exactly. Total confirmation-related call
sites requiring migration to the modern/legacy adapter (W4): **24** (14 + 10).

### 5.3 `ctx.sample` (removed; Core Sampling deprecated)

| File:line | Context |
| --- | --- |
| `chat/server.py:32` | `summarize_space_messages` tool |
| `keep/server.py:34` | `summarize_note` tool |

Exactly 2, matching the plan. No `ctx.sample_step` or `ctx.list_roots` usage found anywhere in `src/`.

### 5.4 `McpError(ErrorData(...))` positional constructor

| File:line | Context |
| --- | --- |
| `common/errors.py:152-156` | `StructuredToolErrorMiddleware.on_call_tool`, re-raising every uncaught tool exception as one structured JSON-RPC error |

Exactly one call site (spans multiple lines: `raise McpError(\n    ErrorData(\n        code=rpc_code,\n        message=...,\n    )\n)`), matching the plan's claim that the audit reproduced this `TypeError` independently. This is the single highest-blast-radius fix in the ledger — every tool-level error in the server goes through it.

### 5.5 Reserved `-32029` rate-limit code

| File:line | Context |
| --- | --- |
| `common/errors.py:63` | `rpc_code = -32029 if code == "rate_limited" else -32000` (explicit `error_code` on a raised exception) |
| `common/errors.py:80` | `code, rpc_code, retryable = "rate_limited", -32029, True` (HTTP 429 / "rate limit" message heuristic) |

Both in `_error_envelope()`. Both must move to an application code in `-32000..-32019` or a `rate_limited` structured-error string per the plan.

### 5.6 `fastmcp.settings.docket`

| File:line | Context |
| --- | --- |
| `server_http.py:41` | `fastmcp.settings.docket.url = redis_url` (only when `MCP_REDIS_URL` is set and `FASTMCP_DOCKET_URL` is not) |

Exactly one site, matching the plan. Absent in FastMCP 4.0.10; must move to explicit `TasksExtension` configuration (or its documented environment defaults) per W2.

### 5.7 `ctx.reset_visibility`

| File:line | Context |
| --- | --- |
| `auth/google_oauth.py:247` | `refresh_workspace_catalog` tool, called right before returning a `"notification_sent": "tools/list_changed"` claim |

Exactly one site, matching the plan. Per plan section 3.1/W5, this must become explicit per-request grant/catalog freshness rather than a connection-visibility reset, and the tool's `notification_sent` claim must not promise a notification the server does not actually deliver.

### 5.8 Python SDK camelCase-vs-snake_case attribute reads

| File:line | Context |
| --- | --- |
| `common/component_annotations.py:336` | `existing_meta = getattr(current, "_meta", None)` |
| `common/component_annotations.py:338` | `setattr(annotations, "_meta", existing_meta)` |

Matches the plan's pointer to `component_annotations.py:331` (within a few lines — the plan's audit line number and this file's current line number differ slightly, but it is the same two statements in `apply_default_tool_annotations`'s per-tool annotation-merge helper). No other camelCase (`.inputSchema`, `.outputSchema`, `.nextCursor`, `.isError`, `.structuredContent`) attribute reads were found anywhere in `src/mcp_google_workspace` — those only appear as JSON Schema dict keys (wire format, correctly left camelCase) or inside test files reading raw `mcp.types` protocol objects (also correctly camelCase, since that's the wire attribute name in SDK 1.x/pydantic-aliased fields).

### 5.9 Removed FastMCP proxy/OpenAPI/`import_server`/`exclude_args`/serializer aliases

Searched `src/mcp_google_workspace` for `import_server`, `as_proxy`, `exclude_args`, `FastMCPOpenAPI`, `from_openapi`, `OpenAPITool`, and a custom `.serializer(` call: **no matches**. Confirms the plan's claim that no production usage exists; no migration work needed here beyond the regression scan the plan already recommends keeping.

## 6. Host compatibility matrix

Not measured in this environment — no live hosts were qualified as part of
W0 (the plan explicitly notes "Local mock-host results are not
substitutes"). Left for the release owner to fill in per host/version
during W7/W8 qualification.

| Host | Version | Modern core MCP | Stable Apps | Elicitation | Tasks | Downloads | Sandboxed UI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Desktop | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Claude.ai | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Claude Code | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| VS Code | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| ChatGPT | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| MCP Inspector | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## 7. Files added/changed for W0

- `tests/test_bundle_runtime.py` — isolate the one subprocess `uv run` bundle test (section 3.1).
- `scripts/snapshot_catalog.py` — regenerates the two committed catalog snapshots.
- `tests/contracts/__init__.py`, `tests/contracts/catalog_snapshot.py` — shared snapshot-building logic (imported by both the script and the contract test).
- `tests/contracts/catalog_default.json`, `tests/contracts/catalog_all_optional.json` — committed catalog contract snapshots.
- `tests/test_catalog_contract.py` — regenerates the catalog in-process and diffs it against the committed snapshots on every test run.
- `docs/migration/W0_BASELINE.md` — this document.

## 8. Risks / open items for W2+

- The catalog snapshot's `discovery_view` pins `MCP_TOOL_SEARCH=on` rather
  than exercising the `auto` heuristic end-to-end for a real Claude client
  name; W5's catalog-freshness work should add a client-name-driven variant
  once the modern per-request authorization/catalog-filtering design lands,
  since `auto` mode is itself in scope for change (plan section 3.1).
  Fixed to `MCP_TOOL_SEARCH=on` for now.
- This baseline does not exercise the frontend/TypeScript suite, MCPB
  packaging, or Docker build — W0's scope here was the Python contracts and
  the 216→218-test suite; W1/W6/W7 own those surfaces.
- `docs/MIGRATION_AUDIT_2026-09-26.md` referenced from the plan's summary
  paragraph was not located in this checkout (only
  `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` and `docs/QA_TODO.md` /
  `docs/RICH_OUTPUTS.md` / `docs/MCPB.md` exist under `docs/`); if that
  audit document exists elsewhere it should be linked here, otherwise the
  plan's cross-reference should be corrected.
