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
- FastMCP advanced features: progress updates, background tasks, resources, and prompts on MCP 2026-07-28.
- Composed server architecture: Gmail + Calendar + Drive + Sheets + Docs + Tasks + People + Forms + Slides mounted by default, with optional Apps/Keep/Chat/Meet/Gemini namespaces.
- Protocol support: MCP **2026-07-28** (modern, no session) plus legacy handshake-era connectivity for **2024-11-05 through 2025-11-25**, over both stdio and Streamable HTTP.

## Requirements

- Python 3.12+
- UV package manager
- Node.js 20+ and npm (required for MCP Apps UI in `src/mcp_google_workspace/apps/ui`)
- Google Cloud OAuth client credentials (`credentials.json`) for a client marked **Use this client for an AI-powered agent** (see [OAuth setup](#oauth-setup))
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

Create the server's OAuth client as an AI agent client:

1. In the Google Cloud console, open **Google Auth Platform → Clients** and create a client: **Desktop app** for local stdio/MCPB, or **Web application** with `MCP_GOOGLE_OAUTH_REDIRECT_URL` as an authorized redirect URI for HTTP deployments.
2. Select **Use this client for an AI-powered agent**.
3. Download the client JSON as `credentials.json`.

Google asks developers to mark every OAuth client that an AI agent uses, even one that only calls the Workspace REST APIs, and to keep agent and non-agent workflows on separate client IDs. Use a dedicated client for this server rather than one shared with other apps. The mark grants no extra scopes and does not replace user consent; it lets Workspace administrators apply agent-specific access controls. From November 11, 2026, Google's hosted Workspace MCP servers reject clients that are not marked. This server calls the Workspace REST APIs, not those hosted servers, so an unmarked client still works here. On November 12, 2026, the setting becomes permanent on existing clients: if yours is unmarked after that date, create a new client. See [Manage OAuth clients](https://support.google.com/cloud/answer/15549257).

To switch an existing installation to a new client, replace `credentials.json`, then call `disconnect_google_workspace` (it asks you to confirm) and connect again (each user, over HTTP). Saved grants keep refreshing through the client that issued them until you reconnect.

Place the Google OAuth client `credentials.json` in one of:

- project root: `./credentials.json`
- package credentials folder: `./src/credentials/credentials.json`

Each user's Google refresh token is encrypted separately with Fernet; the MCP never writes a shared `token.json`.

**Local stdio / MCPB:** nothing to configure. On first use the server generates the encryption key and keeps it in the OS keychain (Windows Credential Manager, macOS Keychain, or the Secret Service on Linux), never in a file or in the host's extension settings. If no secure keychain is available (for example headless Linux), startup stops and asks you to set `MCP_TOKEN_ENCRYPTION_KEY` instead. Losing the keychain entry (new machine, OS reinstall) only means reconnecting Google once.

**HTTP deployments:** configure a versioned key ring from your secret manager. Mount a secret-manager document through `MCP_SECRET_FILE`; `MCP_TOKEN_ENCRYPTION_KEY` remains a single-key development option. The keychain is never used over HTTP.

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

### Tool tiers

`MCP_TOOL_TIER` selects how much of the catalog is published:

- `core` (default): the everyday tools of every core service (82 tools).
- `extended`: core plus the raw `*_batch_update_*` tools for Docs, Sheets, Slides and Forms, the Gmail settings tools (`gmail_manage_filters`, `gmail_manage_forwarding`, `gmail_manage_vacation`), `drive_update_drive` and `get_mcp_apps_diagnostics` (91 tools).
- `complete`: extended plus every optional integration below (Apps dashboard, Chat, Keep, Meet, and Gemini when `GEMINI_API_KEY` is set) unless its `ENABLE_*` flag is explicitly `false`.

Tools above the active tier are not registered: they are absent from `tools/list`, a call to one is an unknown tool, and `prepare_workspace_action` does not offer them. `format: raw` on `sheets_get_spreadsheet`, `slides_read_presentation` and `forms_get_form` (Google's resource unchanged, to read before a raw batch update) also needs `extended` or `complete`; under `core` it fails with `invalid_input`. `get_google_connection_status` reports the active tier under `server.tool_tier`.

### Consent

Calls that are hard to undo or reach other people ask the user first: sending mail, replies, drafts, invitations and Chat messages; sharing and unsharing; permanent deletion; forwarding, forwarding or trashing filters and the auto-reply; disconnecting; and changes to 10 or more items in one call. Reads and ordinary reversible edits do not ask. No tool argument skips the question; arguments that change what happens (`mode: permanent`, `send_updates`, the recipients) decide whether it is asked.

A client that supports elicitation shows the question with a preview of exactly what will happen. Other clients get a `confirmation_required` error whose next step is `prepare_workspace_action`, which returns the same preview and a one-time token; `commit_workspace_action` then runs that exact call once. For those clients the host's own permission prompt on `commit_workspace_action` is the point where a person approves, so do not auto-approve that tool. `gmail_modify_messages`, `gmail_delete` and the `*_batch_update_*` tools take `dry_run: true` to return the preview without changing anything.

| Variable | Default | Effect |
| --- | --- | --- |
| `MCP_CONSENT` | `required` | `required`: ask as above. `preview_only`: never elicit; every consequential call goes through prepare/commit. `off`: run consequential calls without asking (for unattended deployments; logged as a warning at startup). |
| `MCP_EMAIL_PREPARE_RECIPIENTS` | `10` | Recipient count from which a send preview flags a large send. It does not decide whether a send asks: every send asks. |

Classification is per call; the table is in `src/mcp_google_workspace/common/consent.py` and the model in [docs/CONSENT.md](docs/CONSENT.md).

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

### Local stdio trust boundary

A local stdio server (including the MCPB bundle) serves exactly one trusted user: the
person running it. There is no bearer token, so every request runs as one explicit
trusted-local principal (`MCP_LOCAL_PRINCIPAL`, default `local-user`). That principal owns
the Google grant, the picker uploads, and the dashboard views of this process.

Dashboard view handles and upload IDs are still unguessable (256-bit and 192-bit random
values) and are checked on every call, but over stdio they only keep one view's state
apart from another view's. They are **not** multitenant isolation: anything that can talk
to this stdio process already acts as that user. Serve more than one user only through
the authenticated Streamable HTTP entrypoint, where every handle and upload is bound to
the verified `(issuer, subject)` of the bearer token.

## MCP Bundle (MCPB)

This repository now includes a native `uv`-based MCP Bundle manifest and packaging assets.

Install on Claude Desktop:

1. Download the latest `.mcpb` from [GitHub Releases](https://github.com/guinacio/mcp-google-workspace/releases/latest).
2. In Claude Desktop, open the MCP bundle install flow.
3. Select the downloaded `mcp-google-workspace-*.mcpb` file.
4. Choose the credentials directory that contains `credentials.json` (a Desktop app client marked as an AI agent client; see [OAuth setup](#oauth-setup)), or leave it empty to use the repo defaults.
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

The remote server speaks MCP Streamable HTTP for protocol **2026-07-28** and keeps FastMCP's built-in compatibility path for every handshake-era (legacy) client version: **2024-11-05, 2025-03-26, 2025-06-18, and 2025-11-25**. `/version` reports both the full supported set and the pair this repository's suite actively tests (2025-11-25 and 2026-07-28). Modern requests are self-contained POSTs with no session: a response is JSON unless the tool reports progress or runs long, in which case it becomes a request-scoped SSE stream, and closing that stream cancels the tool. Legacy requests keep the `initialize` handshake and an `Mcp-Session-Id` session. Durable long-running work runs as MCP Tasks on the Redis queue. It requires an OIDC bearer-token issuer and refuses to start without this configuration:

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

Clients connect with an OIDC bearer JWT. The server validates its signature (JWKS), issuer, audience and expiry, and requires `iss`, `sub` and `exp`; the verified `iss` + `sub` selects an isolated encrypted Google token. MCP bearer tokens are never forwarded to Google. A request without a valid token gets `401` with a `WWW-Authenticate` challenge whose `resource_metadata` points to the RFC 9728 document served at `/.well-known/oauth-protected-resource<base path>/mcp`, which names the issuer as the authorization server (the server does not implement an authorization server or dynamic client registration). Each user calls `connect_google_workspace`, opens its returned URL and completes Google consent; `tools/list` reflects the caller's current grants on every request (`get_google_connection_status` reports them). The callback is PKCE-protected, one-time, checks the RFC 9207 `iss` parameter when present, and cannot connect Google credentials to a different MCP principal.

### Docker / GHCR

Every GitHub release publishes a signed multi-architecture image for
`linux/amd64` and `linux/arm64`:

```bash
docker pull ghcr.io/guinacio/mcp-google-workspace:latest
docker pull ghcr.io/guinacio/mcp-google-workspace:2.0.1
```

The image runs the authenticated Streamable HTTP entrypoint on port 8000. It
does not replace the local stdio/MCPB installation. To run it locally:

```bash
cp .env.example .env
# Configure the OIDC, Google OAuth redirect, and encryption values in .env.
# Place the Google OAuth client at ./credentials.json.
docker compose up -d
curl http://localhost:8000/health/live
```

The Compose service persists encrypted Google tokens and upload metadata in a
named volume. For production, terminate TLS in front of the container, use a
versioned image tag, mount a versioned `MCP_SECRET_FILE`, and configure the
distributed Redis/S3 contract described under Production operations when
running multiple replicas.

Release images include signed GitHub build provenance. Verify a tag with:

```bash
gh attestation verify \
  oci://ghcr.io/guinacio/mcp-google-workspace:2.0.1 \
  -R guinacio/mcp-google-workspace
```

## Notable MCP tools

The catalog is organised around tasks rather than Google API methods: one tool per job, with typed parameters, and an `action` enum where a resource has several operations. The default `core` tier publishes 82 tools (80 for the model plus two app-only upload callbacks); see [Tool tiers](#tool-tiers) for the rest.

Gmail (namespaced as `gmail_*` in composed server):

- `search_emails` (Gmail query plus sender, recipient, subject, unread, attachment and age filters; `group_by_thread` lists conversations)
- `get_mail_digest`, `check_mail_updates` (unbiased received/sent feeds, including routed and automated mail)
- `read_emails` (one to 100 messages at one detail level; attachments carry their `download_id`), `get_thread`, `download_attachment`
- `send_email`, `reply` (true Gmail-thread replies with RFC reply headers; `reply_all` copies everyone)
- `modify_messages`: labels by name or ID, read/unread, archive or back to the inbox, spam/not spam and restore from Trash, for messages or a whole thread
- `delete`: Trash (default) or permanent deletion of messages or a thread
- Drafts: `save_draft` (create, or rewrite with `draft_id`), `list_drafts` (`draft_id` reads one), `send_draft`, `delete_draft`
- Labels: `list_labels`, `manage_labels` (`create`, `update`, `delete`)
- Extended tier: `manage_filters`, `manage_forwarding`, `manage_vacation`

Label parameters accept names as shown in Gmail ("Clients/Acme") or IDs; system labels such as INBOX, UNREAD or STARRED match case-insensitively. A name costs one `users.labels.list` per call; an unknown or ambiguous name fails with `not_found` and the candidate labels, and no label is ever created implicitly.

Calendar (namespaced as `calendar_*`):

- `search_events`, `read_events`, `get_calendar_digest`
- `get_calendar_context`: the account time zone and current time, plus the user's calendars
- `find_free_time`: one FreeBusy query that reports whether a window is free for everyone (`all_free`, busy periods per calendar) and suggests slots of a given length inside working hours
- `create_event`, `update_event` (`add_meet_link`, `reminder_minutes` or `reminders` with popup and email methods, `send_updates`, color, visibility, transparency, attendees, attachments, recurrence), `respond_to_event`, `delete_event`
- `set_event_attachments`: add and remove attachments in one change; attached Drive files download through `drive_download_file`
- Conflict prevention: create/update run overlap checks and fail with `conflict` when the slot is unavailable; updates exclude the event being moved
- Retry safety: pass a stable `idempotency_key` to `create_event`; the App does this automatically and Google Calendar stores a deterministic event ID

`find_free_time` takes `participants` (calendar IDs or attendee emails, as a native JSON array), `time_min`/`time_max`, `slot_duration_minutes` (default 30), `granularity_minutes` (15), `max_results` (10), `time_zone` and `working_hours_start`/`working_hours_end` (`08:00`/`17:00`). To check one proposed time, pass it as the window and read `all_free`.

Drive (namespaced as `drive_*`):

- Find and read: `list_files` (Drive query plus `folder_id`, `kind`, `modified_after` and `full_text` filters), `get_file` (metadata plus how the content can be retrieved), `read_file` (text of Docs, Sheets, Slides and text files)
- Write: `upload_file` (new file from an upload or local path, replace an existing file's content with `file_id`, or an empty/native Google file from metadata alone), `create_folder`, `update_file_metadata`, `move_file`, `copy_file`, `delete_file`
- Save locally: `download_file` (stored files, and Google files exported to PDF or `export_mime_type`; local stdio only)
- Sharing: `list_permissions`, `share_file` (new grant, or change one with `permission_id`), `unshare_file`
- Shared drives: `list_drives`; extended tier: `update_drive` (hide or unhide)
- Progress reporting: `upload_file` and `download_file` emit MCP progress updates

Sheets (namespaced as `sheets_*`):

- `get_spreadsheet` (`format: raw` in the extended tier: Google's Spreadsheet resource, paged by sheet), `create_spreadsheet`
- Values: `read_sheet_values` (one or many ranges), `update_sheet_values`, `append_sheet_values`
- Extended tier: `batch_update_spreadsheet` (raw request escape hatch)

Docs (namespaced as `docs_*`):

- `read_document` (`text`, `markdown`, `outline` with the indices batch updates need, or `raw`), `create_document`
- Convenience text mutations: `append_document_text`, `replace_document_text`
- Extended tier: `batch_update_document`

Tasks (namespaced as `tasks_*`):

- Task lists: `list_tasklists` (`tasklist_id` reads one), `create_tasklist`
- Tasks: `list_tasks` (`task_id` reads one), `tasks_digest`, `create_task`, `update_task` (fields, completion, and moves within or across lists), `delete_task`; every task tool defaults to the account's default list

People (namespaced as `people_*`):

- Contacts: `search_contacts` (without `query` it lists every contact), `get_contact`, `create_contact`, `update_contact`, `delete_contact`
- Contact groups: `manage_contact_groups` (`list`, `create`, `modify_members`)
- Scope note: v1 is personal contacts only; Workspace directory lookup is intentionally excluded

Forms (namespaced as `forms_*`):

- `get_form` (`format: raw` in the extended tier: Google's Form resource, paged by item), `create_form`, `set_form_publish_settings`
- Responses: `list_form_responses` (`response_id` reads one)
- Extended tier: `batch_update_form`

Slides (namespaced as `slides_*`):

- `read_presentation` (outline with each slide's text and notes; `slide_ids` adds element geometry; `format: raw` in the extended tier: Google's Presentation, or the Pages of `slide_ids`, paged), `create_presentation`, `get_slide_thumbnail`
- Text mutation: `replace_text_in_presentation`; extended tier: `batch_update_presentation`

Apps (namespaced as `apps_*`, mounted when `ENABLE_APPS_DASHBOARD=true`):

- State/navigation: `get_state`, `set_state`, `patch_state`, `today`, `next_range`, `prev_range`
- Dashboard: `get_dashboard`
- Weekly calendar layout: `get_weekly_calendar_view` (Google Calendar-like week columns)
- Detail views: `get_event_detail`, `get_email_detail`, `get_email_attachment`
- Calendar mutations are provided only by the core `calendar_*` tools; the App namespace contains view/state tools only.

Keep (namespaced as `keep_*`):

- `create_note`, `get_note`, `list_notes`, `delete_note`
- `share_note`, `unshare_note`
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

Apps resources (mounted when `ENABLE_APPS_DASHBOARD=true`; the root composition adds the `apps` namespace prefix, matching the mounted `ui://apps/dashboard-ui` address below):

- `apps://apps/dashboard/current`
- `apps://apps/dashboard/day/{ymd}`
- `apps://apps/dashboard/week/{ymd}`
- `apps://apps/calendar/week/{ymd}`

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
- `/health/ready` — draining, encryption, token storage, Redis, S3, app state, task queue, operation records, operation lease and (for a fleet) continuation keys and shared storage; advisory checks are listed under `warnings`
- `/version` — package/build/MCP protocol versions without secrets
- `/metrics` — Prometheus/OpenTelemetry-compatible low-cardinality metrics

Admission control is principal- and tool-cost-aware. Per-principal limits are fleet-wide (enforced in Redis) whenever `MCP_REDIS_URL` is set, and process-local otherwise (`MCP_ADMISSION_BACKEND=auto|local|redis`); process limits always apply per process. Background-task executions take the same concurrency slots as foreground calls and count toward draining; the request rate is charged once, at submission. Rejections are JSON-RPC errors (`-32005` rate limited, `-32006` draining/unavailable); the deadline and every Google or validation failure are `isError` tool results with a stable error envelope.

| Variable | Default | Scope | Purpose |
| --- | ---: | --- | --- |
| `MCP_RATE_LIMIT_PER_MINUTE` | `120` | principal (fleet) | Sliding one-minute request rate |
| `MCP_PRINCIPAL_CONCURRENCY` | `8` | principal (fleet) | Active calls and task executions per principal |
| `MCP_PRINCIPAL_QUEUE_SECONDS` | `10` | principal (fleet) | Wait for a fleet concurrency slot before refusing |
| `MCP_GLOBAL_CONCURRENCY` | `64` | process | Active tool calls and task executions in this process |
| `MCP_PRINCIPAL_STATE_LIMIT` | `10000` | process | Maximum retained process-local admission identities |
| `MCP_PRINCIPAL_STATE_TTL_SECONDS` | `900` | process | Idle admission-state retention |
| `MCP_EXPENSIVE_CONCURRENCY` | `4` | process | Gemini/download/export/batch calls |
| `MCP_TOOL_DEADLINE_SECONDS` | `120` | call | Standard deadline (also bounds a task's runtime) |
| `MCP_EXPENSIVE_DEADLINE_SECONDS` | `600` | call | Expensive-tool deadline; must stay below `MCP_OPERATION_LEASE_SECONDS` |
| `MCP_SHUTDOWN_GRACE_SECONDS` | `30` | process | In-flight request and task drain interval |

Google provider calls have a failure-window circuit breaker and expose logical-call versus HTTP-attempt metrics so retries are measurable. Logs include hashed principals and correlation IDs, never tokens, message bodies, prompts, filenames, recipient lists or continuation state. A valid W3C `traceparent`/`tracestate` in the request `_meta` parents the tool span; malformed values are ignored and baggage is never accepted. A multi-round-trip question is recorded as outcome `input_required` (a round, span attribute `mcp.tool.round`), separately from completed logical operations (`mcp_workspace_logical_operations_total`).

The HTTP boundary validates `Host` and `Origin` (defaults derived from `MCP_HTTP_BASE_URL`; `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`), bounds request bodies while they stream (`MCP_MAX_REQUEST_BYTES`, 36 MiB, enough for one 25 MiB picker upload after base64 encoding; chunked bodies are refused with `413` before being buffered) and lets the SDK reject `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` mismatches (`-32020`) and unsupported versions (`-32022`) before any tool runs. `MCP_HTTP_RESPONSE_MODE=json` disables request-scoped SSE for intermediaries that cannot pass it (no progress, and disconnects no longer cancel tools). Change subscriptions (`subscriptions/listen`) are not advertised.

### Deployment

A single stdio (MCPB) process or a single-process Streamable HTTP deployment needs none of the distributed infrastructure below: app state, uploads, operation records and the task queue all use in-memory backends, and Redis is not required. Redis (and S3-compatible object storage for uploads) becomes required only when you run more than one HTTP process or replica behind a load balancer, as described next. For a dedicated walkthrough of that fleet topology (Redis/S3 contract, reverse proxy, and running workers), see the [multi-replica runbook](docs/DEPLOYMENT_FLEET.md).

For more than one HTTP process or replica, declare the fleet (`MCP_WORKERS` for processes per replica, `MCP_REPLICAS` for single-worker replicas; setting `MCP_REDIS_URL` implies a fleet too) and set `MCP_REDIS_URL`, `MCP_UPLOAD_S3_BUCKET`, `MCP_REQUEST_STATE_KEYS` and `FASTMCP_TASKS_ENCRYPTION_KEY` identically on every replica and worker. Redis then stores encrypted Google credentials, one-time PKCE state, distributed refresh locks, operation records, upload metadata, dashboard view state (encrypted with the same key ring), fleet admission counters and the task queue. Modern (2026-07-28) clients need **no session affinity**: every request is independent, and views, uploads, confirmations and tasks are addressed by server-issued handles or sealed continuations that any replica can verify. Only handshake-era (legacy) clients hold an `Mcp-Session-Id` session in one process; if you serve them behind several replicas, pin them to one replica at the load balancer (a hash of that header does not work; see the [multi-replica runbook](docs/DEPLOYMENT_FLEET.md)) and set `MCP_SESSION_AFFINITY=true` (readiness reports it as an advisory `legacy_session_affinity` check). Set `MCP_TOKEN_REDIS_URL` only when OAuth state must use a separate Redis deployment. Readiness fails unless OAuth state is Redis-backed and the complete distributed contract is reachable. Task-enabled tools run through the MCP Tasks extension (`io.modelcontextprotocol/tasks`), registered once on the root server by one shared factory for HTTP, stdio, and workers: the queue is `FASTMCP_DOCKET_URL` when set, otherwise `MCP_REDIS_URL` (except in the local stdio bundle), otherwise an in-process `memory://` queue; the queue name defaults to `mcp-google-workspace` (`FASTMCP_DOCKET_NAME` overrides it). Set the same `FASTMCP_TASKS_ENCRYPTION_KEY` on every server and worker so queued caller-credential snapshots are encrypted (tool arguments and results are not). Additional workers run with `uv run mcp-google-workspace-worker` against the same Redis queue.

Clients without elicitation complete consequential calls with `prepare_workspace_action` and `commit_workspace_action` (see [Consent](#consent)). The one-time token is principal-bound and bound to the validated arguments, expires after `MCP_CONFIRMATION_TTL_SECONDS` (10 minutes by default), and comes with the same preview the elicitation would show. The bound action runs at most once: repeating a commit that already ran returns its saved result, and a call whose outcome is uncertain after a timeout or disconnect returns `outcome_unknown` with verification steps instead of being retried (see `docs/migration/W4_CONFIRMATION_POLICY.md`). Stable `resource` handles (`gdrive:///...`, `gmail-message:///...`, and related schemes) are included where applicable and can be refreshed through `resolve_workspace_resource`.

Emergency principal invalidation accepts hashed principal storage keys through `MCP_REVOKED_PRINCIPALS` or the Redis set `mcp:revoked_principals`. Redis-backed validation fails closed if revocation state cannot be checked.

## MCP Client-Dependent Features

These features depend on active MCP client support and may be silently unavailable in clients that do not implement the corresponding MCP capabilities.

### MCP Apps File Picker

The composed server exposes `uploads_file_manager`, built with FastMCP Prefab and delivered through the standard MCP Apps wire protocol. Its model-visible tool references a generated `ui://prefab/tool/.../renderer.html` resource served as `text/html;profile=mcp-app`; the rendered app provides drag-and-drop and native file selection. It is intended for hosted clients such as Claude where a server-local path is not useful and sending binary data through the model context is wasteful.

Typical flow:

1. Call `uploads_file_manager` and let the user choose one or more files (up to 25 MiB each).
2. Use the opaque `upl_...` handle returned by the picker as `uploaded_file` in a Workspace tool. `display_name` is presentation-only.
3. The integration reads the bytes directly from scoped server storage; the binary payload does not pass through the model context.

`uploaded_file` is supported by:

- Gmail `send_email`, `reply` and `save_draft` attachments
- Drive `upload_file` (new files and content replacement)
- Gemini `edit_image`, `describe_video`, and `analyze_audio`

Local/stdio uploads live in process memory, scoped to the trusted-local principal (not to an MCP connection, so they survive across requests) and use the same `upl_...` handles, TTL, quota, and content checks as remote uploads; see [Local stdio trust boundary](#local-stdio-trust-boundary). A single remote instance can use encrypted filesystem objects plus SQLite metadata through `MCP_UPLOAD_DB`. Multi-worker production uses Redis metadata and S3-compatible encrypted object storage by configuring `MCP_REDIS_URL` and `MCP_UPLOAD_S3_BUCKET` (plus optional `MCP_UPLOAD_S3_ENDPOINT` and `MCP_UPLOAD_S3_PREFIX`). Uploads use opaque handles, expire after one hour, and have a 250 MiB per-principal aggregate quota by default. Configure `MCP_UPLOAD_TTL_SECONDS` and `MCP_UPLOAD_QUOTA_BYTES` as needed. Uploads are MIME-sniffed, archive expansion is bounded, checksums are verified, and `MCP_REQUIRE_MALWARE_SCAN=true` enforces ClamAV through `MCP_CLAMAV_HOST`/`MCP_CLAMAV_PORT`. Raw host paths are absent from the remote catalog and rejected at runtime.

Uploads expire on their own; `uploads_list_files` lists them a page at a time (`limit`, `cursor`).
`get_mcp_apps_diagnostics` (extended tier) reports the UI resource, renderer mode, generated hidden callback addresses, and can run a temporary store/delete self-test with `run_self_test=true`.

The picker always serves the self-contained renderer bundled in the locked `prefab-ui` version (every transport, not only the MCPB), so its resource declares no CSP domains and loads nothing from a CDN. `uploads_list_files` and `uploads_read_file` are model-only; `uploads_store_files` and `uploads_delete_file` are the picker's app-only callbacks.

**Requires:** an MCP client with MCP Apps/iframe rendering support. Clients without Apps support can still use Google Drive file IDs or trusted local/stdio paths.

### Progressive Tool Discovery

Both stdio and authenticated Streamable HTTP use FastMCP's [BM25 Tool Search transform](https://fastmcp.wiki/en/servers/transforms/tool-search) by default. The model-visible catalog is reduced to the connection and consent tools, `search_workspace`, the main read entry point of each core service (`gmail_search_emails`, `gmail_get_mail_digest`, `calendar_search_events`, `calendar_get_calendar_digest`, `drive_list_files`, `tasks_tasks_digest`), plus `search_tools` and `call_tool`; hidden tools, including the upload picker and diagnostics, remain callable after discovery. HTTP additionally hides namespaces whose OAuth capability is not granted and removes host-filesystem-only tools and parameters. The catalog is sorted and evaluated against the caller's current grants on every request (and re-checked when a tool is called, so a stale catalog cannot run a revoked capability); after a consent, list tools again (`get_google_connection_status` reports the grants; no notification is sent).

Configuration:

- `MCP_TOOL_SEARCH=auto` (default): enable progressive discovery unless `MCP_CLIENT_MODEL` contains `claude`.
- `MCP_TOOL_SEARCH=on`: always enable progressive discovery.
- `MCP_TOOL_SEARCH=off`: expose the complete catalog.
- `MCP_CLIENT_MODEL=claude`: disable progressive discovery in auto mode for Claude's current tool/App routing behavior.

The official MCPB manifest declares `MCP_CLIENT_MODEL=claude`, so Claude Desktop receives the complete catalog. For a manual Claude Desktop stdio configuration, add the same environment variable explicitly.

`get_google_connection_status` reports, besides the user's grants, the active tool tier, enabled namespaces, OAuth capability names, and supported file-input strategies. `search_workspace` searches Drive (names and content), contacts, and Gmail concurrently and returns normalized hits: Gmail hits carry subject, sender, date and snippet. `docs_read_document` and `drive_read_file` return a document or file as text (Docs also as Markdown) in pages, with the same `next_offset`, `body_chars` and `body_sha256` paging fields as email bodies, so a remote client can read content without the server's filesystem.

### Incremental Google Consent

`connect_google_workspace` accepts a `capabilities` list such as `["drive"]`, `["gmail", "calendar"]`, or `["people"]`. When omitted it requests Gmail only. Tools build clients with service-specific scopes and return an actionable reconnect error when that capability has not yet been granted. `get_google_connection_status` accepts an optional `capability` to verify one grant and reports all currently granted capabilities. Disconnect attempts to revoke the Google grant before deleting encrypted local credentials.

### Response and Error Contracts

All model-visible tools publish documented input schemas with closed objects; open input objects are limited to an audited set of genuine Google polymorphic batch maps. Argument size limits (1,000,000 characters per string, 10,000 items per array or properties per object) are enforced on every call but not repeated in the schemas. Every response is normalized by the server (snake_case keys, dates in the account's time zone, people as `{name, email}`, a `resource` handle per Workspace item carrying only what the item does not already say) and publishes an open output schema; batch-update tools keep Google's per-request `replies` inside that envelope. Every list-shaped response (list and search tools, digests, thread reads) carries the same `count`, `has_more`, `next_page_token` and `fetched_at` fields next to its resource-named list, plus `account_timezone` when its items carry dates; `status` appears only on results of changes. Uncaught tool failures use a machine-readable envelope with `code`, `message`, `retryable`, `retry_after`, executable `required_action`, `provider_status`, and `field_errors`.

Drive, Calendar, and Gemini Drive-media downloads stream through bounded temporary files instead of buffering complete files in memory. `MCP_MAX_DOWNLOAD_BYTES` controls the per-download ceiling (default 250 MiB, maximum 10 GiB). Setting `MCP_REDIS_URL` makes prepare/commit records cross-replica and atomic.

### MCP Apps (UI Dashboard)

When `ENABLE_APPS_DASHBOARD=true`, the `apps_get_dashboard` and `apps_get_weekly_calendar_view` tools carry `_meta.ui.resourceUri` pointing to `ui://apps/dashboard-ui` (the nested key only; the flat `ui/resourceUri` alias and the old `ui://dashboard-ui`/`apps://dashboard/ui` addresses were removed). MCP clients that support the stable MCP Apps protocol (2026-01-26), such as Claude Desktop, embed an interactive workspace dashboard alongside the tool response. The dashboard applies the host's theme, colors, fonts, safe area and container size, and loads nothing from the network (no web fonts, no CDN), so the host's default CSP applies.

The UI is a vanilla TypeScript view built on MCP Apps SDK 2 (`@modelcontextprotocol/ext-apps` 2.0.3 with the split `@modelcontextprotocol/client`/`core` 2.1.0). It talks only to the host through the Apps `ui/*` channel and:

- takes the host's tool input/result as the invocation context and never opens a second view while the launch call is opening one; it handles cancellation and teardown and ignores stale or late responses;
- checks the host's capabilities before optional actions: links, downloads (draft `ui/download-file`, bounded to 10 MiB inline), "Reply in chat" (`ui/message`), model context updates and full screen are offered only when supported, and a declined action is reported with a usable alternative instead of forcing another path;
- offers a write only when the server's operation manifest (`_meta["mcp-google-workspace/operations"]` on launch results) lists it for your account; without a manifest it only reads;
- reports tool failures by their typed code and undoes optimistic changes when an action fails.

The two launch tools are visible to the model and the view; the dashboard callbacks (`apps_get_state`, `apps_set_state`, `apps_patch_state`, `apps_next_range`, `apps_prev_range`, `apps_today`, `apps_get_event_detail`, `apps_get_email_detail`, `apps_get_email_attachment`) are app-only. The server still authorizes every call. Details, the capability matrix and the manifest format: [docs/RICH_OUTPUTS.md](docs/RICH_OUTPUTS.md#mcp-apps-outputs).

It renders:

- A weekly calendar view (all-day events + timed event columns)
- An inbox summary with email detail drill-down
- Scheduling action buttons (RSVP, reschedule, cancel)

Each dashboard view has its own server-side state (current view, anchor date, selected calendars, inbox query, weekend visibility), addressed by a server-issued view handle:

- `apps_get_dashboard` or `apps_get_weekly_calendar_view` called without `view_handle` opens a new view. The result carries `{handle, revision, expires_at, ttl_seconds}` in `_meta["mcp-google-workspace/view"]` (what the Apps UI reads) and as `view` in the structured content. Pass `view_handle` to reopen an existing view.
- `apps_get_state`, `apps_set_state`, `apps_patch_state`, `apps_next_range`, `apps_prev_range`, and `apps_today` require `view_handle`. Writes accept `expected_revision`; if the view changed since that revision nothing is written and the call returns a `view_state_conflict` tool error with the current state (the UI refetches). Without it, the change is applied atomically on top of the latest state.
- Unknown, expired, malformed, or another user's handles return a `view_handle_invalid` tool error; the UI then opens one fresh view and retries once.
- Views expire after `MCP_APP_VIEW_TTL_SECONDS` of inactivity (default 86400, sliding). State lives in process memory over stdio and in Redis (`MCP_REDIS_URL`) for the HTTP fleet. Each view is isolated from every other view, including other views of the same user.

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

This includes the golden wire-contract fixtures (`tests/wire/`, raw JSON-RPC over HTTP and the
in-memory stdio-equivalent transport, modern and legacy) and the feature-flag matrix
(`tests/test_feature_flag_matrix.py`, startup + list + one safe call per optional integration,
independently and together). Regenerate the wire fixtures deliberately after a reviewed
protocol/behavior change with `UPDATE_WIRE_FIXTURES=1 uv run pytest tests/wire`.

Verify the packaged MCPB bundle actually runs standalone (build, extract, stdio lifecycle: start,
list, a safe call, picker store/list/read/delete, close, reconnect):

```powershell
uv run python scripts/verify_mcpb_bundle.py
```

Dashboard browser regression tests (from `src/mcp_google_workspace/apps/ui`):

```powershell
npm ci
npx playwright install chromium
npm run build
npm test
```

These tests embed the generated UI bundle in an AppBridge host with fixture data;
Google credentials are not required.

Apps smoke test:

```powershell
# in-process mode (auto-enables apps namespace)
uv run python scripts/qa_apps_smoke.py

# or against a running Streamable HTTP server
uv run python scripts/qa_apps_smoke.py --http-url http://127.0.0.1:8001/mcp
```

## Existing calendar project reference

- [guinacio/mcp-google-calendar](https://github.com/guinacio/mcp-google-calendar)

