# W7 — Manual qualification checklist (owner-run)

Per the migration plan's owner decisions (`docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md`
§9.1: *"Live Google / real-host checks are run manually by the owner"*), this
checklist covers everything CI cannot: real Google API side effects and real
MCP host behavior. Everything automatable (protocol shape, confirmation
logic, error envelopes, catalog contracts, the packaged MCPB bundle's stdio
lifecycle) is already covered by `uv run pytest -q`, `tests/wire/`,
`tests/test_feature_flag_matrix.py`, and `scripts/verify_mcpb_bundle.py`
(§9.9). None of that is repeated here.

## 0. Before you start

- **Use a dedicated Google *test* account.** Never a personal or production
  account — several items send real mail, create real Drive files, and
  exercise permanent-delete paths. If your organization has a sandbox
  Workspace domain, use it.
- **Record results in this file** (or a copy of it) by replacing each `[ ]`
  with `[x]` (pass), `[x] (see note)` (pass with a caveat, add a dated note
  below the item), or `[FAIL]` (record the exact error/screenshot and file an
  issue before shipping). Add your name and the date next to the section
  heading you completed.
- **Section 1's table is the deliverable for `docs/migration/W0_BASELINE.md`
  section 6** ("Host compatibility matrix", currently all `TBD`). Copy the
  filled table from section 1 below into that file when you're done.
- Have ready: a stdio client (Claude Desktop, or `uv run python -m
  mcp_google_workspace` plus a generic stdio-capable host/inspector), an HTTP
  deployment reachable from a real host (see README "Run (Streamable HTTP)"
  — a local `ngrok`/Cloudflare Tunnel in front of `uv run python -m
  mcp_google_workspace.server_http` is sufficient for OAuth redirect and
  webhook testing), and `.env` values for at least one enabled optional
  integration (`ENABLE_KEEP`, `ENABLE_CHAT`, `ENABLE_MEET`, and
  `ENABLE_GEMINI`+`GEMINI_API_KEY`) so section 6 has something to test
  against.
- Undo every side effect you create (delete the test event, trash the test
  email, etc.) as you go — each item below has its own "Cleanup" step, but
  don't rely on it alone; leave the test account as clean as you found it.

---

## 1. Host matrix (fills `docs/migration/W0_BASELINE.md` §6)

For each host you support, connect (stdio hosts: point them at `uv run
python -m mcp_google_workspace` or an installed MCPB bundle; HTTP hosts:
point them at your deployed `MCP_HTTP_BASE_URL/mcp`) and record:

| Host | Version | Modern core MCP | Stable Apps | Elicitation | Tasks | Downloads | Sandboxed UI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Desktop | | | | | | | |
| Claude.ai | | | | | | | |
| Claude Code | | | | | | | |
| VS Code | | | | | | | |
| MCP Inspector | | | | | | | |
| ChatGPT (if used) | | | | | | | |

How to fill each column (all derived from what you directly observe, not
assumed):

- **Modern core MCP** — does the host negotiate `2026-07-28` (check
  `get_mcp_apps_diagnostics`/`get_workspace_capabilities` responds, and that
  the host's own devtools/logs show no `initialize` request for this
  connection), or does it fall back to a handshake-era version? Record the
  exact negotiated version string.
- **Stable Apps** — does the dashboard (`apps_get_dashboard`, when
  `ENABLE_APPS_DASHBOARD=true`) or the Workspace Files picker
  (`files_file_manager`) render inline, or does the host show only the text
  fallback content?
- **Elicitation** — trigger any confirmation-gated tool (e.g.
  `gmail_delete_email` with `permanent=true`, or just accept the default
  `confirm_send=true` on `gmail_send_email`) and record whether the host
  presents the confirmation prompt at all, and whether it's a native
  form/dialog or a chat message.
- **Tasks** — call a `task=True` tool (e.g. `gemini_generate_image` if
  Gemini is enabled, or `drive_upload_file` with a large local file) and
  record whether the host shows a distinct "running in background" state or
  just blocks until done.
- **Downloads** — from the dashboard, click something that uses
  `ui/download-file` (an email attachment in the email detail view) and
  record whether a file actually saves, whether the host asks for a location,
  or whether it silently does nothing.
- **Sandboxed UI** — open the dashboard and check (via the host's own
  devtools, if available) whether the app renders in a separate-origin
  iframe/sandbox, and whether any content-security-policy violation appears
  in the console.

---

## 2. Gmail Message-ID retention and `outcome_unknown` reconciliation

