# mcp-google-workspace

Production-oriented Google Workspace MCP package with:

- Gmail MCP: send/read/search emails, attachment handling, label management, batch operations.
- Google Calendar MCP: events, availability, create/update/delete operations.
- Google Drive MCP: files/folders CRUD, uploads/downloads/exports, sharing permissions, Shared Drives operations.
- Google Sheets MCP: spreadsheet metadata, values reads/writes, and raw batch updates.
- Google Docs MCP: document fetch/create flows plus convenience text mutations and raw batch updates.
- Google Tasks MCP: task lists, tasks, completion, movement, and deletion.
- Google People MCP: personal contacts and contact groups.
- Google Forms MCP: forms CRUD, publish settings, and response reads.
- Google Slides MCP: presentations, slide pages, thumbnails, text replacement, and raw batch updates.
- MCP Apps Dashboard: workspace dashboard app-layer tools/resources with interactive UI.
- Optional Google Keep MCP, Google Chat MCP, Google Meet MCP, and Gemini media integrations behind feature flags.
- FastMCP advanced features: Context logging, progress updates, user elicitation, sampling, resources, and prompts.
- Composed server architecture: Gmail + Calendar + Drive + Sheets + Docs + Tasks + People + Forms + Slides mounted by default, with optional Apps/Keep/Chat/Meet/Gemini namespaces.

## Requirements

- Python 3.12+
- UV package manager
- Node.js 18+ and npm (required for MCP Apps UI in `src/mcp_google_workspace/apps/ui`)
- Google Cloud OAuth desktop credentials (`credentials.json`)
- Google APIs enabled in your Google Cloud project: Gmail, Calendar, Drive, Sheets, Docs, Tasks, People, Forms, and Slides
- Optional APIs when enabling feature-flagged integrations: Google Keep, Google Chat, and Google Meet
- Gemini Developer API key when enabling Gemini media tools

## Installation

```powershell
uv sync --all-extras --dev
```

If you are working on MCP Apps UI, install frontend dependencies and build the bundle:

```powershell
cd src/mcp_google_workspace/apps/ui
npm ci
npm run build
```

## OAuth setup

Place the Google OAuth client `credentials.json` in one of:

- project root: `./credentials.json`
- package credentials folder: `./src/credentials/credentials.json`

Configure a versioned Fernet key ring before first use. Production deployments should mount a secret-manager document through `MCP_SECRET_FILE`; `MCP_TOKEN_ENCRYPTION_KEY` remains a single-key development option. The MCP encrypts each user's refresh token separately and never writes a shared `token.json`.

Generate a key once and store it in your secret manager:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Mounted secret document:

```json
{
  "active_token_encryption_key_id": "2026-07",
  "token_encryption_keys": {
    "2026-07": "<active Fernet key>",
    "2026-04": "<retained previous Fernet key>"
  }
}
```

Reads accept retained keys and rewrite ciphertext with the active key. Remove an old key only after a rotation/restore drill confirms all durable records have been rewritten.

### Optional service feature flags

Sheets, Docs, Tasks, People, Forms, and Slides are mounted by default and their scopes are always requested.
When scopes change, reconnect the affected user so their encrypted per-user token receives the expanded grant.

Google Keep OAuth scope can return `invalid_scope` in standard user OAuth flows.
Keep integration is therefore disabled by default.

Enable Keep when your Google Workspace setup supports it:

```powershell
$env:ENABLE_KEEP="true"
```

Google Chat OAuth scopes also commonly require Google Workspace accounts.
Chat integration is therefore disabled by default.

Enable Chat when your Google Workspace setup supports it:

```powershell
$env:ENABLE_CHAT="true"
```

Google Meet integration is also disabled by default.
Enable it only after enabling the Meet API for the same OAuth client:

```powershell
$env:ENABLE_MEET="true"
```

Gemini media integration is also disabled by default.
Enable it with a Gemini Developer API key:

```powershell
$env:ENABLE_GEMINI="true"
$env:GEMINI_API_KEY="your-api-key"
```

Capability-specific Gemini model defaults:

