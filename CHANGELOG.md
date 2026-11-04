# Changelog

All notable changes to `mcp-google-workspace` are recorded here. Dates are
UTC. Entries trace to `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` (the
migration plan; sections cited below are `9.x` implementation records unless
noted) and its `docs/migration/W*_CATALOG_DIFF.md` contract diffs.

## 2.0.1 — Unreleased

Polish from the 2.0.0 live QA (issue #63) and the cheap items left over from
issue #57. Response changes are additive except where a "Changed" entry says
otherwise; no tool, parameter or dependency is added or removed.

### Added

- **`docs_read_document` `format: outline` is paged** like the text formats.
  A page holds about 16,000 characters of JSON (`OUTLINE_LIMIT_CHARS`; the
  outline is about twice the size of the text, so a page covers about as much
  of the document as an 8,000-character text page) and is cut only between
  structural elements, so a paragraph or table is never split; an element
  larger than the budget comes alone on its page. The result gains
  `truncated`, `next_offset`, `element_count`, `outline_chars` and
  `outline_sha256` (size and hash of the whole outline, to tell whether the
  document changed between pages, since `revision_id` is null for viewers).
  `offset` for the outline counts structural elements already read, across
  tabs in reading order, not characters: an element index always falls on a
  boundary and does not depend on how the JSON is serialized. A client
  continues by calling again with `offset: next_offset` until it is null. A
  tab's `headers`, `footers`, `footnotes` and `named_ranges` come with its
  first element; a tab begun on an earlier page has `continued: true` and its
  `content` only.
- **The list envelope on the remaining list-shaped reads.**
  `gmail_read_emails` gains `count`, `has_more` (always false),
  `next_page_token` (null) and `fetched_at` beside `missing_count` and
  `missing_message_ids`, which stay. `calendar_get_calendar_context` gains
  `count` (the calendars listed; 0 with `include_calendars: false`),
  `has_more` (false: every page is read), `next_page_token` (null) and
  `fetched_at`. `uploads_list_files` gains `has_more`, `next_page_token` (the
  same value as `next_cursor`, which stays) and `fetched_at`.
- **`calendar_find_free_time`** gains `count`, the number of
  `suggested_slots`; `total_suggestions` stays with the same value.

### Changed

- **`calendar_get_calendar_context` `timezone_name`** is the IANA zone name
  (the same value as `timezone`) instead of the abbreviation Python's
  `tzname()` gave, which for many zones is only an offset such as `"-03"`; the
  offset stays in `utc_offset`. `utc_datetime` keeps the value of
  `current_utc_time` and is now described as its alias rather than removed.

- **`docs_read_document` outline of a long document** returns its first page
  instead of the whole outline (a document whose outline fits in one page is
  unchanged apart from the new fields above), and `offset`, ignored for the
  outline before, now selects the page.

### Fixed

- **Descriptions.** `docs_read_document` says `revision_id` is null unless
  the user can edit the document (the Docs API returns `revisionId` only to
  editors). `docs_read_document` and `drive_read_file` say that their text,
  and so `body_chars`, differ for the same Doc: the first renders the
  Document resource itself, the second returns Google's text export. The
  renderers stay separate: sharing one would make `drive_read_file` call the
  Docs API (another scope and request) or drop the Docs renderer's tabs,
  chips and footnotes.

## 2.0.0 — 2026-11-04

A smaller catalog organised around tasks, one consent model, and one response
shape. Tools are consolidated (140 in 1.1.0 to 82 by default) and grouped in
tiers; every call that sends, shares, permanently deletes or acts in bulk asks
the user through one table, with a prepare/commit fallback for clients that
cannot ask; every response is normalized by the server (snake_case, local
times, compact resource handles, one list envelope) and publishes an open
output schema. Every tool and parameter description is written for the tool,
schemas stop repeating generic limits and `null` alternatives, and API
plumbing parameters are gone. Docs and Drive files can be read as text. The
default catalog shrinks from about 89,000 to about 46,000 estimated tokens
(characters / 4). No dependency changes.

### Breaking changes

Input schemas are closed (`additionalProperties: false`), so a caller that
still sends a removed parameter gets the normal input-validation error. Some
entries below describe a change by the tool's 1.1.0 name; the Retired tools
table gives each retired name's replacement.

Upgrading from 1.1.0 needs no reconnect and loses no stored state: the OAuth
scopes, the token store and the upload and operation stores are unchanged.
The one exception is `MCP_TOOL_TIER=complete`, which turns on Chat, Keep and
Meet unless their `ENABLE_*` flag is `false`, so their scopes are requested on
the next connection. An action prepared or a confirmation left pending under
1.1.0 (both expire after 10 minutes by default) may name a retired tool or a
removed flag; prepare or call it again.

#### Tool consolidation

The catalog is consolidated around tasks instead of Google API methods: the
default catalog goes from 140 tools in 1.1.0 (142 with the two new read tools)
to 82 (80 for the model plus the two app-only upload callbacks), Gmail from 44
tools to 16 (19 with the extended tier). Merged tools take the union of their
predecessors' parameters, typed, and return the same envelope shapes. Retired
names are removed outright: they are absent from `tools/list` and a call to
one is the normal unknown-tool error.

#### Tool tiers

`MCP_TOOL_TIER` selects how much of the catalog a deployment publishes:

- `core` (default): the everyday tools of every core service.
- `extended`: core plus the raw `docs_batch_update_document`,
  `sheets_batch_update_spreadsheet`, `slides_batch_update_presentation` and
  `forms_batch_update_form`, the Gmail settings tools
  (`gmail_manage_filters`, `gmail_manage_forwarding`,
  `gmail_manage_vacation`), `drive_update_drive` and
  `get_mcp_apps_diagnostics` (82 to 91 tools).
- `complete`: extended plus every optional integration (Apps dashboard,
  Chat, Keep, Meet, and Gemini when `GEMINI_API_KEY` is set) whose `ENABLE_*`
  flag is not explicitly `false` (143 tools with all of them on). Under
  `core` and `extended` an unset `ENABLE_*` flag is off, as in 1.1.0.

Tools above the active tier are not registered at all, and
`prepare_workspace_action` only offers tools of the active tier.
`get_google_connection_status` reports the active tier under
`server.tool_tier`. An unknown value fails at startup.

#### Retired tools

| Retired | Replacement | How |
|---|---|---|
| `gmail_mark_as_read` | `gmail_modify_messages` | `mark_read: true` |
| `gmail_mark_as_unread` | `gmail_modify_messages` | `mark_read: false` |
| `gmail_mark_as_spam` | `gmail_modify_messages` | `spam: true` |
| `gmail_mark_as_not_spam` | `gmail_modify_messages` | `spam: false` (adds INBOX unless `archive: true`) |
| `gmail_move_email` | `gmail_modify_messages` | `archive`, `add_labels`, `remove_labels` |
| `gmail_apply_labels` | `gmail_modify_messages` | `add_labels`, `remove_labels` (names or IDs) |
| `gmail_batch_modify` | `gmail_modify_messages` | `message_ids` with several IDs |
| `gmail_modify_thread` | `gmail_modify_messages` | `thread_id` |
| `gmail_untrash_email` | `gmail_modify_messages` | `untrash: true` |
| `gmail_untrash_thread` | `gmail_modify_messages` | `thread_id`, `untrash: true` |
| `gmail_delete_email` | `gmail_delete` | `mode: trash` (default) or `permanent` |
| `gmail_batch_delete` | `gmail_delete` | `message_ids` with several IDs |
| `gmail_delete_thread` | `gmail_delete` | `thread_id`, `mode: permanent` |
| `gmail_trash_thread` | `gmail_delete` | `thread_id` (default `mode: trash`) |
| `gmail_reply_email` | `gmail_reply` | `reply_all: false` (default) |
| `gmail_reply_all_email` | `gmail_reply` | `reply_all: true` |
| `gmail_list_threads` | `gmail_search_emails` | `group_by_thread: true` |
| `gmail_list_attachments` | `gmail_read_emails` | `attachments[].download_id` |
| `gmail_create_draft` | `gmail_save_draft` | without `draft_id` |
| `gmail_update_draft` | `gmail_save_draft` | with `draft_id` |
| `gmail_get_draft` | `gmail_list_drafts` | `draft_id` |
| `gmail_create_label` | `gmail_manage_labels` | `action: create` |
| `gmail_update_label` | `gmail_manage_labels` | `action: update` |
| `gmail_delete_label` | `gmail_manage_labels` | `action: delete` |
| `gmail_list_filters` | `gmail_manage_filters` | `action: list` (extended tier) |
| `gmail_create_filter` | `gmail_manage_filters` | `action: create` (extended tier) |
| `gmail_delete_filter` | `gmail_manage_filters` | `action: delete` (extended tier) |
| `gmail_list_forwarding_addresses` | `gmail_manage_forwarding` | `action: list` (extended tier) |
| `gmail_get_forwarding_address` | `gmail_manage_forwarding` | `action: get` (extended tier) |
| `gmail_create_forwarding_address` | `gmail_manage_forwarding` | `action: create` (extended tier) |
| `gmail_delete_forwarding_address` | `gmail_manage_forwarding` | `action: delete` (extended tier) |
| `gmail_get_vacation_settings` | `gmail_manage_vacation` | `action: get` (extended tier) |
| `gmail_update_vacation_settings` | `gmail_manage_vacation` | `action: update` (extended tier) |
| `drive_create_permission` | `drive_share_file` | without `permission_id` |
| `drive_update_permission` | `drive_share_file` | with `permission_id` |
| `drive_delete_permission` | `drive_unshare_file` | same arguments |
| `drive_get_permission` | `drive_list_permissions` | `permission_id` |
| `drive_get_drive` | `drive_list_drives` | `drive_id` |
| `drive_hide_drive` | `drive_update_drive` | `hidden: true` (extended tier) |
| `drive_unhide_drive` | `drive_update_drive` | `hidden: false` (extended tier) |
| `drive_get_file_content_capabilities` | `drive_get_file` | `is_google_native`, `can_download`, `export_formats` |
| `drive_update_file_content` | `drive_upload_file` | `file_id` |
| `drive_create_file_metadata` | `drive_upload_file` | no `local_path`/`uploaded_file` |
| `drive_export_google_file` | `drive_download_file` | `export_mime_type` (Google files export by default) |
| `calendar_list_calendars` | `calendar_get_calendar_context` | `calendars` (`include_calendars`, default true) |
| `calendar_check_time_availability` | `calendar_find_free_time` | `all_free`, `calendars[].busy` |
| `calendar_find_common_free_slots` | `calendar_find_free_time` | `suggested_slots` |
| `calendar_add_event_attachment` | `calendar_set_event_attachments` | `add` |
| `calendar_remove_event_attachment` | `calendar_set_event_attachments` | `remove` |
| `calendar_download_event_attachment` | `drive_download_file` | the attachment's `file_id` from `calendar_read_events` |
| `tasks_get_tasklist` | `tasks_list_tasklists` | `tasklist_id` |
| `tasks_get_task` | `tasks_list_tasks` | `task_id` |
| `tasks_complete_task` | `tasks_update_task` | `status: completed` (records now unless `completed` is given) |
| `tasks_move_task` | `tasks_update_task` | `parent`, `previous`, `destination_tasklist_id`, `move_to_top` |
| `people_list_contacts` | `people_search_contacts` | no `query` |
| `people_list_contact_groups` | `people_manage_contact_groups` | `action: list` |
| `people_create_contact_group` | `people_manage_contact_groups` | `action: create` |
| `people_modify_contact_group_members` | `people_manage_contact_groups` | `action: modify_members` |
| `docs_get_document` | `docs_read_document` | `format: outline` (`format: raw` is Google's Document) |
| `sheets_get_sheet_values` | `sheets_read_sheet_values` | `ranges` with one range |
| `sheets_batch_get_sheet_values` | `sheets_read_sheet_values` | `ranges` |
| `slides_get_presentation` | `slides_read_presentation` | the outline (default) |
| `slides_get_slide_page` | `slides_read_presentation` | `slide_ids` |
| `forms_get_form_response` | `forms_list_form_responses` | `response_id` |
| `files_file_manager` | `uploads_file_manager` | renamed |
| `files_list_files` | `uploads_list_files` | one paged listing (`limit`, `cursor`) |
| `files_list_files_page` | `uploads_list_files` | same parameters |
| `files_read_file` | `uploads_read_file` | renamed |
| `files_store_files` | `uploads_store_files` | renamed; app-only callback |
| `files_delete_file` | `uploads_delete_file` | app-only callback (uploads expire on their own) |
| `get_workspace_capabilities` | `get_google_connection_status` | `server` |
| `refresh_workspace_catalog` | `get_google_connection_status` | `granted_capabilities`; list tools again after consent |

#### Changed parameters and results

- **Gmail labels by name.** Every Gmail tool that takes labels
  (`gmail_search_emails`, `gmail_modify_messages`, `gmail_manage_labels`,
  `gmail_manage_filters`) accepts label names as shown in Gmail or label
  IDs. System labels (INBOX, UNREAD, STARRED, SPAM, TRASH, ...) match
  case-insensitively without a request; other names cost one
  `users.labels.list` per call. An unknown or ambiguous name fails with
  `not_found`, the candidate labels in `details.candidates`, and nothing
  changed; a label is never guessed or created. `gmail_search_emails`
  `label_ids` is renamed `labels`; filter actions take `add_labels` and
  `remove_labels`.
- **`gmail_delete`** defaults to `mode: trash`, so deleting a thread
  permanently (the old `gmail_delete_thread`) needs `mode: permanent`.
- **`gmail_list_drafts`** list items are `{id, message_id, thread_id}`;
  `gmail_save_draft` and `gmail_manage_labels` results gain `status` (and
  `mode` or `action`).
- **`calendar_create_event` / `calendar_update_event`**: the raw
  `conference_data` object becomes `add_meet_link: bool`, and the raw
  `reminders` object becomes `reminder_minutes: list[int]` (popup reminders;
  `[]` means none). Email reminders and non-Meet conferencing are no longer
  expressible.
- **`calendar_find_free_time`** returns `all_free`, `calendars` (request
  order, each with `id`, `free`, `busy`, `errors`), `suggested_slots` and
  `total_suggestions`; every time is in `time_zone` (default the account's),
  and working hours apply in that zone.
- **`drive_list_files`** drops `corpora` and `spaces` (all drives are
  searched, or the one `drive_id` names) and gains `folder_id`, `kind`,
  `modified_after` and `full_text` filters. Without `drive_id` it now
  searches shared drives too; before, it searched only the user's corpus.
- **`drive_upload_file`** with `if_exists: skip` reports `status: ok`,
  `mode: skipped` and the existing `file` (was `status: skipped`,
  `existing_file`); new modes `replace` and `metadata_only`.
- **`drive_download_file`** reports the source `mime_type` and the target
  `export_mime_type`; `drive_get_file` drops Google's export URL map in
  favour of `export_formats`.
- **`docs_read_document`** gains `format: outline` (the former
  `docs_get_document` result), `include_tabs_content` (default true in every
  format) and `suggestions_view_mode`.
- **`sheets_read_sheet_values`** always returns `value_ranges`, one entry per
  requested range.
- **`people_search_contacts`** without `query` lists contacts (the former
  `people_list_contacts`, paged, with `sort_order`); with `query` it searches,
  returns at most 30 matches without paging, and rejects `page_token` and
  `sort_order`. Both return `contacts`, `count`, `total` and
  `next_page_token` (the last two null for a search).
- **`uploads_list_files`** is paged and returns `{files, count,
  next_cursor}` instead of a bare list.
- **`get_google_connection_status`** gains `server` (tier, namespaces,
  capabilities, file inputs, tool search); the old catalog-refresh
  `grant_revision` is gone.

#### Consent for consequential calls

One table (`CONSENT_TABLE` in `common/consent.py`, described in
`docs/CONSENT.md`) classifies every call, from its tool and validated
arguments, as read, write or consequential. Consequential calls (sending mail,
replies, sent drafts, invitations and Chat messages; sharing and unsharing;
permanent deletion; forwarding, filters that forward or trash, turning the
auto-reply on; disconnecting; 10 or more items in one call) always need
consent, and no argument can skip it. The confirmation guard asks before the
tool body runs, on every path (direct calls, the `call_tool` search proxy,
commits, background tasks); no tool asks by itself any more.

- **Clients with elicitation** are asked as before, with a preview that says
  exactly who, what and how many.
- **Clients without elicitation** no longer hit a dead end: the call fails
  with `confirmation_required` and `required_action` names
  `prepare_workspace_action` with the call's arguments. Prepare returns the
  same preview and a one-time token; `commit_workspace_action` runs exactly
  that call once. The host's permission prompt on `commit_workspace_action`
  is the human gate. A client that can elicit is still asked when it commits.
- A task-capable call from a client that could never answer the task's
  question is refused at submission with the same prepare step, instead of
  parking a task until it expires.
- `MCP_CONSENT=required|preview_only|off` (default `required`):
  `preview_only` never elicits and routes every consequential call through
  prepare/commit; `off` runs them without asking and logs a warning at every
  start. An unknown value fails at startup.
- `MCP_EMAIL_PREPARE_RECIPIENTS` (default 10) now only adds a "large send"
  line to a send preview; every send asks.
- `dry_run: true` on `gmail_modify_messages`, `gmail_delete`,
  `docs_batch_update_document`, `sheets_batch_update_spreadsheet`,
  `slides_batch_update_presentation` and `forms_batch_update_form` returns
  `{status: "dry_run", preview, consent_required, action}` without any Google
  call.
- `readOnlyHint`, `destructiveHint` and `idempotentHint` come from the table
  instead of name prefixes: `destructiveHint` is true for every tool some call
  of which needs consent.

What changes for callers:

- **Model-settable safety flags removed.** Each was a way for the
  caller to skip or weaken the user's confirmation; whether a call asks now
  depends only on what it does.
  - `confirm_send` from `gmail_send_email` and `gmail_reply` (every send and
    reply asks).
  - `force` from `calendar_delete_event` and `chat_delete_message` (deleting
    always asks).
  - `confirm_permanent` from `drive_delete_file` (`delete_mode: permanent`
    asks).
  - `notify` from `chat_create_message`, `chat_post_message_simple` and
    `chat_reply_to_message` (every post asks).
  - `confirm_create` from `keep_create_note` (asks when it shares the note
    with collaborators) and `confirm_delete` from `keep_delete_note` (always
    asks).
  - `confirm` from `disconnect_google_workspace` (asks).
- **Calls that now ask** (through elicitation, or prepare/commit for
  clients without it), with what they did before:
  - `gmail_send_email`: asked only with `confirm_send: true`, and 10 or more
    recipients needed prepare/commit; now every send asks.
  - `gmail_reply`: asked only with `confirm_send: true`; now always.
  - `gmail_send_draft`: never asked; now always, with the draft's recipients.
  - `gmail_delete` with `mode: trash` and 10 or more distinct `message_ids`:
    never asked; now asks (a thread, and fewer messages, still do not).
  - `gmail_modify_messages` with 10 or more distinct `message_ids`
    (including restoring from Trash): needed prepare/commit, which an
    elicitation-capable client could pass without being asked; now asks.
  - `gmail_manage_filters` `create` with `forward` or a `TRASH` label,
    `gmail_manage_forwarding` `create`, and `gmail_manage_vacation` `update`
    that turns the auto-reply on: never asked; now ask.
  - `calendar_create_event` with attendees: never asked; now asks.
  - `calendar_update_event`: only `recurrence` needed prepare/commit; now it
    asks when it sets `attendees` or `recurrence` or when `send_updates` is
    `all` or `externalOnly`.
  - `calendar_set_event_attachments`: asked when removing; now also when
    `send_updates` emails guests.
  - `drive_share_file` with `type: anyone`: asked and also needed
    prepare/commit; now asks once, like every other share.
  - `docs_`, `sheets_`, `slides_` and `forms_batch_update_*`: only Sheets with
    10 or more requests needed prepare/commit; now each asks with 10 or more
    requests or any `delete*` request.
  - `tasks_update_task` with `deleted: true`: never asked; now asks like
    `tasks_delete_task`.
  - `keep_share_note`, `keep_unshare_note`, and `keep_append_note_content` /
    `keep_patch_note_checklist` with `delete_original_on_apply`: never asked;
    now ask. `meet_end_active_conference` and `meet_update_space` opening the
    space to anyone (`accessType: OPEN`): never asked; now ask.
  - The Apps dashboard no longer passes `force` when it cancels an event.
    Cancelling an event, and saving one with guests, from the dashboard now
    asks through the host; a host that cannot complete the question shows the
    view's "ask the assistant in chat" message and nothing changes.
- **Changed contract.**
  - `confirmation_required`: `required_action` is the
    `prepare_workspace_action` call (`{tool, arguments: {tool_name,
    arguments}}`) instead of `{action: "request_host_confirmation",
    operation, prompt}`; the action name and preview text are in
    `details.action` and `details.preview`.
  - The `prepare_required` error code is gone; those calls return
    `confirmation_required` like every other consequential call.
  - `prepare_workspace_action` accepts any tool some call of which is
    consequential (its `tool_name` enum is gone), validates the arguments like
    a direct call, refuses a call that needs no consent with `invalid_input`
    naming the direct call, and returns `action` and `preview` instead of
    `impact`.
  - A declined or cancelled confirmation returns `{status: "cancelled",
    action, reason}` for every tool instead of each tool's own shape.
  - The preview texts changed (they now list every recipient, the total, the
    subject, the start of the text, roles and counts).
  - A call whose arguments are invalid in a way only the tool body checks
    (for example `drive_share_file` mixing create and update parameters) is
    asked about first and then fails with the same validation error; nothing
    changes either way.
- **Annotations from the consent table.** `destructiveHint` becomes true for
  `gmail_send_email`, `gmail_reply`, `gmail_send_draft`,
  `gmail_modify_messages`, `gmail_manage_vacation`, `calendar_create_event`,
  `calendar_update_event`, `drive_share_file`, `tasks_update_task`,
  `disconnect_google_workspace`, `commit_workspace_action`, the four
  `*_batch_update_*` tools, `chat_create_message`, `chat_post_message_simple`,
  `chat_reply_to_message`, `keep_create_note`, `keep_append_note_content`,
  `keep_patch_note_checklist`, `keep_share_note`, `keep_unshare_note` and
  `meet_update_space`. `idempotentHint` becomes true for the delete and
  unshare tools (`gmail_delete`, `gmail_delete_draft`,
  `calendar_delete_event`, `drive_delete_file`, `drive_unshare_file`,
  `people_delete_contact`, `tasks_delete_task`, `chat_delete_message`,
  `keep_delete_note`, `keep_unshare_note`, `meet_end_active_conference`),
  `disconnect_google_workspace` and `commit_workspace_action`, and false for
  `keep_patch_note_checklist` (it creates a new note).
  `prepare_workspace_action` is read-only. The Keep tools that only report
  that Keep cannot do something (`keep_archive_note`, `keep_unarchive_note`,
  `keep_update_note`, the label and checklist-item tools) are read-only and
  not destructive.

#### Removed parameters and schema changes

- **Removed parameters.** The server now always sends what the removed
  defaults sent.
  - `supports_all_drives` from `drive_list_files`, `drive_get_file`,
    `drive_create_folder`, `drive_create_file_metadata`, `drive_upload_file`,
    `drive_update_file_metadata`, `drive_update_file_content`,
    `drive_move_file`, `drive_copy_file`, `drive_delete_file`,
    `drive_download_file`, `drive_get_file_content_capabilities`,
    `drive_list_permissions`, `drive_get_permission`,
    `drive_create_permission`, `drive_update_permission`,
    `drive_delete_permission` (shared drives are always in scope).
  - `include_items_from_all_drives` from `drive_list_files` (always true).
  - `use_domain_admin_access` from `drive_list_permissions`,
    `drive_get_permission`, `drive_create_permission`,
    `drive_update_permission`, `drive_delete_permission`, `drive_list_drives`,
    `drive_get_drive`, `drive_hide_drive`, `drive_unhide_drive` (calls act as
    the user, never as a domain administrator).
  - `fields` from `drive_list_files`, `drive_get_file`, `drive_create_folder`,
    `drive_create_file_metadata`, `drive_upload_file`,
    `drive_update_file_metadata`, `drive_update_file_content`,
    `drive_move_file`, `drive_copy_file`, `drive_list_permissions`,
    `drive_get_permission`, `drive_create_permission`,
    `drive_update_permission`, `drive_list_drives`, `drive_get_drive`. Each
    call requests the fields its response needs; `drive_list_files` and
    `drive_get_file` keep the masks that were their defaults, so a normalized
    file never reports a value that was not fetched.
  - `person_fields` from `people_list_contacts`, `people_get_contact`,
    `people_create_contact`, `people_update_contact`; `read_mask` from
    `people_search_contacts`; `group_fields` from `people_list_contact_groups`
    (fixed masks: names, email addresses, phone numbers, organizations,
    biographies; for groups name, type, member count, metadata).
  - `metadata_headers` from `gmail_get_thread` and `gmail_get_draft`
    (`gmail_get_draft` never forwarded it; `gmail_get_thread` with
    `format=metadata` now returns every header).
- **Values that passed through to Google and are now rejected by the schema.**
  - `send_updates` on `calendar_create_event`, `calendar_update_event`,
    `calendar_delete_event` and `calendar_set_event_attachments`: only `all`,
    `externalOnly`, `none`.
  - `calendar_search_events` `order_by`: only `startTime`, `updated`.
  - `drive_share_file` `role` (the former `drive_update_permission` role):
    only `owner`, `organizer`, `fileOrganizer`, `writer`, `commenter`,
    `reader`.
- **No tool returns a Google resource unchanged any more**, except
  `docs_read_document` with `format: raw`. The 37 tools
  that did (every Docs, Sheets and Slides tool; `forms_get_form`,
  `forms_create_form`, `forms_batch_update_form`,
  `forms_set_form_publish_settings`; every People tool except
  `people_delete_contact`; `calendar_list_calendars`,
  `calendar_check_time_availability`; `tasks_create_task`,
  `tasks_update_task`, `tasks_complete_task`, `tasks_move_task`,
  `tasks_create_tasklist`) return a normalized response and publish an open
  output schema for it; see Response shapes below.
- **Optional parameters are published as their plain type.** `T | None`
  parameters no longer appear as `anyOf: [T, {type: null}]` with
  `default: null`; they are simply not in `required`. A client that validates
  arguments against the schema before sending should omit an unset parameter
  rather than send `null` (the server itself still accepts `null`).

#### Response shapes

Every tool now returns a response the server builds: snake_case keys at
every depth, dates in the account's time zone, people as `{name, email}`, and
a `resource` handle per Workspace item. Google's own objects appear only in
the `replies` of the four `*_batch_update_*` tools and in
`docs_read_document` with `format=raw`. Each tool publishes an open output
schema (`additionalProperties: true`) for its shape. Breaking changes, by
area:

- **One list envelope.** Every list-shaped result (the `list_*` and
  `search_*` tools, `gmail_get_mail_digest`, `gmail_check_mail_updates`,
  `gmail_get_thread`, `calendar_get_calendar_digest`, `tasks_tasks_digest`)
  carries its resource-named list (`messages`, `files`, `events`, `tasks`,
  `contacts`, `calendars`, ...) with `count`, `has_more`,
  `next_page_token` and `fetched_at`, plus `account_timezone` when the
  items carry dates. Renamed: `message_count` → `count`
  (`gmail_get_mail_digest`, `gmail_get_thread`), `new_count` → `count`
  (`gmail_check_mail_updates`). Removed: `next_history_id` from
  `gmail_get_mail_digest` (always null there; `gmail_check_mail_updates`
  keeps its cursor fields unchanged).
- **`status` only on changes.** Removed from `search_workspace` (`status:
  "partial"` is now a non-empty `errors`), `resolve_workspace_resource`
  (`status: "ok"`/`"resolved"` is now `verified: true`/`false`; its
  `next_actions` is gone) and `get_workspace_capabilities`. Calendar changes
  report `status: "ok"` instead of `success: true`
  (`calendar_create_event`, `calendar_update_event`,
  `calendar_respond_to_event`, `calendar_delete_event`), and
  `calendar_add_event_attachment` / `calendar_remove_event_attachment`
  gain `status: "ok"`.
- **Resource handles** (`resource`) omit fields they do not know instead of
  sending `null`, and a handle attached to an item carries only `kind`, `id`
  and `uri` plus what the item does not already say (no repeated `name`,
  `mime_type`, `modified_at`, `web_url`). Gmail handles no longer put the
  thread ID in `name`. Handles now also attach to `contacts`, `file`,
  `event`, `contact`, `task` and to the digest groups (`overdue`,
  `upcoming`, `unscheduled`, `requires_response`); a task list no longer
  gets a handle of kind `task`.
- **Web links** (`web_url`, `url`) drop Google's tracking parameters (`usp`,
  `ouid`, `rtpof`, `sd`, `utm_*`, ...); parameters a link needs, such as a
  Calendar link's `eid`, stay.
- **Gmail.** Message envelopes drop `date_timezone` (the response's
  `account_timezone` says it once) and `source_date`; `gmail_read_emails`
  drops `history_id` and `internal_date`, and with `format=full` returns the
  raw values under `details` (`date_header`, `internal_date`,
  `history_id`). `gmail_get_thread` drops `history_id` (the raw formats
  keep Gmail's own `thread.historyId`).
- **Drive.** File envelopes drop `timezone`, `source_created_at` and
  `source_modified_at`. The `file` returned by `drive_create_folder`,
  `drive_create_file_metadata`, `drive_upload_file`,
  `drive_update_file_metadata`, `drive_update_file_content`,
  `drive_move_file`, `drive_copy_file` and `drive_delete_file` is
  normalized: `id, name, kind, mime_type, size, parent_ids, drive_id,
  web_url, trashed` instead of Google's `mimeType`, `parents`, `driveId`,
  `webViewLink`.
- **Chat, Keep and Meet** (optional integrations): messages, notes,
  conference records, participants, recordings and transcripts drop the
  per-item `timezone`; list results already carry `account_timezone`, and
  every timestamp keeps its offset.
- **Calendar.** Event envelopes drop `timezone` and `source_start` /
  `source_end`; `event_timezone` names the zone the event was scheduled in
  when it differs from the account's, and `updated_at` is in the account's
  time zone. `calendar_read_events` attendees are `{name, email,
  response_status, optional, organizer, is_self, is_resource}` and
  attachments `{title, file_url, file_id, mime_type}`; it also returns
  `url` and `recurring_event_id`. `calendar_create_event`,
  `calendar_update_event`, `calendar_respond_to_event` and the two
  attachment tools return `event` in that same shape instead of Google's
  Event resource (dropped: `kind`, `etag`, `iCalUID`, `sequence`,
  `colorId`, `creator`, `reminders`, `conferenceData` beyond the meeting
  link, `eventType`, `created`). `calendar_create_event` and
  `calendar_update_event` always return `warnings` (empty when the conflict
  check ran). `calendar_list_calendars` returns `calendars` (`id`, `name`
  — the user's own name for the calendar when set — `description`,
  `primary`, `access_role`, `time_zone`, `hidden`), `count` and
  `next_page_token` instead of Google's CalendarList (dropped: `kind`,
  `etag`, `nextSyncToken`, `colorId`, `backgroundColor`,
  `foregroundColor`, `selected`, `defaultReminders`,
  `notificationSettings`, `conferenceProperties`; `summary`/
  `summaryOverride` → `name`, `accessRole` → `access_role`, `timeZone` →
  `time_zone`). `calendar_check_time_availability` returns `all_free`,
  `time_min`, `time_max`, `account_timezone` and `calendars` as a list in
  request order of `{id, free, busy, errors}` with busy periods in the
  requested time zone, instead of Google's map keyed by calendar ID
  (`errors` holds Google's reasons, e.g. `notFound`; `free` is null when a
  calendar could not be read). `calendar_search_events` gains
  `account_timezone`.
- **`search_workspace`.** Gmail hits carry `thread_id`, `subject`,
  `from`, `date`, `snippet` and `unread` (one metadata request per hit,
  concurrent and bounded); their `name` (the thread ID) is gone. Drive
  searches names and content (`fullText contains` or `name contains`), name
  matches first; Drive hits report `kind` as the file kind (`document`,
  `folder`, ...) with the MIME type in `mime_type`, `url` → `web_url`, and
  `modified_at` in the account's time zone. Matches are grouped by service
  in the order of `services`. The result gains `account_timezone`.
- **Docs.** `docs_get_document` returns an editing outline instead of the
  Document resource: `document_id`, `title`, `revision_id`, `url`,
  `suggestions_view_mode` and `tabs` (always a list; one entry with null
  `tab_id`/`title` without `include_tabs_content`), each tab with
  `tab_id`, `title`, `index`, `nesting_level`, `parent_tab_id`, `headers`,
  `footers`, `footnotes`, `named_ranges` and `content`: structural elements
  with `kind` (`paragraph`, `table`, `table_of_contents`,
  `section_break`), `start_index`, `end_index`, the plain `text`, the named
  `style` (null for normal text), `list` (`list_id`, `nesting_level`,
  `ordered`), `inline` objects and links with their indices, and table
  `cells`. Dropped: `namedStyles`, `documentStyle`, every `textStyle`, the
  rest of `paragraphStyle`, list glyph definitions, `inlineObjects` /
  `positionedObjects` details, `suggested*` fields, table styles. Read the
  Document resource unchanged with `docs_read_document` `format=raw`.
  `docs_create_document` returns `status`, `document_id`, `title`,
  `revision_id`, `url`; `docs_append_document_text` and
  `docs_replace_document_text` return `status`, `document_id`,
  `revision_id` (+ `occurrences_changed`, 0 when nothing matched);
  `docs_batch_update_document` returns `status`, `document_id`,
  `revision_id`, `replies` (Google's, unchanged) and `count`.
- **Sheets.** `sheets_get_spreadsheet` returns `spreadsheet_id`, `title`,
  `locale`, `spreadsheet_time_zone`, `url`, `sheets` (`sheet_id`, `title`,
  `index`, `type`, `row_count`, `column_count`, `frozen_row_count`,
  `frozen_column_count`, `hidden`, and with `include_grid_data` the
  displayed `values` and their top-left cell `values_start` instead of raw
  `rowData`) and `named_ranges` (`named_range_id`, `name`, A1 `range`).
  Dropped: `defaultFormat`, `spreadsheetTheme`, `autoRecalc`,
  `iterativeCalculationSettings`, `dataSources`, `dataSourceSchedules`,
  `developerMetadata`, and per sheet `conditionalFormats`,
  `protectedRanges`, `bandedRanges`, `charts`, `filterViews`,
  `basicFilter`, `merges`, tab colors and cell formatting.
  `sheets_create_spreadsheet` returns `status`, `spreadsheet_id`, `title`,
  `url`, `sheets`. `sheets_get_sheet_values` returns `spreadsheet_id`,
  `range`, `major_dimension`, `values` (`[]` for an empty range) and
  `row_count`; `sheets_batch_get_sheet_values` returns `spreadsheet_id`,
  `value_ranges` (same fields) and `count`. `sheets_append_sheet_values`
  and `sheets_update_sheet_values` return `status`, `spreadsheet_id`,
  (`table_range`,) `updated_range`, `updated_rows`, `updated_columns`,
  `updated_cells` and `updated_values` (null unless
  `include_values_in_response`). `sheets_batch_update_spreadsheet` returns
  `status`, `spreadsheet_id`, `replies`, `count` and `spreadsheet` (the
  `sheets_get_spreadsheet` shape when requested, else null).
- **Slides.** `slides_get_presentation` returns an outline:
  `presentation_id`, `title`, `revision_id`, `url`, `page_size`
  (`width_pt`, `height_pt`), `count` and `slides`, each with `object_id`,
  `index`, `layout` (its name), `title`, `text` (all text in reading
  order), `notes` (speaker notes) and `elements` (`object_id`, `type`,
  `placeholder`, `text`). Dropped: `masters`, `layouts`, `notesMaster`,
  `locale`, transforms, sizes, styles, fills, content URLs.
  `slides_get_slide_page` returns `object_id`, `page_type`, `title`,
  `text`, `notes` and `elements` with each element's `size` and `position`
  in points. `slides_get_slide_thumbnail`
  returns `content_url`, `width`, `height`, `mime_type`;
  `slides_create_presentation` `status`, `presentation_id`, `title`, `url`,
  `slides`; `slides_replace_text_in_presentation` `status`,
  `presentation_id`, `occurrences_changed`;
  `slides_batch_update_presentation` `status`, `presentation_id`,
  `revision_id`, `replies`, `count`.
- **Forms.** `forms_get_form` returns `form_id`, `title`,
  `document_title`, `description`, `responder_url`, `revision_id`,
  `linked_spreadsheet_id`, `is_quiz`, `published`, `accepting_responses`
  (null for legacy forms), `count` and `items` (`item_id`, `title`,
  `description`, `kind`, `question_id`, `required`, `question_type` such as
  `short_text`, `multiple_choice`, `scale`, and `options`). Dropped:
  `settings` other than the quiz flag, `emailCollectionType`, grading,
  choice navigation and images, media, `shuffle`, file-upload limits.
  `forms_create_form` returns `status`, `form_id`, `title`,
  `document_title`, `responder_url`, `revision_id`;
  `forms_batch_update_form` `status`, `form_id`, `revision_id`,
  `replies`, `count`, `form` (the `forms_get_form` shape when requested);
  `forms_set_form_publish_settings` `status`, `form_id`, `published`,
  `accepting_responses`. Form responses drop the per-item `timezone`.
- **People.** Every contact is `{resource_name, etag, name, given_name,
  family_name, emails, phones, organizations, biography}` with `emails` and
  `phones` as `[{value, type}]` (duplicates from merged sources listed once)
  and `organizations` as `[{name, title}]`; dropped: `metadata`, sources,
  primary flags, `formattedType`, `canonicalForm`,
  `displayNameLastFirst`, `unstructuredName`. `people_list_contacts`
  returns `contacts`, `count`, `total` (was `totalItems`) and
  `next_page_token` (dropped `totalPeople`, `nextSyncToken`);
  `people_search_contacts` returns `contacts` and `count` (each
  `results[].person` unwrapped); `people_get_contact` returns `contact`;
  `people_create_contact` / `people_update_contact` return `status` and
  `contact`. Contact groups are `{resource_name, name, group_type ('user'
  or 'system'), member_count}`: `people_list_contact_groups` returns
  `contact_groups`, `count`, `total`, `next_page_token`;
  `people_create_contact_group` `status` and `contact_group`;
  `people_modify_contact_group_members` `status`, `group_name`,
  `not_found`, `cannot_remove_last_group` (both `[]` when every change
  applied).
- **Tasks.** Tasks drop `source_due` and `due_is_date_only` (Google Tasks
  stores only a date: `due_date`), report `completed_at` and `updated_at`
  in the account's time zone, and gain `url`. `tasks_create_task`,
  `tasks_update_task`, `tasks_complete_task` and `tasks_move_task` return
  `status` and `task` (the `tasks_get_task` shape) instead of Google's Task
  resource; `tasks_create_tasklist` returns `status` and `tasklist`. Task
  lists report `updated_at` in the account's time zone and
  `tasks_list_tasklists` gains `account_timezone`. `tasks_tasks_digest`
  gains `count`. `tasks_list_tasks` and `tasks_tasks_digest` report the
  list's real ID as `tasklist_id`.

### Added

- `docs_read_document(document_id, format=text|markdown|outline|raw,
  offset)`: a document as clean text or Markdown (headings, nested lists,
  tables, links, person and rich-link chips, footnotes, every tab), paged like
  an email body (`body`, `truncated`, `next_offset`, `body_chars`,
  `body_sha256`); `outline` is the editing outline with indices for
  `docs_batch_update_document` (an index inside an element's text counts
  UTF-16 units, as Google's do), and `raw` the Document resource unchanged.
- `drive_read_file(file_id, format=text|markdown, offset)`: the text of a
  Drive file with the same paging fields, without the server's filesystem:
  Google Docs (also as Markdown) and Slides exported as text, Sheets as CSV
  of the first sheet, stored text files up to 5 MB downloaded; binary files,
  folders, forms and drawings fail with `invalid_input`.
- `tasklist_id` is optional on every task tool; omitted, the tool uses the
  account's default list, and `tasks_list_tasks` and `tasks_tasks_digest`
  report that list's real ID.
- `MCP_TOOL_TIER=core|extended|complete` (see Tool tiers).
- `MCP_CONSENT=required|preview_only|off`, `dry_run` on the bulk and raw
  batch-update tools, and prepare/commit for every consequential call (see
  Consent for consequential calls); `docs/CONSENT.md` describes the model.
- `get_google_connection_status` reports the deployment under `server`
  (tool tier, enabled namespaces, OAuth capabilities, file inputs, tool
  search) without calling Google.
- `tests/test_catalog_budget.py` pins a ceiling for the default and
  all-optional catalogs; `scripts/measure_tokens.py` prints their size.
- `tests/fixtures/google_payloads/` holds a recorded-shape Google payload
  per normalizer and its golden output (`tests/test_response_goldens.py`);
  `scripts/measure_tokens.py` reports each response's size against its
  payload, and per-item token ceilings are tested.

### Changed

- **Descriptions.** Every default-catalog tool description says what the tool
  does, when to use it instead of its siblings, what it returns and its side
  effects. Every parameter has a written description; the generated
  fallback ("Whether to enable ...", "Human-readable description for the
  target resource.", "Email address for the from.") is removed and
  `tests/test_description_lint.py` fails on a missing, name-restating or
  templated description and on an enum-shaped parameter without `enum`.
  Descriptions no longer promise an `{"error": ...}` result; failures are
  `isError` results with the shared error envelope.
- **Schemas no longer publish the generic limits.** `maxLength: 1000000`,
  `maxItems: 10000` and `maxProperties: 10000` are still enforced on every
  call but are not repeated in each input schema. Tool-specific limits (the
  upload picker's payload) stay published.
- **Output schemas** lose the generated "Response field: x" descriptions,
  their filler title and description, and the top-level `resource` block that
  most results never carry. Published schemas stay open.
- **Discovery front page.** With tool search on, the always-visible tools are
  the connection and consent tools, `search_workspace`,
  `resolve_workspace_resource`, `gmail_search_emails`,
  `gmail_get_mail_digest`, `calendar_search_events`,
  `calendar_get_calendar_digest`, `drive_list_files` and
  `tasks_tasks_digest`. The upload picker (`uploads_file_manager`,
  `uploads_list_files`) and `get_mcp_apps_diagnostics` (extended tier) are
  reachable through `search_tools`.
- **Enums for closed sets the tools already enforced**, so the schema states
  them: `search_workspace` `services`, `connect_google_workspace`
  `capabilities`, `get_google_connection_status` `capability`,
  `keep_patch_note_checklist` `operation`. The accepted values are unchanged;
  an invalid value is now rejected by input validation instead of by the
  tool.
- **Apps dashboard** follows the 2.0.0 tools: it reads calendars from
  `calendar_get_calendar_context`, creates events with `add_meet_link`, sends
  `gmail_modify_messages` and `gmail_delete` for its email actions, and no
  longer passes `force` when it cancels an event. Every argument the view
  sends is listed per operation in `apps/ui/src/operations.ts`
  (`OPERATION_ARGUMENTS`), and `tests/test_apps_ui_tool_contract.py` checks
  each operation's tool and argument names against the published catalog.
  An action that needs consent the host cannot ask for
  (`confirmation_required`) shows the server's preview text and tells the
  user nothing changed and to ask the assistant, which runs the
  prepare/commit step; the view never prepares or commits itself.

### Fixed

- `tasks_tasks_digest` was published with `readOnlyHint: false` and
  `idempotentHint: false` (its local name starts with its namespace, which the
  annotation rules stripped); it is now read-only and idempotent.
- Hiding or unhiding a shared drive (`drive_hide_drive` and
  `drive_unhide_drive`, now `drive_update_drive`) failed on every call: it
  passed `useDomainAdminAccess`, which `drives.hide`/`drives.unhide` do not
  accept, and the Google client raised before sending the request.
- Background-task runs of `tasks_tasks_digest` were authorized, admitted and
  recorded under `tasks_digest`: the execution guard skipped the namespace
  prefix for a local name that already starts with it.
- `drive_upload_file` without `parent_ids` checked for a same-named file
  across everything the user can access, including shared drives, so
  `if_exists="overwrite"` could replace an unrelated file's content (and
  `skip`/`rename` reacted to it). The check now looks only in the folder the
  upload lands in: the first parent, or the root of My Drive.
- `people_update_contact` with only `given_name` (or only `family_name`)
  cleared the other name parts, middle name included: Google replaces the
  whole name. The tool now reads the stored name and writes the passed parts
  over it.
- `chat_list_messages` with `thread_name` failed on every call: it passed a
  `thread.name` keyword the Chat client does not accept. The thread is now a
  `filter` clause, combined with any `filter` given by AND.
- `calendar_search_events` returned `next_page_token` but had no way to
  pass it back; it gains `page_token`, and `max_results` is per page.
- `calendar_find_free_time` applied working hours at the first day's UTC
  offset on every later day of the window, so across a DST change the
  suggested slots moved by an hour. Each day's hours are now local time in
  `time_zone`.
- `gmail_save_draft` with `thread_id` set only the thread ID, without the
  `In-Reply-To` and `References` headers Gmail needs to keep the draft in
  the thread. They now point at the thread's latest message.
- The dashboard's event editor saved only through the form's `submit` event,
  which a sandbox without `allow-forms` blocks; it now saves from the submit
  button (and Enter in a field) in either sandbox.
- Listing uploads stored in S3 failed validation on the store's own `status`
  field.
- `resolve_workspace_resource` is annotated read-only, and
  `calendar_get_calendar_context` open-world (it always read the account time
  zone from Google).

## 1.1.0 — 2026-11-04

Cleaner Gmail text. No tool or parameter is removed or renamed; one optional
parameter and two result fields are added. One default changes:
`gmail_read_emails` with `format=full` returns `html_body` as `null` unless
the new `include_html` is true.

### Changed

- **Gmail bodies read as the message does.** The HTML-to-text step skips
  `<head>`, `<style>`, `<script>`, `<title>`, `<template>` and `<noscript>`
  content and inline-hidden elements (pre-headers, hidden layout variants),
  separates blocks and table cells, keeps a link's URL once after its text,
  and drops zero-width and other invisible characters used as padding
  (this includes the zero-width joiner, so an emoji ZWJ sequence such as a
  family emoji shows as its separate emoji). On
  the synthetic newsletter corpus in `tests/fixtures/gmail_corpus` the first
  `clean` page shrinks by 85 to 94% and no longer starts with CSS.
- **Pages end between words.** `clean` and `preview` pages end at the last
  paragraph, line, sentence or word break in the final 40% of the window
  instead of at a fixed character. `next_offset` is still a character
  offset into the cleaned text, and consecutive pages join back into it.
- **`preview` is a 1,000-character window of the cleaned text**, and
  `gmail_get_thread`, `gmail_get_draft` (`format=full`) and the digest use
  the same cleaner.
- **Signatures and quoted replies.** Several trailing "Sent from my ..." /
  "Get Outlook for ..." lines are all removed, not only the last one, and a
  body that is only a quoted reply shows the `[quoted: ...]` marker alone.
- **Snippets are decoded and de-padded** (`&#39;` and pre-header padding no
  longer appear). Every Gmail tool takes the snippet from Gmail's own
  `snippet`, so search, digest, thread and read results agree on it.

### Added

- `gmail_read_emails` `include_html` (default `false`): with `format=full`,
  returns the original HTML part as `html_body`. `html_body` is now `null`
  unless it is set; `format=full` returns the whole cleaned text as `body`
  and the original text part as `text_body`.
- `body_chars` and `body_sha256` on message results that carry a body,
  describing the whole cleaned body, so a client paging with `next_offset`
  can tell whether the message changed between pages.
- `gmail_get_draft` with `format=full` adds the cleaned, unpaged `body` (with
  `body_chars`, `body_sha256` and `quoted_messages_collapsed`) next to the
  raw `draft`, and its `snippet` is decoded like the other Gmail tools'.

## 1.0.1 — 2026-11-04

Correctness fixes to the published output schemas and the error envelope. No
tool or parameter is removed or renamed. One result changes shape: a calendar
time-slot conflict is now an error result instead of a successful result with
`status: "CONFLICT"` (see below); clients that match on that status need to
check for the `conflict` error code instead.

### Fixed

- **Output schemas no longer reject real Google responses.** Every published
  `outputSchema` is now open (`additionalProperties: true`), and a field is
  typed only when the code or an explicit list says so; a plural name is no
  longer taken as proof of an array. Clients that validate
  `structuredContent` (as the MCP specification asks) previously rejected
  `docs_get_document` (`namedStyles` is an object) and `people_list_contacts`
  (`totalItems` was not listed), and would have rejected
  `sheets_get_spreadsheet` (`namedRanges` is a list there, an object in Docs).
  The schemas of the Google passthrough tools (Docs, Sheets, Slides, Forms,
  People, calendar list and availability, Tasks writes) and of all inferred
  schemas widen; nothing narrows.
- **Google HTTP failures from Gmail, Drive, Calendar, Chat and Gemini tools
  are classified** (issue #51). A raw provider error used to reach the client
  as JSON-RPC `-32603` / `internal_error` with no status and no next step.
  It is now an `isError` result with the same codes the other namespaces
  already used: 400 `invalid_input`, 401 `reauth_required`, 403
  `permission_denied`, 404/410 `not_found`, 409/412 `conflict`, 429
  `rate_limited`, 5xx `provider_unavailable`, with `provider_status` set.
- **A 403 for a missing OAuth scope says so.** Both classification paths
  read Google's machine-readable reason (`errors[].reason` or
  `details[].reason`): `insufficientPermissions` /
  `ACCESS_TOKEN_SCOPE_INSUFFICIENT` becomes `missing_capability` with
  `required_action: {tool: connect_google_workspace, arguments:
  {capabilities: [<service>]}}` (the service is read from the request URL),
  quota reasons become `rate_limited`, `accessNotConfigured` keeps
  `permission_denied` with an `enable_api_in_google_cloud_project` next step.
  The reason is echoed as `details.provider_reason`.
- **Protocol errors are no longer classified twice.** An error the execution
  guard had already turned into a JSON-RPC error (server draining, revoked
  principal, unexpected fault) was re-classified by the middleware after
  FastMCP wrapped it, which reduced every one of them to `internal_error`.
  The original code and envelope are now forwarded unchanged.
- **Calendar time-slot conflicts are errors, not successes.**
  `calendar_create_event` and `calendar_update_event` used to return
  `{"error": ..., "status": "CONFLICT"}` as a successful result. They now
  return an `isError` result with code `conflict`, the overlapping events
  under `details.conflicting_events`, and, with
  `on_conflict="suggest_next_slot"`, free alternatives under
  `details.suggested_slots`. When the conflict check itself fails the event
  is still written and the response carries a `warnings` list saying the
  slot was not verified, instead of silently proceeding.
- **`serverInfo.version` is the package version.** It reported FastMCP's
  version (`4.1.6`).
- **Drive file metadata no longer invents values.** `shared`, `starred`,
  `trashed`, `owners` and `parent_ids` are `null` when Google did not return
  the field (a narrower `fields` mask, or a shared-drive file without
  owners); they were `false` or `[]`.

### Changed

- `gmail_search_emails` fetches the details of its hits concurrently (the
  same bounded helper `gmail_get_mail_digest` uses) instead of one request
  after another; results keep Gmail's order.

## 1.0.0 — 2026-11-03

The first stable release. This version migrates the server from FastMCP
3.4.4 / MCP SDK 1.28.1 (protocol `2025-11-25`) to **FastMCP 4.1.6 / MCP SDK
2.3.0** (migrated on 4.0.10 / 2.2.0; see Security fixes), adds full support for **MCP 2026-07-28** while keeping legacy
protocol connectivity (`2024-11-05` through `2025-11-25`), and upgrades the
MCP Apps dashboard/picker to **Apps SDK 2.0.3**. See
`docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` for the full plan and
`docs/migration/W0_BASELINE.md` for the pre-migration baseline
(`fba6253`, application `0.3.13`).

### Breaking changes

- **MCPB no longer asks for a token encryption key.** The
  `token_encryption_key` setting is removed from the bundle manifest. The
  local stdio runtime now generates its key on first use and keeps it in the
  OS keychain (see Security). Google connections saved with a previously
  pasted key cannot be decrypted with the new one: reconnect Google once
  after upgrading. Headless setups without a secure keychain set
  `MCP_TOKEN_ENCRYPTION_KEY` in the environment instead.
- **Removed tools.** `chat_summarize_space_messages` and `keep_summarize_note`
  are deleted, not stubbed. Both depended on FastMCP's `ctx.sample()`
  (Sampling), which the 2026-07-28 spec deprecates and this project does not
  replace with a server-side summarization provider. The Chat/Keep read
  tools and the user-invoked summary *prompts*
  (`chat_summarize_chat_thread_prompt`, `keep_summarize_keep_note_prompt`)
  are unchanged. (§9.1, §9.2, `docs/migration/W2_CATALOG_DIFF.md` §2.)
- **Removed `session_id` everywhere.** The MCP Apps dashboard no longer
  accepts a caller-chosen `session_id`; every dashboard tool
  (`apps_get_dashboard`, `apps_get_weekly_calendar_view`, `apps_get_state`,
  `apps_set_state`, `apps_patch_state`, `apps_next_range`, `apps_prev_range`,
  `apps_today`, `apps_get_event_detail`, `apps_get_email_detail`,
  `apps_get_email_attachment`) instead uses a server-issued `view_handle`
  (`wsv_` + 256 random bits), required on the six state-mutating tools and
  optional (validated when supplied) elsewhere. Old dashboard state,
  transport-session fallback, and the browser `localStorage`/`Math.random()`
  view id are not migrated — there is no application-level backward
  compatibility (owner decision, §9.1). (§9.3, `docs/migration/W3_CATALOG_DIFF.md` §1.)
- **Removed URI aliases.** The flat `ui/resourceUri` tool metadata key, the
  legacy `ui://dashboard-ui` root registration, the double-mounted
  `ui://apps/apps/dashboard-ui` resource, and the `apps://apps/dashboard/ui`
  text-only copy are all removed. Only the canonical nested
  `_meta.ui.resourceUri` (`ui://dashboard-ui` on a direct subserver client,
  `ui://apps/dashboard-ui` composed at the root) remains. (§9.1, §9.7,
  `docs/migration/W6_CATALOG_DIFF.md` §1.)
- **Removed parameter.** `gmail_delete_thread.force` is deleted (it was
  accepted and silently ignored — thread deletion was always confirmed
  interactively). The input schema is closed
  (`additionalProperties: false`), so a client still sending `force` now
  gets an input-validation error instead of having it dropped.
  (`docs/migration/W4B_CATALOG_DIFF.md` §4.)
- **Closed schemas.** The picker's upload callback
  (`files_store_files`) now publishes a closed, bounded input schema for the
  exact Prefab `DropZone` payload (name ≤1024 chars, size 0..25 MiB, type
  ≤255 chars, base64 `data` bounded to the file-size limit,
  `additionalProperties: false`) instead of an open `list[dict]`. The six
  dashboard state tools publish a closed `{state, view}` output object
  (previously FastMCP's default `additionalProperties: true`). Several
  Google Meet space tools (`meet_create_space`, `meet_get_space`,
  `meet_update_space`, `meet_end_active_conference`) go the other way: a
  latent output-schema-inference bug had published a *closed* schema
  containing only error fields, which every successful response violated;
  they now publish FastMCP's open object schema until a typed Meet response
  model lands. (`docs/migration/W2_CATALOG_DIFF.md` §1,
  `docs/migration/W3_CATALOG_DIFF.md` §1, `docs/migration/W5_CATALOG_DIFF.md` §2.)
- **Visibility changes.** `files_list_files`, `files_list_files_page`, and
  `files_read_file` are now model-only (previously model + app);
  `apps_get_dashboard`/`apps_get_weekly_calendar_view` are explicitly
  `["model", "app"]`; the six dashboard state tools and
  `apps_get_event_detail`/`apps_get_email_detail` are app-only. Visibility is
  a host hint for `tools/list` filtering, not authorization — every call is
  still authorized server-side by principal-bound view handle, Google grant,
  and confirmation gates. (`docs/migration/W6_CATALOG_DIFF.md` §2.)
- **Error-shape changes.**
  - A Google API failure, argument-validation failure, or business-rule
    rejection is now consistently an `isError: true` **tool result** carrying
    the shared error envelope (`code`, `message`, `retryable`, `retry_after`,
    `required_action`, `provider_status`, `field_errors`, optional
    `details`) — never a JSON-RPC protocol error. 61 tool bodies that used to
    *return* `{"error": ..., "provider_status": ..., "context": ...}` as a
    **successful** result now raise a structured error instead, so their
    output schemas no longer publish `error`/`provider_status`/`context`;
    the error context moved to `details.context`.
    (`docs/migration/W5_CATALOG_DIFF.md` §1.)
  - `McpError(ErrorData(...))`'s positional constructor (a `TypeError` under
    SDK 2) is replaced with the supported keyword form.
  - The custom `-32029` rate-limit code, which collides with the
    2026-07-28 spec's newly reserved error-code range, is retired. Named
    JSON-RPC codes now live in `-32000..-32019`
    (`common/errors.py`): `-32005` rate limited, `-32006`
    draining/unavailable, `-32007` unauthorized, `-32008` authorization
    state unavailable. (§3.5 of the plan, §9.6.)
  - `commit_workspace_action` returns `resultType: input_required` (not a
    `status: committed` wrapper) when the bound action still needs
    confirmation, and passes through a nested `isError` result unwrapped
    instead of relabeling it `committed`. (`docs/migration/W4_CATALOG_DIFF.md` §2.)
  - `refresh_workspace_catalog`'s `notification_sent` field changed type
    from a string (`"tools/list_changed"`) to a boolean (now always
    `false`, since the server sends no such notification and grants are
    re-evaluated on every request instead). (`docs/migration/W5_CATALOG_DIFF.md` §3.)
- **Confirmation flow changes.** Destructive/consequential tools on MCP
  2026-07-28 now return `resultType: input_required` **before any mutation**
  and resume on a retry carrying the answer and a sealed continuation
  (`requestState`), instead of the legacy imperative `ctx.elicit()` flow.
  Legacy (pre-2026-07-28) clients keep the `ctx.elicit()` branch through the
  same policy adapter. A modern host that declares no elicitation capability
  gets a fail-closed `confirmation_required` result — never an automatic
  mutation. `commit_workspace_action`'s prepared-token semantics changed: a
  repeated commit for a `succeeded` operation now replays the saved result
  instead of failing, and its token lifetime changed from a 300s
  token / 600s question split to one `MCP_CONFIRMATION_TTL_SECONDS` (default
  600s) covering both. The `ApprovalStore.consume()`-before-execution design
  is replaced by durable operation records
  (`prepared -> awaiting_input -> executing -> succeeded | failed |
  outcome_unknown`); old approval/replay tokens are not migrated. Bypass
  flags (`confirm_send`, `notify`, `confirm_create`, `confirm_delete`,
  `force`, `delete_mode`, `permanent`) keep their existing defaults and
  behavior — only stale descriptions were corrected (owner decision, §9.1).
  (§3.3 of the plan, §9.4, §9.5, `docs/migration/W4_CONFIRMATION_POLICY.md`.)
- **Removed deprecated-protocol dependencies.** No application code depends
  on Sampling (`ctx.sample`), Roots (`ctx.list_roots`), client-facing Logging
  (`ctx.info/debug/warning/...` now go to server logs at `DEBUG`; progress
  notifications are unaffected), or dynamic client registration. The Apps
  `ui/initialize` handshake is retained — it is a separate, non-deprecated
  protocol. (Owner decision §9.1, §9.2.)
- **Python attribute reads.** Internal SDK model reads moved from SDK-1-style
  camelCase attributes to SDK 2 snake_case
  (`tool.input_schema`, `resource.mime_type`, `result.is_error`,
  `result.structured_content`, ...); wire JSON and Google API payload keys
  remain camelCase. CI now runs with `FASTMCP_MCP_CAMELCASE_COMPAT=false`, so
  a regression fails loudly instead of silently working through FastMCP's
  compatibility shim. (§3.5 of the plan, §9.2.)

### New features

- **MCP 2026-07-28 support**, including `server/discover`, first-call
  `tools/list`/`tools/call` with no `initialize` handshake, `resultType` on
  every tool/list result, request-scoped SSE for progress and long-running
  calls, and multi-round-trip confirmations (MRTR). Legacy protocol
  connectivity (`2024-11-05`..`2025-11-25`) is kept through FastMCP's
  built-in compatibility path. (`docs/migration/W0_BASELINE.md`,
  `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` §3.1–§3.2.)
- **MCP Tasks extension** (`io.modelcontextprotocol/tasks`, replacing the
  removed core experimental tasks and `tasks/result`/`tasks/list`): task
  tools now negotiate through `tasks/get`/`tasks/update`, support answering a
  parked confirmation via `tasks/update`, and restore the caller's identity
  (bearer token, principal revocation, current Google grant) at execution
  time on every worker, not only at submission. (§3.4 of the plan, §9.6.)
- **Durable, replay-safe mutations.** A repeated commit or confirmed
  continuation for an operation that already `succeeded` returns the saved
  (size-capped) result instead of re-executing; simultaneous claims execute
  exactly one and return `operation_in_progress` for the rest; a call whose
  outcome could not be determined (timeout, disconnect, crash) returns
  `outcome_unknown` with `required_action.verify` steps instead of being
  retried automatically. Calendar event creation and Chat message creation
  use provider-native idempotency keys; Gmail send/reply reconciles an
  unknown outcome via the RFC 822 `Message-ID` it always sets. (§9.5,
  `docs/migration/W4_CONFIRMATION_POLICY.md` §6–§8.)
- **Operation manifest.** Dashboard launch results now carry
  `_meta["mcp-google-workspace/operations"]`, listing exactly the write
  operations available to the current view given registered tools, app
  visibility, and the principal's granted Google capabilities — the
  dashboard no longer guesses which mutating actions are safe to offer.
  (§9.7, `docs/RICH_OUTPUTS.md`.)
- **Cached Prefab picker resource.** The picker's rendered HTML/CSP
  (previously rebuilt from scratch, including a fresh disk read of the ~6.6 MB
  bundled renderer, on every `resources/list`/`resources/read`) is now built
  once per process and reused, keyed by tool identity, the installed
  `prefab-ui` version, the resolved renderer mode/`PREFAB_RENDERER_URL`, and
  the tool's CSP/permissions metadata (`common/prefab_render_cache.py`).
  Measured 8.8x faster listings (41.1ms -> 4.7ms per call, one build plus
  cache hits thereafter) in `tests/test_prefab_render_cache.py`.
- **Golden wire-contract fixtures** (`tests/wire/`) pin the exact JSON-RPC
  shape of modern discovery/listing/call/error/MRTR exchanges and the legacy
  initialize/list/call/confirm sequence, over both raw HTTP and the in-memory
  transport that stands in for stdio.
- **Feature-flag matrix smoke test** exercises startup, listing, and one
  safe call for every optional integration (Apps, Keep, Chat, Meet, Gemini)
  independently and together, through both the root composition and a
  direct client to each namespace's own subserver
  (`tests/test_feature_flag_matrix.py`).

### Security fixes

- **Local token encryption key lives in the OS keychain.** The stdio/MCPB
  runtime stores its Fernet key in Windows Credential Manager, macOS Keychain
  or the Linux Secret Service via `keyring`, instead of a value the user
  generated and pasted into (unencrypted) extension settings. Insecure
  `keyring` backends (fail/null/`keyrings.alt` files) are refused; the key
  is never written to disk. HTTP deployments keep operator-managed key rings.
  The MCPB `gemini_api_key` setting is now marked `sensitive`, so hosts store
  it in the OS keychain too.
- **Picker uploads over ~750 KB were rejected.** The generic 1,000,000-character
  argument limit applied to the picker's base64 file field. String limits now
  honor a larger `maxLength` the called tool declares (only the upload field),
  and rejections name the argument path. The default `MCP_MAX_REQUEST_BYTES`
  rises from 30 to 36 MiB so one 25 MiB upload fits after base64 encoding.
- **Dashboard rendering (W1).** `render.ts` used `textContent`-derived values
  interpolated into HTML attributes/URLs without attribute-safe escaping, so
  a crafted filename/subject/title (e.g. containing `" data-audit-injected="yes`)
  could inject a new DOM attribute. Values are now set through DOM
  APIs/attribute setters rather than string interpolation, URL schemes are
  validated separately, and the custom email-HTML sanitizer is hardened
  against event handlers, active embedded content, external tracking
  requests, and CSS escapes. Adversarial filename/title/URL/encoded-quote/
  malformed-HTML fixtures are covered in the browser test suite. (Plan §4.2,
  status table row "W1".)
- **Origin enforcement was silently disabled.** FastMCP 4.0.10 defaults
  `host_origin_protection` to *off*, so this project's existing
  `allowed_hosts`/`allowed_origins` configuration was not actually being
  enforced. `server_http.HttpServingPolicy` now sets
  `host_origin_protection=True` explicitly. (§9.6.)
- **JWT hardening.** Incoming MCP bearer tokens are verified through a
  `RemoteAuthProvider` wrapping the JWT verifier, which now serves real MCP
  protected-resource discovery metadata and challenge routes; `exp`/`iss`/
  `sub` are mandatory, a future `nbf`/`iat` is refused, and an unknown JWKS
  `kid` triggers at most one refetch per 30 seconds. MCP bearer tokens are
  never reused as Google credentials (tested). (§9.6.)
- **FastMCP 4.1.6.** FastMCP 4.0.10 is affected by GHSA-7xgj-8392-5q4r
  (post-transform component authorization skipped on direct calls) and
  GHSA-vqqp-x5qm-5qp4 (M2M OAuth client wrappers without issuer binding),
  both fixed in 4.1.2. The upgrade also brings MCP SDK 2.3.0 and pydocket
  0.27.1. FastMCP 4.1 now bounds every JWKS fetch attempt itself and moved
  its cache clock to `time.monotonic()`; `WorkspaceJWTVerifier` follows it,
  so a rotated signing key is still accepted on first use while unknown key
  ids still cannot force more than one refetch per interval. Only
  `serverInfo.version` (FastMCP's version) changed on the wire.
- **Dependency security updates.** Carries the 0.3.14 fixes onto the 1.0.0
  line: pyjwt 2.15.1, urllib3 2.8.0, oauthlib 4.0.1, authlib 1.9.3,
  joserfc 1.8.0, pyasn1 0.6.5, dnspython 2.9.1, certifi 2026.10.23 and
  opentelemetry-api 1.45.2 (`pip-audit` clean). The Apps UI moves to
  `@modelcontextprotocol/client`/`core` 2.4.0 (GHSA-6qxp-vccf-f47h,
  GHSA-6prh-2h8m-c8cw), dompurify 3.4.17, zod 4.6.7 and vite 6.4.5
  (`npm audit` clean).

### Operations

- **New environment variables** (see `.env.example` for full descriptions
  and defaults): `MCP_REQUEST_STATE_KEYS` (confirmation-continuation sealing
  key ring), `MCP_CONFIRMATION_TTL_SECONDS`, `MCP_OPERATION_RETENTION_SECONDS`,
  `MCP_OPERATION_LEASE_SECONDS`, `MCP_OPERATION_RESULT_MAX_BYTES`,
  `MCP_APP_VIEW_TTL_SECONDS`, `MCP_UPLOAD_TTL_SECONDS`,
  `MCP_UPLOAD_QUOTA_BYTES`, `FASTMCP_TASKS_ENCRYPTION_KEY`,
  `FASTMCP_DOCKET_URL`/`FASTMCP_DOCKET_NAME`/`FASTMCP_DOCKET_CONCURRENCY`,
  `MCP_WORKERS`, `MCP_REPLICAS`, `MCP_SESSION_AFFINITY`,
  `MCP_HTTP_RESPONSE_MODE`, `MCP_ADMISSION_BACKEND`,
  `MCP_PRINCIPAL_QUEUE_SECONDS`, `MCP_TOOL_DEADLINE_SECONDS`,
  `MCP_EXPENSIVE_DEADLINE_SECONDS`. `fastmcp.settings.docket.url` (FastMCP 3)
  is gone; queue configuration now goes through the `TasksExtension`
  (`FASTMCP_DOCKET_*`) or `MCP_REDIS_URL`. (§3.4 of the plan, §9.6.)
- **Readiness checks.** `/health/ready` declares a fleet when `MCP_WORKERS >
  1`, `MCP_REPLICAS > 1`, or `MCP_REDIS_URL` is set, and then requires shared
  app state, a shared snapshot-encrypted task queue, shared operation
  records, shared continuation keys, and Redis/S3-backed OAuth/upload
  storage. Modern (2026-07-28) traffic needs no session affinity — that
  requirement is removed from readiness; `MCP_SESSION_AFFINITY` is now an
  advisory `legacy_session_affinity` check for handshake-era clients pinned
  behind a load balancer. (§9.6.)
- **Redis is optional.** stdio and single-process HTTP deployments use
  in-memory backends for app state, uploads, operation records, and the task
  queue; nothing requires Redis. A multi-replica/multi-worker fleet requires
  Redis (app state, operation records, task queue, OAuth token storage) and
  S3-compatible storage for uploads at scale — `MCP_REDIS_URL` alone implies
  fleet mode. (Owner decision §9.1, §9.6, README "Run (Streamable HTTP)".)
- **New worker entrypoint.** `uv run mcp-google-workspace-worker` runs
  additional MCP Tasks workers against the same Redis-backed queue as the
  HTTP server, sharing `FASTMCP_TASKS_ENCRYPTION_KEY`. (README "Production
  operations".)

### Upgrade notes

- **Mark the Google OAuth client as an AI agent client.** Google asks that
  every OAuth client used by an AI agent have **Use this client for an
  AI-powered agent** selected (Google Auth Platform → Clients), on a client
  not shared with non-agent apps. This server calls the Workspace REST APIs,
  so an unmarked client keeps working, but the setting becomes permanent on
  existing clients on 2026-11-12; after that, create a new client, replace
  `credentials.json`, and reconnect Google. (README "OAuth setup".)
- **Drain task queues before cutover.** Do not run the old (FastMCP 3 /
  pydocket) and new (FastMCP 4 / `fastmcp-tasks`) task queues side by side.
  Let in-flight FastMCP-3 tasks finish, then deploy 1.0.0; the new deployment
  starts against an empty queue.
- **Old application state is discarded, not migrated.** Dashboard sessions
  keyed by the old `session_id`/browser `localStorage` id, old approval/
  replay tokens, and the removed URI aliases are not readable by 1.0.0. Any
  open dashboard view or in-flight confirmation from before the upgrade must
  be restarted by the user after cutover (owner decision, §9.1; do not build
  a compatibility reader for old application state, plan §8/W8).
- **New required secrets for a fleet deployment** (single-process/stdio
  deployments need none of these): `MCP_REQUEST_STATE_KEYS` (confirmation
  continuations) and `FASTMCP_TASKS_ENCRYPTION_KEY` (task credential
  snapshots) must be set identically on every replica and worker before
  `/health/ready` will pass; generate and rotate them independently of
  `MCP_TOKEN_ENCRYPTION_KEY`/`MCP_SECRET_FILE` (see the rotation procedure in
  `.env.example`).
- Encrypted Google grants and durable uploads/operation evidence from a
  previous release are retained across the upgrade (they are not part of the
  removed application-state surface above); reconnect Google accounts only
  if an actual token/provider migration requires it.
