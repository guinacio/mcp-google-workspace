# Rich list and detail outputs

Read-oriented tools return compact, action-ready envelopes by default. Raw provider payloads are intentionally not the standard list response; body-heavy tools provide an explicit full mode where appropriate.

| Service | List/detail output | Derived value |
| --- | --- | --- |
| Gmail | Sender identity, category, clean snippets, unread/attachment/newsletter/automation flags | `get_mail_digest`, `check_mail_updates`, and `read_emails` |
| Chat | Person-aware message authors, DM peers, member list, thread and attachment state | Cached People lookups, bounded to 10 concurrent resolutions |
| Tasks | Title, due date, overdue state, note preview, hierarchy and lifecycle state | `tasks_digest` groups overdue, upcoming, and unscheduled tasks |
| Drive | File kind, owner/modifier, sharing and lifecycle state, capability flags | No additional per-file requests; parent paths remain IDs to avoid fan-out |
| Calendar | Time window, organizer, attendee/RVSP state, meeting link, recurrence and attachment metadata | `get_calendar_digest` groups events requiring a response |
| Meet | Conference lifecycle, named participant identity, recording and transcript destinations | Uses identity data already included by Meet |
| Forms | Answers with question titles rather than opaque question IDs | One form-schema lookup per enriched response request |
| Keep | Preview text, checklist completion, attachment count and lifecycle timestamps | Detail reads retain full note text |

All compact representations preserve stable provider IDs for follow-up tools.

## MCP Apps outputs

Two tools render MCP Apps UIs: the Workspace dashboard (`apps_get_dashboard`,
`apps_get_weekly_calendar_view`, behind `ENABLE_APPS_DASHBOARD`) and the Workspace Files picker
(`files_file_manager`). Both use the stable Apps protocol (2026-01-26) and nested
`_meta.ui.resourceUri` only; the flat `ui/resourceUri` key and the old dashboard URIs are gone.
Launch results still carry text `content` for hosts without Apps support.

### Dashboard resource addressing

The dashboard subserver declares `ui://dashboard-ui`. `mount_apps_dashboard(root, server,
namespace="apps")` mounts it and rewrites the launch tools' `_meta.ui.resourceUri` to the URI the
mount serves, `ui://apps/dashboard-ui`. A plain `mount()` would not rewrite tool metadata, so use
the helper. The resource declares no `_meta.ui.csp`: the single-file bundle inlines all scripts and
styles and loads no fonts or other external resources, so the host's restrictive default CSP
applies.

### Tool visibility

`_meta.ui.visibility` is a host hint. Some hosts ignore it, so the server authorizes every call
the same way regardless of who makes it.

| Tool | Visibility | Why |
| --- | --- | --- |
| `apps_get_dashboard`, `apps_get_weekly_calendar_view` | model + app | The model opens (or reopens) a view; the view refreshes itself with the same tools. |
| `apps_get_state` | app | View-internal read. The model gets the same state from the launch tools and uses `calendar_*`/`gmail_*` for data. |
| `apps_set_state`, `apps_patch_state`, `apps_next_range`, `apps_prev_range`, `apps_today` | app | UI state writes (compare-and-set on `expected_revision`). No model use case: the model re-launches with `date_override`/`include_weekend` instead. |
| `apps_get_event_detail`, `apps_get_email_detail` | app | UI-shaped view models duplicating the canonical `calendar_*`/`gmail_*` reads the model should use. |
| `apps_get_email_attachment` | app | Returns base64 bytes for host downloads; never belongs in model context. |
| `files_file_manager` | model | Launches the picker; the picker never calls it. |
| `files_store_files` | app | The picker's upload callback; file bytes must not pass through the model. |
| `files_delete_file` | model + app | The model cleans up uploads; `get_mcp_apps_diagnostics` exercises its app callback address. |
| `files_list_files`, `files_list_files_page`, `files_read_file` | model | Model tools for using uploads; the picker does not call them. |

### Operation manifest

Launch results carry the operations the calling principal may use right now, so the view never
guesses tool names for anything that writes:

```json
"_meta": {
  "mcp-google-workspace/view": {"handle": "wsv_...", "revision": 1, "expires_at": 1790000000, "ttl_seconds": 86400},
  "mcp-google-workspace/operations": {
    "version": 1,
    "operations": {
      "getDashboard": {"tool": "apps_get_dashboard", "mutates": false},
      "nextRange": {"tool": "apps_next_range", "mutates": true},
      "createEvent": {"tool": "calendar_create_event", "mutates": true},
      "markEmailRead": {"tool": "gmail_mark_as_read", "mutates": true}
    }
  }
}
```

An operation is listed only when its tool is registered in the serving composition (looked up with
`get_tool`, so the BM25 discovery transform does not hide it), callable by an app (visibility), and
covered by the principal's Google grant (remote principals: granted capabilities, as for
`tools/list`; the trusted local user consents to the whole enabled catalog on first use). If the
grant cannot be read, no Google operation is listed. Operation ids: `getDashboard`,
`getWeeklyCalendar`, `getEventDetail`, `getEmailDetail`, `getEmailAttachment`, `listCalendars`
(reads); `patchState`, `nextRange`, `prevRange`, `today` (view state); `respondToEvent`,
`createEvent`, `updateEvent`, `deleteEvent`, `markEmailRead`, `markEmailUnread`, `moveEmail`,
`deleteEmail`, `untrashEmail`, `markEmailSpam`, `markEmailNotSpam`. `mutates` is the negation of
`readOnlyHint`. A subserver-only composition lists only the dashboard's own operations under their
local names. The view disables every write that is not in the manifest; without any manifest it
attempts reads only (by `tools/list` name or a known name). The manifest is UI metadata only; it is
never in `structuredContent`.