```powershell
$env:GEMINI_IMAGE_GENERATE_MODEL="gemini-3.1-flash-image-preview"
$env:GEMINI_IMAGE_EDIT_MODEL="gemini-3.1-flash-image-preview"
$env:GEMINI_VIDEO_UNDERSTANDING_MODEL="gemini-3-flash-preview"
$env:GEMINI_AUDIO_UNDERSTANDING_MODEL="gemini-3-flash-preview"
$env:GEMINI_REASONING_MODEL="gemini-3.1-pro-preview"
```

Whenever you enable an optional integration or otherwise change scopes, reconnect each affected user.

### Apps dashboard rollout flag

The MCP app-layer dashboard namespace is opt-in for controlled rollout.

Enable apps namespace:

```powershell
$env:ENABLE_APPS_DASHBOARD="true"
```

## Run (STDIO)

```powershell
uv run python -m mcp_google_workspace
```

## MCP Bundle (MCPB)

This repository now includes a native `uv`-based MCP Bundle manifest and packaging assets.

Install on Claude Desktop:

1. Download the latest `.mcpb` from [GitHub Releases](https://github.com/guinacio/mcp-google-workspace/releases/latest).
2. In Claude Desktop, open the MCP bundle install flow.
3. Select the downloaded `mcp-google-workspace-*.mcpb` file.
4. Choose the credentials directory that contains `credentials.json`, or leave it empty to use the repo defaults.
5. Enable optional integrations only if your Google Workspace account and OAuth client support their scopes.
6. Finish the install and authenticate in the browser on first launch.

Gemini media tools are API-key-based rather than OAuth-based. If you enable Gemini in the bundle UI, also set the Gemini API key and optional model defaults there.

Build a local `.mcpb` archive only if you are developing or testing bundle changes:

```powershell
uv run python scripts/build_mcpb.py
```

The packaging command runs `npm ci` and rebuilds the Apps UI from the exact
frontend lock before creating the archive. It fails instead of packaging a
stale generated UI.

Note: Claude Desktop currently rejects extra `server` metadata keys such as `package_manager`, `python_version`, and `working_dir`, so this bundle keeps the `uv` server block to the manifest fields Claude accepts.

Bundle-specific documentation, runtime settings, and validation steps live in `docs/MCPB.md`.

## Run (Streamable HTTP)

The remote server uses session-aware MCP Streamable HTTP and requires an OIDC bearer-token issuer. Session mode enables progress, cancellation, and `tools/list_changed` notifications; durable long-running work remains Redis-backed. The server refuses to start without this configuration:

```powershell
$env:MCP_HOST="0.0.0.0"
$env:MCP_PORT="8000"
$env:MCP_HTTP_BASE_URL="https://mcp.example.com"
$env:MCP_HTTP_JWT_ISSUER="https://issuer.example.com"
$env:MCP_HTTP_JWT_AUDIENCE="google-workspace-mcp"
$env:MCP_HTTP_JWKS_URI="https://issuer.example.com/.well-known/jwks.json"
$env:MCP_GOOGLE_OAUTH_REDIRECT_URL="https://mcp.example.com/google/oauth/callback"
$env:MCP_USER_TOKEN_DIR="/srv/mcp-google-workspace/tokens"
$env:MCP_SECRET_FILE="/run/secrets/mcp-google-workspace.json"
uv run python -m mcp_google_workspace.server_http
```

Clients connect with an OIDC bearer JWT. FastMCP validates its issuer, audience, signature, and expiry; the verified `iss` + `sub` selects an isolated encrypted Google token. Each user calls `connect_google_workspace`, opens its returned URL, completes Google consent, and calls `refresh_workspace_catalog`. The callback is PKCE-protected and one-time; it cannot connect Google credentials to a different MCP principal.

## Notable MCP tools

Gmail (namespaced as `gmail_*` in composed server):

- `send_email`
- `search_emails` (compact metadata-first inbox listing and Gmail query surface)
- `read_emails` (consistent one-to-100 message hydration with selectable detail level)
- `get_mail_digest`, `check_mail_updates` (curated triage and cursor-based incremental heartbeat)
- `list_labels`, `create_label`, `update_label`, `delete_label`, `apply_labels`
- `list_attachments`, `download_attachment`
- `mark_as_read`, `mark_as_unread`, `move_email`, `delete_email`
- `untrash_email`, `mark_as_spam`, `mark_as_not_spam`
- `batch_modify`, `batch_delete`
- `list_filters`, `create_filter`, `delete_filter`
- Drafts: `list_drafts`, `get_draft`, `create_draft`, `update_draft`, `delete_draft`, `send_draft`
- Threads: `list_threads`, `get_thread` (clean latest message by default), `modify_thread`, `trash_thread`, `untrash_thread`, `delete_thread`
- Forwarding addresses: `list_forwarding_addresses`, `get_forwarding_address`, `create_forwarding_address`, `delete_forwarding_address`
- Vacation settings: `get_vacation_settings`, `update_vacation_settings`

Calendar (namespaced as `calendar_*`):

- `search_events`, `read_events`, `get_calendar_digest`, `list_calendars`, `get_calendar_context`
- `check_time_availability`, `create_event`, `update_event`, `respond_to_event`, `delete_event`
- Smart scheduling: `find_common_free_slots`
- Event attachments: metadata is included by `read_events`; mutations use `add_event_attachment`, `remove_event_attachment`, `download_event_attachment`
- Event styling + conferencing fields on create/update: `color_id`, `visibility`, `transparency`, `conference_data`
- Conflict prevention: create/update run overlap checks and return `status: "CONFLICT"` when the slot is unavailable; updates exclude the event being moved
- Retry safety: pass a stable `idempotency_key` to `create_event`; the App does this automatically and Google Calendar stores a deterministic event ID

### Calendar availability tools

Use the availability tool that matches the scheduling intent:

- `check_time_availability`: verify a proposed `timeMin`/`timeMax` interval for one or more calendar IDs. Use this when the exact time is already known.
- `find_common_free_slots`: discover candidate intervals across participants inside a broader search window. Use this when the exact meeting time is not known yet.

### Calendar smart scheduling (`find_common_free_slots`)

`find_common_free_slots` returns candidate meeting slots (not raw FreeBusy output) for all participants in a time window.

Inputs:

- `participants`: list of calendar IDs/emails
- `time_min`, `time_max`: RFC3339 window
- `slot_duration_minutes`: desired meeting duration
- `granularity_minutes`: candidate step size
- `max_results`: result cap
- `time_zone`: optional timezone used in FreeBusy query
- `working_hours_start`, `working_hours_end`: optional daily working-hours filter (`HH:MM`, 24h)

`participants` must be sent as a native JSON array, for example `["primary", "rodrigo@example.com"]`. Use the canonical `slot_duration_minutes` field.

Working-hours defaults:

- `working_hours_start`: `08:00`
- `working_hours_end`: `17:00`

Drive (namespaced as `drive_*`):

- Files/content: `list_files`, `get_file`, `create_folder`, `create_file_metadata`, `upload_file`
- File mutations: `update_file_metadata`, `update_file_content`, `move_file`, `copy_file`, `delete_file`
- Content retrieval: `download_file`, `export_google_file`, `get_file_content_capabilities`
- Sharing: `list_permissions`, `get_permission`, `create_permission`, `update_permission`, `delete_permission`
- Shared Drives: `list_drives`, `get_drive`, `hide_drive`, `unhide_drive`
- Progress reporting: `upload_file`, `update_file_content`, `download_file`, and `export_google_file` emit MCP progress updates
- Use Drive for file discovery when you need Docs, Sheets, Slides, or Forms file IDs by MIME type or name

Sheets (namespaced as `sheets_*`):

- `get_spreadsheet`, `create_spreadsheet`
- Values: `get_sheet_values`, `batch_get_sheet_values`, `append_sheet_values`, `update_sheet_values`
- Raw request escape hatch: `batch_update_spreadsheet`

Docs (namespaced as `docs_*`):

- `get_document`, `create_document`
- Convenience text mutations: `append_document_text`, `replace_document_text`
- Raw request escape hatch: `batch_update_document`

Tasks (namespaced as `tasks_*`):

- Task lists: `list_tasklists`, `get_tasklist`, `create_tasklist`
- Tasks: `list_tasks`, `get_task`, `create_task`, `update_task`, `complete_task`, `move_task`, `delete_task`

People (namespaced as `people_*`):

- Contacts: `list_contacts`, `search_contacts`, `get_contact`, `create_contact`, `update_contact`, `delete_contact`
- Contact groups: `list_contact_groups`, `create_contact_group`, `modify_contact_group_members`
- Scope note: v1 is personal contacts only; Workspace directory lookup is intentionally excluded

Forms (namespaced as `forms_*`):

- `get_form`, `create_form`, `batch_update_form`
- Publishing: `set_form_publish_settings`
- Responses: `list_form_responses`, `get_form_response`

Slides (namespaced as `slides_*`):

- `get_presentation`, `create_presentation`
- Slide reads: `get_slide_page`, `get_slide_thumbnail`
- Text mutation and raw request escape hatch: `replace_text_in_presentation`, `batch_update_presentation`

Apps (namespaced as `apps_*`, mounted when `ENABLE_APPS_DASHBOARD=true`):

- State/navigation: `get_state`, `set_state`, `patch_state`, `today`, `next_range`, `prev_range`
- Dashboard: `get_dashboard`
- Weekly calendar layout: `get_weekly_calendar_view` (Google Calendar-like week columns)
- Detail views: `get_event_detail`, `get_email_detail`, `get_email_attachment`
- Calendar mutations are provided only by the core `calendar_*` tools; the App namespace contains view/state tools only.

Keep (namespaced as `keep_*`):

- `create_note`, `get_note`, `list_notes`, `delete_note`
- `share_note`, `unshare_note`
- `summarize_note` (sampling-powered)
- compatibility stubs for unsupported Keep v1 operations:
  - `update_note`
  - `archive_note`, `unarchive_note`
  - `list_keep_labels`, `create_keep_label`, `delete_keep_label`
  - checklist mutation helpers

Note: Keep tools/resources are mounted only when `ENABLE_KEEP=true`.

Chat (namespaced as `chat_*`):

- `list_spaces`, `get_space`
- `list_messages`, `get_message`
- `create_message`, `update_message`, `delete_message`
- `summarize_space_messages` (sampling-powered)

Note: Chat tools/resources are mounted only when `ENABLE_CHAT=true`.

Meet (namespaced as `meet_*`, mounted when `ENABLE_MEET=true`):

- Spaces: `create_space`, `get_space`, `update_space`, `end_active_conference`
- Conference records: `list_conference_records`, `get_conference_record`

Gemini (namespaced as `gemini_*`, mounted when `ENABLE_GEMINI=true`):

- `generate_image`, `edit_image`
- `describe_video`, `analyze_audio`
- local filesystem or Drive file ID inputs for media tools
- generated images are written locally under `GEMINI_OUTPUT_DIR`
- Artifacts and attendance metadata: `list_conference_participants`, `list_conference_recordings`, `list_conference_transcripts`
- v1 scope boundary: metadata only; transcript or recording file downloads still belong in Drive if added later

## MCP Resources and Prompts

Gmail resources:

- `gmail://inbox/summary`
- `gmail://labels`
- `gmail://email/{message_id}`

Calendar resources:

- `calendar://today`
- `calendar://week`

Drive resources:

- `drive://recent`
- `drive://shared-drives`
- `drive://file/{file_id}`

Keep resources:

- `keep://notes/recent`
- `keep://note/{note_id}`

Chat resources:

- `chat://spaces`
- `chat://space/{space_id}/messages`
- `chat://space/{space_id}/members`
- `chat://users/{user_ref}`
- `chat://users/me`

Apps resources (mounted when `ENABLE_APPS_DASHBOARD=true`):

- `apps://dashboard/current`
- `apps://dashboard/day/{ymd}`
- `apps://dashboard/week/{ymd}`
- `apps://calendar/week/{ymd}`

Prompts:

- `compose_email_prompt`
- `reply_email_prompt`
- `summarize_inbox_prompt`
- `summarize_keep_note_prompt`
- `extract_actions_from_keep_notes_prompt`
- `draft_chat_announcement_prompt`
- `summarize_chat_thread_prompt`

## Production operations

The remote runtime exposes unauthenticated minimal operational endpoints:

- `/health/live` — event-loop/process liveness
- `/health/ready` — draining, encryption, token storage, Redis, S3, and multi-worker dependency readiness
- `/version` — package/build/MCP protocol versions without secrets
- `/metrics` — Prometheus/OpenTelemetry-compatible low-cardinality metrics

Admission control is principal- and tool-cost-aware:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `MCP_RATE_LIMIT_PER_MINUTE` | `120` | Per-principal request rate |
| `MCP_GLOBAL_CONCURRENCY` | `64` | Server-wide active tool calls |
| `MCP_PRINCIPAL_CONCURRENCY` | `8` | Active calls per principal |
| `MCP_PRINCIPAL_STATE_LIMIT` | `10000` | Maximum retained admission-state identities |
| `MCP_PRINCIPAL_STATE_TTL_SECONDS` | `900` | Idle admission-state retention |
| `MCP_EXPENSIVE_CONCURRENCY` | `4` | Gemini/download/export/batch calls |
| `MCP_TOOL_DEADLINE_SECONDS` | `120` | Standard end-to-end deadline |
| `MCP_EXPENSIVE_DEADLINE_SECONDS` | `600` | Expensive-tool deadline |
| `MCP_SHUTDOWN_GRACE_SECONDS` | `30` | In-flight drain interval |

Google provider calls have a failure-window circuit breaker and expose logical-call versus HTTP-attempt metrics so retries are measurable. Logs include hashed principals and correlation IDs, never tokens, message bodies, prompts, filenames, or recipient lists.

For more than one HTTP process/replica, set `MCP_WORKERS`, `MCP_REDIS_URL`, `MCP_UPLOAD_S3_BUCKET`, and configure load-balancer affinity on `Mcp-Session-Id`; set `MCP_SESSION_AFFINITY=true` only after that routing is active. Redis then stores encrypted Google credentials, one-time PKCE state, distributed refresh locks, approval tokens, and upload metadata. Set `MCP_TOKEN_REDIS_URL` only when OAuth state must use a separate Redis deployment. Readiness fails unless OAuth state is Redis-backed and the complete distributed contract is reachable. The HTTP entrypoint uses `MCP_REDIS_URL` as `FASTMCP_DOCKET_URL` when the latter is not set. FastMCP native task-enabled tools use the standard MCP task protocol for operation IDs, progress polling, cancellation, expiry, and partial/error results. Additional workers can run with `uv run fastmcp tasks worker src/mcp_google_workspace/server.py:workspace_mcp` using the same `FASTMCP_DOCKET_URL` and queue name.

High-impact reversible writes use `prepare_workspace_action` and `commit_workspace_action`. The encrypted one-time token is principal-bound, argument-bound, expires after five minutes, and returns an impact preview before commit. Stable `resource` handles (`gdrive:///...`, `gmail-message:///...`, and related schemes) are included where applicable and can be refreshed through `resolve_workspace_resource`.

Emergency principal invalidation accepts hashed principal storage keys through `MCP_REVOKED_PRINCIPALS` or the Redis set `mcp:revoked_principals`. Redis-backed validation fails closed if revocation state cannot be checked.

## MCP Client-Dependent Features

These features depend on active MCP client support and may be silently unavailable in clients that do not implement the corresponding MCP capabilities.

### MCP Apps File Picker

The composed server exposes `files_file_manager`, built with FastMCP Prefab and delivered through the standard MCP Apps wire protocol. Its model-visible tool references a generated `ui://prefab/tool/.../renderer.html` resource served as `text/html;profile=mcp-app`; the rendered app provides drag-and-drop and native file selection. It is intended for hosted clients such as Claude where a server-local path is not useful and sending binary data through the model context is wasteful.

Typical flow:

1. Call `files_file_manager` and let the user choose one or more files (up to 25 MiB each).
2. Use the opaque `upl_...` handle returned by the picker as `uploaded_file` in a Workspace tool. `display_name` is presentation-only.
3. The integration reads the bytes directly from scoped server storage; the binary payload does not pass through the model context.

`uploaded_file` is supported by:

- Gmail `send_email`, `create_draft`, and `update_draft` attachments
- Drive `upload_file` and `update_file_content`
- Gemini `edit_image`, `describe_video`, and `analyze_audio`

Local/stdio uploads are session-scoped in memory. A single remote instance can use encrypted filesystem objects plus SQLite metadata through `MCP_UPLOAD_DB`. Multi-worker production uses Redis metadata and S3-compatible encrypted object storage by configuring `MCP_REDIS_URL` and `MCP_UPLOAD_S3_BUCKET` (plus optional `MCP_UPLOAD_S3_ENDPOINT` and `MCP_UPLOAD_S3_PREFIX`). Remote files use opaque handles, expire after one hour, and have a 250 MiB per-principal aggregate quota by default. Configure `MCP_UPLOAD_TTL_SECONDS` and `MCP_UPLOAD_QUOTA_BYTES` as needed. Uploads are MIME-sniffed, archive expansion is bounded, checksums are verified, and `MCP_REQUIRE_MALWARE_SCAN=true` enforces ClamAV through `MCP_CLAMAV_HOST`/`MCP_CLAMAV_PORT`. Raw host paths are absent from the remote catalog and rejected at runtime.

Use `files_delete_file` to remove an upload before its TTL expires.
Use `files_list_files_page` with `limit` and `cursor` when a principal has many uploads.
`get_mcp_apps_diagnostics` reports the UI resource, renderer mode, generated hidden callback addresses, and can run a temporary store/delete self-test with `run_self_test=true`.

The MCPB manifest forces Prefab's self-contained bundled renderer, avoiding a
runtime CDN dependency inside the host iframe.

**Requires:** an MCP client with MCP Apps/iframe rendering support. Clients without Apps support can still use Google Drive file IDs or trusted local/stdio paths.

### Progressive Tool Discovery

Both stdio and authenticated Streamable HTTP use FastMCP's [BM25 Tool Search transform](https://fastmcp.wiki/en/servers/transforms/tool-search) by default. The model-visible catalog is reduced to workflow/discovery entry points plus `search_tools` and `call_tool`; hidden tools remain callable after discovery. HTTP additionally hides namespaces whose OAuth capability is not granted and removes host-filesystem-only tools and parameters. `refresh_workspace_catalog` sends `tools/list_changed` after incremental consent.

Configuration:

- `MCP_TOOL_SEARCH=auto` (default): enable progressive discovery unless `MCP_CLIENT_MODEL` contains `claude`.
- `MCP_TOOL_SEARCH=on`: always enable progressive discovery.
- `MCP_TOOL_SEARCH=off`: expose the complete catalog.
- `MCP_CLIENT_MODEL=claude`: disable progressive discovery in auto mode for Claude's current tool/App routing behavior.

The official MCPB manifest declares `MCP_CLIENT_MODEL=claude`, so Claude Desktop receives the complete catalog. For a manual Claude Desktop stdio configuration, add the same environment variable explicitly.

`get_workspace_capabilities` reports enabled namespaces, OAuth capability names, and supported file-input strategies. `search_workspace` searches Drive files, contacts, and Gmail message IDs concurrently and returns normalized references.

### Incremental Google Consent

`connect_google_workspace` accepts a `capabilities` list such as `["drive"]`, `["gmail", "calendar"]`, or `["people"]`. When omitted it requests Gmail only. Tools build clients with service-specific scopes and return an actionable reconnect error when that capability has not yet been granted. `get_google_connection_status` accepts an optional `capability` to verify one grant and reports all currently granted capabilities. Disconnect attempts to revoke the Google grant before deleting encrypted local credentials.

### Response and Error Contracts

All model-visible tools publish recursively bounded, documented input schemas and closed documented object output schemas. Open input objects are limited to an audited set of genuine Google polymorphic batch maps. List/search responses expose their documented pagination and result-count fields without relying on undeclared conventions. Uncaught tool failures use a machine-readable envelope with `code`, `message`, `retryable`, `retry_after`, executable `required_action`, `provider_status`, and `field_errors`.

Drive, Calendar, and Gemini Drive-media downloads stream through bounded temporary files instead of buffering complete files in memory. `MCP_MAX_DOWNLOAD_BYTES` controls the per-download ceiling (default 250 MiB, maximum 10 GiB). Setting `MCP_REDIS_URL` makes prepare/commit records cross-replica and atomic.

### MCP Apps (UI Dashboard)

When `ENABLE_APPS_DASHBOARD=true`, the `apps_get_dashboard` and `apps_get_weekly_calendar_view` tools carry an `_meta.ui.resourceUri` annotation pointing to `ui://apps/dashboard-ui`. MCP clients that support the Apps rendering protocol (e.g. Claude Desktop) will embed an interactive workspace dashboard UI alongside the tool response.

The UI is a TypeScript web component that communicates with the server via PostMessage. It renders:

- A weekly calendar view (all-day events + timed event columns)
- An inbox summary with email detail drill-down
- Scheduling action buttons (RSVP, reschedule, cancel)

Session-scoped state (current view, anchor date, selected calendars, inbox query) is stored server-side per session and managed through `apps_get_state` / `apps_set_state` / `apps_patch_state`.

**Requires:** MCP client with App/iframe rendering support.

### Progress Notifications

Long-running tools emit incremental `notifications/progress` messages via `ctx.report_progress(current, total, description)`. Clients that handle progress notifications can display progress bars or status messages during API-heavy operations.

Tools that emit progress:

| Namespace | Tools |
|-----------|-------|
| Drive | `upload_file`, `update_file_content`, `download_file`, `export_google_file` |
| Apps | `get_dashboard`, `get_weekly_calendar_view`, `get_event_detail`, `get_email_detail`, `get_email_attachment` |
| Chat | `list_spaces`, `list_messages` |

**Requires:** MCP client that handles `notifications/progress`.

### Sampling

Optional tools use MCP sampling (`ctx.sample()`) to generate LLM-powered summaries within the tool response, using the host client's configured model for inference.

Sampling-powered tools:

- `keep_summarize_note` — summarizes a Keep note
- `chat_summarize_space_messages` — summarizes recent messages in a Chat space

**Requires:** MCP client with `sampling/createMessage` support (e.g. Claude Desktop). Without sampling support these tools will fail or return an empty summary.

## Tool Input Contract

The published JSON Schema is the runtime contract. Send a native JSON object using the documented field names and native arrays/objects. Unknown keys, camelCase aliases, JSON-stringified objects, comma-delimited arrays, and legacy parameter aliases are rejected rather than silently coerced.

## Google Keep API limitations

Google Keep API v1 currently exposes `create`, `get`, `list`, and `delete` for notes, plus permission batch create/delete. It does not expose a direct update/patch endpoint, archive/unarchive endpoints, or dedicated label endpoints in v1. The MCP server returns explicit `unsupported` responses for those operations.

## Marketplace packaging

This repo includes:

- `.claude-plugin/marketplace.json`
- `.claude-plugin/plugin.json`

and a compatibility manifest at:

- `plugins/google-workspace/.claude-plugin/plugin.json`

Reference docs:

- [Create and distribute a plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces.md)
- [Discover and install prebuilt plugins](https://code.claude.com/docs/en/discover-plugins.md)

### Install with Claude Code marketplace

From within Claude Code:

```bash
/plugin marketplace add guinacio/mcp-google-workspace
/plugin install google-workspace@google-workspace-mcp
```

Local checkout flow (from this repository root):

```bash
/plugin marketplace add .
/plugin install google-workspace@google-workspace-mcp
```

Optional refresh after updates:

```bash
/plugin marketplace update google-workspace-mcp
```

### Claude Desktop JSON (`mcpServers`)

If you want to run it directly in Claude Desktop without marketplace install, add this to your Claude Desktop config JSON under `mcpServers`.

Windows config file:

- `%APPDATA%\Claude\claude_desktop_config.json`

macOS config file:

- `~/Library/Application Support/Claude/claude_desktop_config.json`

Linux config file:

- `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "google-workspace": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "c:/path/to/mcp-google-workspace",
        "python",
        "-m",
        "mcp_google_workspace"
      ],
      "env": {
        "ENABLE_APPS_DASHBOARD": "true",
        "ENABLE_KEEP": "false",
        "ENABLE_CHAT": "false",
        "ENABLE_MEET": "false"
      }
    }
  }
}
```

Replace `c:/path/to/mcp-google-workspace` with your local repo path.

## Tests

```powershell
uv run pytest -q
```

Apps smoke test:

```powershell
# in-process mode (auto-enables apps namespace)
uv run python scripts/qa_apps_smoke.py

# or against a running Streamable HTTP server
uv run python scripts/qa_apps_smoke.py --http-url http://127.0.0.1:8001/mcp
```

## Existing calendar project reference

- [guinacio/mcp-google-calendar](https://github.com/guinacio/mcp-google-calendar)

