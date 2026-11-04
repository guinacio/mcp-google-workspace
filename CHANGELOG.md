# Changelog

All notable changes to `mcp-google-workspace` are recorded here. Dates are
UTC. Entries trace to `docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md` (the
migration plan; sections cited below are `9.x` implementation records unless
noted) and its `docs/migration/W*_CATALOG_DIFF.md` contract diffs.

## 2.0.0 — Unreleased

Smaller, truthful tool catalog. Every tool and parameter description is
written for the tool (no generated text), schemas stop repeating limits and
`null` alternatives, raw Google payloads stop publishing an inferred output
schema, and API plumbing parameters are gone. Every response is normalized
into one envelope (snake_case, local times, compact resource handles, one list
shape), and Docs and Drive files can be read as text. The default catalog
shrinks from about 89,000 to about 67,000 estimated tokens (characters / 4;
75,444 to 58,746 with the cl100k tokenizer). Tool names, results and behaviour
are unchanged except where listed under Breaking changes.

### Breaking changes

Input schemas are closed (`additionalProperties: false`), so a caller that
still sends a removed parameter gets the normal input-validation error.

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
    `calendar_delete_event`, `calendar_add_event_attachment`,
    `calendar_remove_event_attachment`: only `all`, `externalOnly`, `none`.
  - `calendar_search_events` `order_by`: only `startTime`, `updated`.
  - `drive_list_files` `corpora`: only `user`, `drive`, `domain`,
    `allDrives`; `spaces`: only `drive` or `appDataFolder` (the
    comma-separated `drive,appDataFolder` and the retired `photos` space are
    rejected).
  - `drive_update_permission` `role`: only `owner`, `organizer`,
    `fileOrganizer`, `writer`, `commenter`, `reader`.
- **No tool returns a Google resource unchanged any more.** The 37 tools
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
  `tasks_tasks_digest`. The upload picker (`files_file_manager`,
  `files_list_files`, `files_list_files_page`) and
  `get_mcp_apps_diagnostics` are reachable through `search_tools`.
- **Enums for closed sets the tools already enforced**, so the schema states
  them: `search_workspace` `services`, `connect_google_workspace`
  `capabilities`, `get_google_connection_status` `capability`,
  `prepare_workspace_action` `tool_name`, `keep_patch_note_checklist`
  `operation`. The accepted values are unchanged; an invalid value is now
  rejected by input validation instead of by the tool.
- The Chat `notify` field was documented as "notify users immediately"; it
  asks the user to confirm the post, and the description now says so.

### Fixed

- `tasks_tasks_digest` was published with `readOnlyHint: false` and
  `idempotentHint: false` (its local name starts with its namespace, which the
  annotation rules stripped); it is now read-only and idempotent.
- `drive_hide_drive` and `drive_unhide_drive` failed on every call: they
  passed `useDomainAdminAccess`, which `drives.hide`/`drives.unhide` do not
  accept, and the Google client raised before sending the request.

### Added

- `docs_read_document(document_id, format=text|markdown|raw, offset)`: a
  document as clean text or Markdown (headings, nested lists, tables, links,
  person and rich-link chips, footnotes, every tab), paged like an email
  body (`body`, `truncated`, `next_offset`, `body_chars`, `body_sha256`);
  `raw` returns the Document resource unchanged.
- `drive_read_file(file_id, format=text|markdown, offset)`: the text of a
  Drive file with the same paging fields, without the server's filesystem:
  Google Docs (also as Markdown) and Slides exported as text, Sheets as CSV
  of the first sheet, stored text files up to 5 MB downloaded; binary files,
  folders, forms and drawings fail with `invalid_input`.
- `tasks_list_tasks` and `tasks_tasks_digest` take `tasklist_id` as
  optional; omitted, they read the account's default list.
- `tests/test_catalog_budget.py` pins a ceiling for the default and
  all-optional catalogs; `scripts/measure_tokens.py` prints their size.
- `tests/fixtures/google_payloads/` holds a recorded-shape Google payload
  per normalizer and its golden output (`tests/test_response_goldens.py`);
  `scripts/measure_tokens.py` reports each response's size against its
  payload, and per-item token ceilings are tested.

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