### Dashboard lifecycle

- All handlers (tool input, tool result, tool cancelled, host context changed, resource teardown,
  channel error) are registered before `connect()` runs the `ui/initialize` handshake.
- The host's tool input and result are the invocation context. The view renders the launch result
  and never starts a launch of its own while the host's launch call is minting the view. It loads
  by itself only when the input reopens an existing `view_handle` and no result arrives within
  1.5 s (replaying the announced launch tool from `hostContext.toolInfo`, with the input's
  arguments), or when the host announced no invocation at all within 0.75 s. Input without a
  handle and no result shows a notice after 30 s with a user-triggered "Load a new view now".
- A cancelled invocation stops loading; the user can load explicitly. A failed launch result is
  shown with an "Open a new view" action.
- Loads carry generation tickets and abort signals: the latest load of each kind wins, a host
  result supersedes an in-flight fallback, and a slow earlier detail response never replaces a
  newer selection. Navigation writes are serialized (one conditional write at a time).
- Teardown clears timers, aborts in-flight requests, detaches DOM listeners and ignores late
  results.
- Failures are typed: an `isError` result (`structuredContent.code`), a success payload carrying
  an `error` object, a rejected call (`ProtocolError` with the code from `error.data.code`;
  `SdkError` codes such as `REQUEST_TIMEOUT` or `UNSUPPORTED_RESULT_TYPE`) and host refusals are
  told apart before any success message, and optimistic changes are restored on failure. No
  message text is parsed.

### Host capability matrix

| Feature | Host capability (after `ui/initialize`) | When absent | When declined or rejected |
| --- | --- | --- | --- |
| Server tool calls, discovery | `serverTools` | Renders pushed results only; every action disabled; no `tools/list` | Typed error; optimistic state restored |
| Open links (attachments, Meet, email links) | `openLinks` | Shows the address in a copyable field | Same, with the host's reason; never `window.open` or navigation |
| Downloads (draft `ui/download-file`) | `downloadFile` | Download buttons hidden; "ask the assistant to save it to Drive" | Reported; no second attempt, no `data:` fallback; linked files offer a user-chosen "Open link instead" |
| Inline download size | none | none | Refused above 10 MiB decoded (checked before fetching when the size is known, and on the bytes) |
| Reply in chat | `message.text` | Button hidden | Reported |
| Model context | `updateModelContext.text` | Not sent | Logged only |
| Full screen | `availableDisplayModes` contains `fullscreen` | Button hidden | Reported |
| Theme, style variables, fonts, safe area | host context | Built-in dark/light palette | none |
| Container size | `containerDimensions` | Size to content | Fixed: fill and scroll inside; flexible: auto-resize reports the size |

`updateModelContext` is sent only when the user opens an event or email, with its title and ids,
so a follow-up request in chat can refer to "this email". Draft app-provided tools are not used.

### Prefab picker delivery

The picker resource serves the self-contained renderer bundled in the locked `prefab-ui` 0.20.2
(`file_uploads.configure_prefab_renderer()` sets `PREFAB_BUNDLED_RENDERER=1` for every transport;
`PREFAB_RENDERER_URL` remains prefab's development override). It declares no CSP domains. The
trade-off: the resource is about 6.6 MB instead of a small stub, in exchange for no third-party CDN,
no version drift, and no `https://cdn.jsdelivr.net` allowance (which would admit any npm package on
that CDN).

FastMCP 4.0.10 otherwise rebuilds that ~6.6 MB resource -- including a fresh disk read of the
bundled renderer HTML -- on every single `resources/list`/`resources/read`, with no supported
cache hook (see `common/prefab_render_cache.py`'s module docstring for why). This project patches
that one synthesis function with a process-wide cache keyed on the tool, the installed `prefab-ui`
version, the resolved renderer mode/`PREFAB_RENDERER_URL`, and the tool's CSP/permissions meta, cutting
a `resources/list` call from ~41ms to ~5ms after the first build (`tests/test_prefab_render_cache.py`).

### Browser test host

`src/mcp_google_workspace/apps/ui/tests` runs every dashboard test through a web-host sandbox. The
host page (127.0.0.1:4173) embeds a sandbox proxy from a different origin (localhost:4174,
`tests/sandbox-server.ts`, `sandbox="allow-scripts allow-same-origin"`). The proxy loads the view in
an inner `sandbox="allow-scripts allow-forms"` iframe (opaque origin) served with the spec's CSP as
a response header, built from the resource's declared `_meta.ui.csp`. The mock server behind the
official `AppBridge` mirrors the view-handle and manifest contracts and rejects app calls to tools
whose real visibility (exported by `scripts/export_apps_ui_fixtures.py` during Playwright global
setup) excludes `app`. `npm run typecheck` checks both `src/` and `tests/`.
