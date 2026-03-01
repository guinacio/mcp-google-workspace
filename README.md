# mcp-google-workspace

Production-ready Google Workspace MCP package with:

- Gmail MCP: send/read/search emails, attachment handling, label management, batch operations.
- Google Calendar MCP: events, availability, create/update/delete operations.
- Google Drive MCP: files/folders CRUD, uploads/downloads/exports, sharing permissions, Shared Drives operations.
- MCP Apps Dashboard: workspace dashboard + morning briefing app-layer tools/resources.
- Google Keep MCP: note create/get/list/delete, collaboration permissions, resources, prompts.
- Google Chat MCP: spaces and messages operations, collaboration messaging workflows.
- FastMCP advanced features: Context logging, progress updates, user elicitation, sampling, resources, and prompts.
- Composed server architecture: Gmail + Calendar + Keep + Chat mounted into one namespaced MCP server.
- Optional app-layer namespace for dashboard workflows mounted as `apps_*`.

## Requirements

- Python 3.12+
- UV package manager
- Google Cloud OAuth desktop credentials (`credentials.json`)
- Gmail API + Google Calendar API + Google Drive API + Google Keep API + Google Chat API enabled in your Google Cloud project

## Installation

```powershell
uv sync --all-extras --dev
```

## OAuth setup

Place `credentials.json` in one of:

- project root: `./credentials.json`
- package credentials folder: `./src/credentials/credentials.json`

On first run, the server launches a browser for OAuth consent and writes `token.json`.

### Keep and Chat scope feature flags

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

If you change this flag (or after upgrading scopes/features/scopes), delete `token.json` and re-authenticate to refresh granted scopes.

### Apps dashboard rollout flag

The MCP app-layer dashboard/briefing namespace is opt-in for controlled rollout.

Enable apps namespace:

```powershell
$env:ENABLE_APPS_DASHBOARD="true"
```

## Run (STDIO)

```powershell
uv run python -m mcp_google_workspace
```

## Run (SSE)

```powershell
$env:MCP_HOST="127.0.0.1"
$env:MCP_PORT="8000"
uv run python -m mcp_google_workspace.server_sse
```

## Notable MCP tools

Gmail (namespaced as `gmail_*` in composed server):

- `send_email`
- `read_email`
- `search_emails`
- `list_emails`
- `list_labels`, `create_label`, `update_label`, `delete_label`, `apply_labels`
- `list_attachments`, `download_attachment`
- `mark_as_read`, `mark_as_unread`, `move_email`, `delete_email`
- `untrash_email`, `mark_as_spam`, `mark_as_not_spam`
- `batch_modify`, `batch_delete`
- `list_filters`, `create_filter`, `delete_filter`
- Drafts: `list_drafts`, `get_draft`, `create_draft`, `update_draft`, `delete_draft`, `send_draft`
- Threads: `list_threads`, `get_thread`, `modify_thread`, `trash_thread`, `untrash_thread`, `delete_thread`
- `list_history`
- Forwarding addresses: `list_forwarding_addresses`, `get_forwarding_address`, `create_forwarding_address`, `delete_forwarding_address`
- Vacation settings: `get_vacation_settings`, `update_vacation_settings`
- `summarize_email` (sampling-powered)

Calendar (namespaced as `calendar_*`):

- `get_events`, `get_event`, `list_calendars`, `get_timezone_info`, `get_current_date`
- `check_availability`, `create_event`, `update_event`, `delete_event`
- Smart scheduling: `find_common_free_slots`
- Event attachments: `list_event_attachments`, `add_event_attachment`, `remove_event_attachment`, `download_event_attachment`
- Event styling + conferencing fields on create/update: `color_id`, `visibility`, `transparency`, `conference_data`
- Conflict prevention: create/update run a FreeBusy overlap check and return `status: "CONFLICT"` when slot is not available

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

Apps (namespaced as `apps_*`, mounted when `ENABLE_APPS_DASHBOARD=true`):

- State/navigation: `get_state`, `set_state`, `patch_state`, `today`, `next_range`, `prev_range`
- Dashboard: `get_dashboard`
- Weekly calendar layout: `get_weekly_calendar_view` (Google Calendar-like week columns)
- Morning briefing: `get_morning_briefing`
- Scheduling actions: `find_meeting_slots`, `create_meeting_from_slot`, `reschedule_meeting`, `cancel_meeting`

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
- `apps://briefing/morning/{ymd}`

Prompts:

- `compose_email_prompt`
- `reply_email_prompt`
- `summarize_inbox_prompt`
- `summarize_keep_note_prompt`
- `extract_actions_from_keep_notes_prompt`
- `draft_chat_announcement_prompt`
- `summarize_chat_thread_prompt`

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

## Tests

```powershell
uv run pytest -q
```

Apps smoke test:

```powershell
# in-process mode (auto-enables apps namespace)
uv run python scripts/qa_apps_smoke.py

# or against a running SSE server
uv run python scripts/qa_apps_smoke.py --sse-url http://127.0.0.1:8001/sse
```

## Existing calendar project reference

- [guinacio/mcp-google-calendar](https://github.com/guinacio/mcp-google-calendar)
