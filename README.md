# mcp-google-workspace

Production-ready Google Workspace MCP package with:

- Gmail MCP: send/read/search emails, attachment handling, label management, batch operations.
- Google Calendar MCP: events, availability, create/update/delete operations.
- Google Drive MCP: files/folders CRUD, uploads/downloads/exports, sharing permissions, Shared Drives operations.
- MCP Apps Dashboard: workspace dashboard app-layer tools/resources with interactive UI.
- Google Keep MCP: note create/get/list/delete, collaboration permissions, resources, prompts.
- Google Chat MCP: spaces and messages operations, collaboration messaging workflows.
- FastMCP advanced features: Context logging, progress updates, user elicitation, sampling, resources, and prompts.
- Composed server architecture: Gmail + Calendar + Keep + Chat mounted into one namespaced MCP server.
- Optional app-layer namespace for dashboard workflows mounted as `apps_*`.

## Requirements

- Python 3.12+
- UV package manager
- Node.js 18+ and npm (required for MCP Apps UI in `src/mcp_google_workspace/apps/ui`)
- Google Cloud OAuth desktop credentials (`credentials.json`)
- Gmail API + Google Calendar API + Google Drive API + Google Keep API + Google Chat API enabled in your Google Cloud project

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

The MCP app-layer dashboard namespace is opt-in for controlled rollout.

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
- Detail views: `get_event_detail`, `get_email_detail`, `get_email_attachment`
- Scheduling actions: `find_meeting_slots`, `create_meeting_from_slot`, `reschedule_meeting`, `cancel_meeting`, `respond_to_event`

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

Prompts:

- `compose_email_prompt`
- `reply_email_prompt`
- `summarize_inbox_prompt`
- `summarize_keep_note_prompt`
- `extract_actions_from_keep_notes_prompt`
- `draft_chat_announcement_prompt`
- `summarize_chat_thread_prompt`

## MCP Client-Dependent Features

These features depend on active MCP client support and may be silently unavailable in clients that do not implement the corresponding MCP capabilities.

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

Several tools use MCP sampling (`ctx.sample()`) to generate LLM-powered summaries within the tool response, using the host client's configured model for inference.

Sampling-powered tools:

- `gmail_summarize_email` — summarizes an email body in up to 5 bullets
- `keep_summarize_note` — summarizes a Keep note
- `chat_summarize_space_messages` — summarizes recent messages in a Chat space

**Requires:** MCP client with `sampling/createMessage` support (e.g. Claude Desktop). Without sampling support these tools will fail or return an empty summary.

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
        "ENABLE_CHAT": "false"
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

# or against a running SSE server
uv run python scripts/qa_apps_smoke.py --sse-url http://127.0.0.1:8001/sse
```

## Existing calendar project reference

- [guinacio/mcp-google-calendar](https://github.com/guinacio/mcp-google-calendar)