**Preconditions:** test account connected (`connect_google_workspace`,
capability `gmail`); a running server you can restart or reconfigure
(stdio is simplest — restart the process between steps).

### 2a. Message-ID retention on a normal send

1. Call `gmail_send_email` with `to=[<your test account's own address>]`,
   `subject="W7 QA retention check"`, `body="..."`, `confirm_send=false`
   (skips the confirmation round for this check).
2. **Expected:** the tool result's `structuredContent` includes a
   `message_id` (Gmail's internal id). Open the sent message in Gmail's web
   UI ("Show original") and confirm it carries an RFC 822 `Message-ID:`
   header — the server stamps one on every outgoing message
   (`gmail/mime_utils.py`'s `stamp_message_id`, used by `common/repeat_safety.py`
   for reconciliation).
3. **Record:** the `Message-ID` header value alongside your result.
4. **Cleanup:** trash the sent test message.

### 2b. Simulating an uncertain send (`outcome_unknown`)

`gmail_send_email` is a **standard**-cost tool
(`common/admission.tool_cost`: only `gemini`/`video`/`audio`/`export`/
`download`/`batch`-named tools are "expensive"), so its wall-clock budget is
`MCP_TOOL_DEADLINE_SECONDS` (default 120s) — not `MCP_EXPENSIVE_DEADLINE_SECONDS`,
which only bounds Gemini/export/download/batch tools. (If you'd rather
reproduce this for an *expensive*-tagged tool instead, e.g. a Drive export or
a Gemini call, use `MCP_EXPENSIVE_DEADLINE_SECONDS` the same way.)

1. Set `MCP_TOOL_DEADLINE_SECONDS=1` in the server's environment and
   restart it. One second is shorter than a real Gmail API round trip, so the
   deadline will fire while the send may already be in flight on Google's
   side — exactly the "timeout while the non-idempotent call may have been
   applied" scenario `common/repeat_safety.py` guards.
2. Call `gmail_send_email` again with a **new, distinguishable** subject
   (e.g. `"W7 QA outcome_unknown check <timestamp>"`), `confirm_send=false`.
3. **Expected:** the tool result is an `isError: true` tool result (not a
   JSON-RPC error) with `structuredContent.code == "outcome_unknown"`, and
   `structuredContent.required_action.verify` contains one step shaped like:
   ```json
   {"tool": "gmail_search_emails",
    "arguments": {"query": "in:sent rfc822msgid:<generated-message-id>"},
    "check": "A match means the email was sent; do not send it again."}
   ```
   (`common/operations.py`'s `_hint_step`, fed by the `rfc822_message_id`
   the tool tracked *before* calling Gmail — see `gmail/tools/messages.py`'s
   `track_call({"kind": "gmail_sent", "rfc822_message_id": ...})`.)
4. Run the exact `gmail_search_emails` call from `required_action.verify`
   (restore `MCP_TOOL_DEADLINE_SECONDS` to its normal value first — you only
   need the short deadline for the send itself). **Record** whether a match
   was found (i.e., whether the uncertain send actually went through) and
   whether the reconciliation step's `query` was accurate and immediately
   actionable without any manual editing.
5. **Cleanup:** restore `MCP_TOOL_DEADLINE_SECONDS` (unset it, or set it back
   to your deployment's real value) and restart the server; trash whichever
   test message(s) were actually sent.

---

## 3. Drive resumable upload

**Preconditions:** test account connected with `drive` capability.

1. Prepare a local file **larger than 100 MiB** (googleapiclient's
   `MediaFileUpload` default chunk size —
   `.venv/…/googleapiclient/http.py`'s `DEFAULT_CHUNK_SIZE = 100 * 1024 * 1024`
   — so the upload takes more than one `next_chunk()` round trip and you can
   actually observe intermediate progress, not just a single 0%→100% jump).
   A smaller file (a few MB) still exercises the resumable code path and is
   an acceptable substitute if you can't produce a 100+ MiB fixture, but note
   in your result that you only validated single-chunk completion.
2. Call `drive_upload_file` with `local_path=<the file>`, `resumable=true`
   (the default — pass it explicitly for clarity), and a destination folder
   in your test account.
3. **Expected:** if your host surfaces MCP progress notifications, you see
   multiple progress updates with increasing percentages
   (`_execute_resumable_upload_with_progress` in `drive/tools/files.py`
   reports progress after every chunk). The final result is a normal
   `drive_upload_file` success envelope with the created file's id.
4. **Interrupt-and-resume (optional but valuable):** if your network setup
   allows it, disconnect networking partway through the upload (after at
   least one progress update) and reconnect. Record whether the call
   ultimately completes, times out, or fails outright, and whether a partial
   file was left behind in Drive (check by listing the destination folder).
5. Verify the uploaded file's size in Drive matches the local file's size
   exactly.
6. **Cleanup:** `drive_delete_file` the uploaded test file
   (`delete_mode="permanent"` if you don't want it left in Trash).

---

## 4. Real-host feature checks

Preconditions: run this against each host you completed section 1 for,
noting host-specific differences.

### 4a. Modern vs legacy protocol negotiation

1. Connect the host normally and, using whatever request-inspection the host
   or an intermediary proxy offers, confirm the negotiated `MCP-Protocol-Version`
   (HTTP) or the absence of an `initialize` call (modern stdio: modern MCP
   drops the `initialize`/`notifications/initialized` exchange entirely).
2. **Record:** the exact version string, and whether the host behaves
   differently (missing features, extra prompts) versus a host you know
   negotiates the other family.

### 4b. MRTR confirmation in chat

1. Ask the assistant, in normal chat, to do something confirmation-gated —
   e.g. "send a test email to myself" (`gmail_send_email`, default
   `confirm_send=true`) or "delete this test contact"
   (`people_delete_contact`).
2. **Expected:** the host presents a native confirmation UI (not raw JSON)
   before anything happens; declining performs no mutation; accepting
   performs exactly one.
3. **Record:** whether the host's confirmation UI clearly states what will
   happen (matches the tool's preview text) and whether accept/decline both
   work as expected.

### 4c. Confirmation started from the dashboard

Requires `ENABLE_APPS_DASHBOARD=true` and a host with Stable Apps support
(section 1).

1. Open the dashboard (ask the assistant to show your calendar/inbox, or
   call `apps_get_dashboard` directly) and navigate to a test calendar
   event's detail view.
2. Click the delete action. This calls `calendar_delete_event` through the
   app's `callServerTool` (`apps/ui/src/mcp-app.ts`'s `deleteEvent`
   operation), which is one of the 24 confirmation-gated tools — the same
   MRTR adapter as any model-initiated call.
3. **Expected:** either (a) the host completes the `input_required` round on
   the app's behalf — typically by surfacing a confirmation prompt somewhere
   in its UI (in-app or in the surrounding chat) — and the event is deleted
   only after you confirm, or (b) if the host doesn't support completing an
   app-initiated MRTR round, the app receives a typed `SdkError` ("unsupported
   result type") and shows an explicit failure — **never** a silent no-op
   and never an optimistic "deleted" message with the event still present.
4. **Record** which of (a)/(b) happened, and if (a), whether the dashboard's
   own state (the event list) updated correctly afterward.
5. **Cleanup:** if the event was actually deleted, no cleanup needed;
   otherwise delete it manually.

### 4d. `ui/download-file` support

Requires the dashboard and a test email with an attachment.

1. Open the dashboard's email detail view for a message with an attachment
   and trigger the download action.
2. **Expected (per `docs/RICH_OUTPUTS.md`'s host capability matrix):** if the
   host declares the `downloadFile` capability, the file downloads (bounded
   to 10 MiB decoded, checked both from the declared size and the actual
   bytes); if the host does not declare it, the download button is hidden and
   an alternative ("ask the assistant to save it to Drive") is shown instead.
   Never a silent failure.
3. **Record:** which behavior you observed, and — if the download worked —
   whether the saved file's bytes match the original attachment.

### 4e. `openLink`

1. From the dashboard, trigger something that opens an external link (an
   email's sender/link, or a Meet conference link if `ENABLE_MEET=true`).
2. **Expected:** the host mediates the navigation (via `app.openLink`, per
   `apps/ui/src/mcp-app.ts`) rather than the app calling `window.open`
   directly; if the host doesn't declare `openLinks`, the URL is shown in a
   copyable text field instead.
3. **Record:** which behavior you observed, and whether a host that blocks
   popups still lets you reach the link (e.g. via the copyable-field
   fallback).

### 4f. App-only visibility respected

1. Ask the assistant (in chat, i.e. as the *model*) to call an app-only tool
   directly by name — e.g. "call `apps_set_state`" or "call
   `files_store_files`".
2. **Expected:** the host either doesn't offer these tools to the model at
   all (since `_meta.ui.visibility` excludes `"model"`), or if it does list
   them despite the hint, the call is still authorized server-side the same
   as any other call (visibility is a host hint, not authorization — see
   `docs/RICH_OUTPUTS.md` "Tool visibility") — but the model should not be
   *routinely offered* these as chat actions.
3. **Record:** whether the host respects `visibility` in what it exposes to
   the model.

### 4g. Dashboard rendering in the host sandbox

1. Open the dashboard and visually compare against the host's normal
   theme (light/dark, fonts, safe-area insets on a narrow window).
2. **Expected:** the dashboard adopts the host's theme/colors/fonts (no
   flash of unstyled content, no mismatched color scheme) and stays usable
   at a narrow width.
3. **Record:** any visual glitch, layout break, or theme mismatch, with a
   screenshot.

### 4h. Picker under the host CSP

1. Open `files_file_manager` (the Workspace Files picker) in the host and,
   if the host exposes any console/network inspection, confirm no requests
   go to `cdn.jsdelivr.net` or any other external origin (the picker serves
   prefab-ui's bundled renderer, `PREFAB_BUNDLED_RENDERER=1`, with no CSP
   domains declared — see `docs/RICH_OUTPUTS.md` "Prefab picker delivery").
2. Drop a small test file and confirm it uploads and appears in the list.
3. **Record:** whether the picker rendered at all, and whether any CSP
   violation appeared in the host's console.
4. Upload a file larger than 1 MB (for example a 3 MB photo), then a small
   one. Both must succeed (regression check for the base64 string limit).

### 4i. Token encryption key in the OS keychain (MCPB)

1. Install the 1.0.0 MCPB. Confirm the host's extension settings do **not**
   ask for a token encryption key.
2. Connect Google (`connect_google_workspace`) and run one read tool.
3. Confirm the keychain entry exists: Windows Credential Manager → Windows
   Credentials → `mcp-google-workspace` / `token-encryption-key`; macOS
   Keychain Access → search `mcp-google-workspace`; Linux `secret-tool search
   service mcp-google-workspace`. Confirm no key file appears in
   `user_token_dir` (only encrypted `.token` files).
4. Restart the host. The read tool still works without reconnecting.
5. **Cleanup/negative check:** delete the keychain entry and restart. The
   next Google call asks you to reconnect (old tokens are unreadable); after
   reconnecting, a new entry exists.
6. **Record:** OS, host version, and each step's result.

---

## 5. Google OAuth incremental consent (HTTP mode)

**Preconditions:** a deployed Streamable HTTP server (`MCP_HTTP_BASE_URL`
reachable from your browser, OIDC issuer configured) and a test MCP user
that has **not yet** connected any Google capability.

1. Call `connect_google_workspace({"capabilities": ["gmail"]})`.
2. **Expected:** a consent URL scoped to only the Gmail scopes
   (`auth/google_auth.py`'s `CAPABILITY_SCOPES["gmail"]`). Open it, sign in
   with the test Google account, and approve.
3. Call `get_google_connection_status({"capability": "gmail"})` — expect
   granted; call `get_google_connection_status({"capability": "calendar"})`
   — expect **not** granted.
4. Call `tools/list` (or ask the assistant what it can do) — Calendar tools
   should not yet be authorized.
5. Call `connect_google_workspace({"capabilities": ["calendar"]})` again.
6. **Expected:** a **new** consent URL requesting only the additional
   Calendar scopes (incremental — Google does not re-prompt for Gmail).
   Approve it.
7. Call `get_google_connection_status({"capability": "gmail"})` again —
   still expect granted (cumulative, not overwritten by the second consent —
   `auth/google_oauth.py`'s `_cumulative_authorization_scopes`); and
   `get_google_connection_status({"capability": "calendar"})` — now
   granted.
8. Call `refresh_workspace_catalog`, then `tools/list` again — Calendar
   tools should now be authorized alongside Gmail's.
9. **Record:** whether both consent screens correctly scoped their
   requested permissions (Google's own consent screen shows the exact scopes
   being requested — confirm Gmail's second screen did **not** re-request
   Gmail scopes), and whether the cumulative grant behaved as described.
10. **Cleanup:** call `disconnect_google_workspace({"confirm": true})` to
    remove the test grant, or leave it connected if you'll reuse this test
    account for section 6.

---

## 6. One read, one mutation and one provider error per enabled integration

Per plan section 8's minimum service coverage table. Enable each optional
integration you support (`ENABLE_KEEP`, `ENABLE_CHAT`, `ENABLE_MEET`,
`ENABLE_GEMINI`+`GEMINI_API_KEY`) before its row; the always-on services
need no flag.

| Service | Read | Mutation | Provider error (how to force it) |
| --- | --- | --- | --- |
| Gmail | `gmail_search_emails` (any query) | `gmail_send_email` to self, `confirm_send=false` | `gmail_read_emails` with a made-up `message_ids` value → expect `missing_message_ids` in the result, not a crash |
| Calendar | `calendar_search_events` (primary, next 7 days) | `calendar_create_event` ("W7 QA event", +1h), then `calendar_delete_event` cleanup | `calendar_search_events` with `calendar_id="does-not-exist@group.calendar.google.com"` → expect a structured `not_found`/`provider_tool_error` result |
| Drive | `drive_list_files` (`page_size=5`) | `drive_create_folder` ("W7 QA Folder"), then `drive_delete_file` cleanup | `drive_get_file` with a bogus `file_id` → expect a structured 404 error result |
| Sheets | `sheets_get_spreadsheet` on a test sheet | one `sheets_batch_update_spreadsheet` request (e.g. `addSheet`), then delete the added sheet in a follow-up batch update | `sheets_get_spreadsheet` with a bogus `spreadsheet_id` → expect a structured error |
| Docs | `docs_get_document` on a test doc | `docs_batch_update_document` (`insertText`), then a follow-up batch update to remove it | `docs_get_document` with a bogus `document_id` → expect a structured error |
| Forms | `forms_get_form` on a test form | `forms_batch_update_form` (e.g. add a text question), then remove it | `forms_get_form` with a bogus `form_id` → expect a structured error |
| Slides | `slides_get_presentation` on a test deck | `slides_batch_update_presentation` (e.g. `createSlide`), then delete the added slide | `slides_get_presentation` with a bogus `presentation_id` → expect a structured error |
| Google Tasks | `tasks_list_tasklists` | create a task, then `tasks_delete_task` cleanup | an operation on a bogus `task_id`/`tasklist_id` → expect a structured error |
| People | `people_list_contacts` | create a test contact, then `people_delete_contact` cleanup (confirmation-gated — accept it) | `people_delete_contact` on a bogus `person_name` → expect a structured error |
| Keep (`ENABLE_KEEP`) | `keep_list_notes` | `keep_create_note` ("W7 QA note", `confirm_create=false`), then `keep_delete_note` cleanup (`confirm_delete=false`) | `keep_get_note` with a bogus `note_name` → expect a structured 404 (`docs/migration/W4_CATALOG_DIFF.md`'s `provider_tool_error` conversion) |
| Chat (`ENABLE_CHAT`) | `chat_list_spaces` | `chat_create_message` in a test space, `notify=false`, then `chat_delete_message` cleanup (accept the confirmation) | `chat_get_space` with a bogus space name → expect a structured error |
| Meet (`ENABLE_MEET`) | `meet_list_conference_records` | `meet_create_space`, then note the created space is not independently deletable via the Meet API — record any cleanup limitation you find | `meet_get_space` with a bogus space name → expect a structured error, and confirm the response actually matches the resource schema (a latent bug meant this previously returned a schema-violating closed object — see `docs/migration/W5_CATALOG_DIFF.md` §2) |
| Gemini (`ENABLE_GEMINI`) | none (no read-only Gemini tool exists) — instead run `gemini_generate_image` with a trivial prompt and note it as the representative call | `gemini_generate_image` (a mutation in the sense that it costs money/quota and writes a local output file); delete the local output file afterward | call any Gemini tool with `GEMINI_API_KEY` temporarily unset/invalid → expect a structured provider error, not a crash; also verify a long-running Gemini call surfaces as a background task if your host supports Tasks (section 1) |

For each cell: **record** pass/fail, the exact structured error `code` you
observed (for the provider-error column), and clean up any created resource
immediately after testing its row.

---

## Sign-off

| Section | Completed by | Date | Notes |
| --- | --- | --- | --- |
| 1. Host matrix | | | |
| 2. Gmail Message-ID / outcome_unknown | | | |
| 3. Drive resumable upload | | | |
| 4. Real-host feature checks | | | |
| 5. OAuth incremental consent | | | |
| 6. Per-service read/mutation/error | | | |

Once every row above is filled and every `[FAIL]` is either fixed or
explicitly accepted as a known limitation, copy the section 1 table into
`docs/migration/W0_BASELINE.md` section 6 and this release is qualified for
the hosts you tested.
