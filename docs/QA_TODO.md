# Google Workspace MCP – QA test list

All tests assume real credentials and APIs enabled. Destructive tests create then delete where noted.

---

## Calendar full matrix run (2026-02-28)

Scope executed with live API calls and an attendee invite target (`QA_CALENDAR_ATTENDEE`).

- [x] Time handling matrix
  - [x] naive datetime
  - [x] timezone-aware datetime
  - [x] date-only inputs
  - [x] DST boundary dates
- [x] Event shape coverage
  - [x] description/location
  - [x] attendees list (invite target included)
  - [x] reminders override
  - [x] recurrence rules
- [x] Mutation behavior
  - [x] partial update (single field)
  - [x] update with attendees/reminders/recurrence
  - [x] verify persisted fields (validated in focused rerun with `calendar_read_events`)
- [x] Error-path coverage
  - [x] invalid calendar ID (expected 404)
  - [x] malformed datetime (expected 400)
  - [x] end before start (expected 400 timeRangeEmpty)
  - [x] deleting non-existent event (expected 404)
- [x] Notification/update modes
  - [x] `send_updates=all` on update/delete
  - [x] `send_updates=externalOnly` on update/delete
  - [x] `send_updates=none` on update/delete
- [x] Pagination/perf checks
  - [x] small vs larger `max_results`
  - [x] response consistency

Run summary: **14 passed / 1 failed**.

Failure detail:
- Mutation verification did not locate the updated recurring event by original event ID in list output during the verification query window.

Follow-up note:
- Additional check found Calendar API rejects `orderBy=startTime` when `singleEvents=false` (`invalid orderBy` for that query). If we need recurring-master verification, the tool should conditionally omit `orderBy` for `singleEvents=false`.
- Focused mutation persistence rerun succeeded on 2026-03-01 using create -> update -> `calendar_read_events` verification (summary/description/attendees/reminders/recurrence all persisted).

---

## Gmail (`gmail_*`)

Filters, forwarding and vacation (`gmail_manage_filters`, `gmail_manage_forwarding`, `gmail_manage_vacation`) are extended-tier tools: run with `MCP_TOOL_TIER=extended` (the QA script sets it).

| # | Tool | Test | Notes |
|---|------|------|--------|
| 1 | gmail_list_labels | List all labels | Read-only |
| 2 | gmail_search_emails | List INBOX via labels=["INBOX"], max_results=5 | Read-only; labels take names or IDs |
| 3 | gmail_search_emails | Search e.g. "unsubscribe", max_results=5 | Find ad-like mail for delete |
| 4 | gmail_read_emails | Read one or more IDs from search_emails | Read-only |
| 5 | gmail_send_email | Send to self (asks for consent) | Use existing address |
| 6 | gmail_modify_messages | mark_read=true on one message | Id from search |
| 7 | gmail_modify_messages | mark_read=false on same message | Id from search |
| 8 | gmail_delete | Trash one message (ad/unsubscribe), mode="trash" | Use ID from search |
| 9 | gmail_modify_messages | add_labels / remove_labels on one message (label ID) | Replaces move/apply labels |
| 10 | gmail_read_emails | Attachments of a message that has one (format="metadata") | Each attachment carries download_id; optional if no such mail |
| 11 | gmail_modify_messages | add_labels / remove_labels on 2 message_ids | Small batch |
| 12 | gmail_delete | Trash 1–2 message_ids, mode="trash" | Small batch |
| 13 | gmail_manage_labels | action="create", name "QA-test-label" | Then update and delete |
| 14 | gmail_manage_labels | action="update", label=<id or name>, new name | If create succeeded |
| 15 | gmail_manage_labels | action="delete", label=<id or name> | After create/update; asks to confirm |
| 16 | gmail_modify_messages | add_labels=["QA-test-label"] by name | Name resolution; before label delete |
| 18 | gmail_download_attachment | Download one attachment (attachment_id=download_id) to temp file | If message with attachment exists |
| 19 | gmail_manage_filters | action="list" | Read-only; extended tier |
| 20 | gmail_manage_filters | action="create", criteria + filter_action{add_labels/remove_labels/forward} | Requires label or forward target |
| 21 | gmail_manage_filters | action="delete", filter_id | Cleanup after create; asks to confirm |
| 22 | gmail_list_drafts | List drafts | Read-only |
| 23 | gmail_save_draft | Create test draft (no draft_id) | Then read/rewrite/delete |
| 24 | gmail_list_drafts | Read one draft with draft_id | From save_draft |
| 25 | gmail_save_draft | Rewrite existing draft (draft_id) | Replaces recipients/subject/body |
| 26 | gmail_send_draft | Send draft by id | Optional, side-effectful |
| 27 | gmail_delete_draft | Delete draft by id | Cleanup |
| 28 | gmail_search_emails | group_by_thread=true | Read-only; returns threads |
| 29 | gmail_get_thread | Get one thread | Use thread_id from read_emails/search_emails |
| 30 | gmail_modify_messages | thread_id + add_labels / remove_labels | Requires label ids or names |
| 31 | gmail_delete | thread_id, mode="trash" | Then untrash |
| 32 | gmail_modify_messages | thread_id, untrash=true | Cleanup |
| 33 | gmail_delete | thread_id, mode="permanent" | Optional, destructive; asks to confirm |
| 34 | gmail_reply | Reply (reply_all=false / true) to a message (asks for consent) | Optional, side-effectful |
| 35 | gmail_manage_forwarding | action="list" | Read-only; extended tier |
| 36 | gmail_manage_forwarding | action="get", forwarding_email | If known address exists |
| 37 | gmail_manage_forwarding | action="create", forwarding_email | May require account policy/verification |
| 38 | gmail_manage_forwarding | action="delete", forwarding_email | Cleanup; asks to confirm |
| 39 | gmail_manage_vacation | action="get" | Read-only; extended tier |
| 40 | gmail_manage_vacation | action="update" | Revert after test |
| 41 | gmail_modify_messages | untrash=true on the message trashed in #8 | Restore from Trash |
| 42 | gmail_modify_messages | spam=true on one message | Then spam=false |
| 43 | gmail_modify_messages | spam=false | Removes SPAM and restores to inbox |

---

## Calendar (`calendar_*`)

| # | Tool | Test | Notes |
|---|------|------|--------|
| 22 | calendar_get_calendar_context | include_calendars=true: list calendars | Read-only; returns calendars |
| 23 | calendar_search_events | List primary, next 7 days, max 10 | Read-only |
| 23a | calendar_read_events | Read one or more events including attachments | Deterministic verification for mutation |
| 23c | calendar_set_event_attachments | add=[{file_url}] one attachment | Requires valid Drive-style file URL |
| 23d | calendar_set_event_attachments | remove=[{file_url or file_id}] | Cleanup |
| 23e | drive_download_file | Download/export an event attachment by its Drive file_id | Google-native docs are exported (PDF by default) |
| 24 | calendar_get_calendar_context | Get timezone and local/UTC date-time context | Read-only |
| 26 | calendar_find_free_time | participants=["primary"], time_min/time_max window | Read-only; all_free, suggested_slots, total_suggestions |
| 27 | calendar_create_event | Create "QA test event" 1h from now | Then delete with force=True |
| 28 | calendar_update_event | Update event summary/description | Use event from create |
| 29 | calendar_delete_event | Delete QA test event, force=True | After create |

---

## Drive (`drive_*`)

`drive_update_drive` is an extended-tier tool (`MCP_TOOL_TIER=extended`).

| # | Tool | Test | Notes |
|---|------|------|--------|
| 30 | drive_list_files | List recent files, page_size=10 | Read-only |
| 31 | drive_get_file | Read one file by id | Use id from upload/create |
| 32 | drive_create_folder | Create folder "QA Drive MCP Folder" | Cleanup optional |
| 33 | drive_upload_file | No local_path/uploaded_file: metadata-only create (Google Doc for export test) | Side-effectful; mode "metadata_only" |
| 34 | drive_upload_file | Upload local text fixture file | Then download/update/copy |
| 35 | drive_update_file_metadata | Rename/update description/properties | Mutation |
| 36 | drive_upload_file | file_id + local_path: replace content | Mutation; mode "replace" |
| 37 | drive_move_file | Move by addParents/removeParents | Optional depending on structure |
| 38 | drive_copy_file | Copy uploaded file | Then cleanup |
| 39 | drive_delete_file | Delete one generated test file | Cleanup |
| 40 | drive_download_file | Download uploaded file to tmp path | Validate bytes > 0 |
| 41 | drive_download_file | Export Google Doc, export_mime_type="application/pdf" | Requires google-apps file id; mode "export" |
| 42 | drive_get_file | is_google_native / export_formats of the Google Doc | Read-only |
| 43 | drive_list_permissions | List sharing permissions for test file | Read-only |
| 44 | drive_share_file | Create test permission (e.g. anyone reader) | type="anyone" needs prepare_workspace_action + commit_workspace_action; cleanup required |
| 45 | drive_list_permissions | Get created permission (permission_id) | One-item list |
| 46 | drive_share_file | permission_id + new role | Policy dependent |
| 47 | drive_unshare_file | Delete created permission | Cleanup; asks to confirm |
| 48 | drive_list_drives | List Shared Drives | Read-only |
| 49 | drive_list_drives | Get one Shared Drive (drive_id) | If list_drives returns one |
| 50 | drive_update_drive | hidden=true | Optional/admin side-effect; extended tier |
| 51 | drive_update_drive | hidden=false | Optional/admin side-effect; extended tier |

---

## Keep (`keep_*`) – only when ENABLE_KEEP=true

| # | Tool | Test | Notes |
|---|------|------|--------|
| 27 | keep_list_notes | List notes, page_size=5 | Read-only |
| 28 | keep_create_note | Create note "QA test note" | Then delete |
| 29 | keep_get_note | Get note by ID from list/create | Read-only |
| 30 | keep_delete_note | Delete QA note (asks for consent) | After create |
| 31 | keep_share_note | (Optional) Share note if we have collaborator | May skip |
| 32 | keep_update_note | Call (expect unsupported) | Expect status unsupported |
| 33 | keep_archive_note | Call (expect unsupported) | Expect status unsupported |
| 34 | keep_list_keep_labels | Call (expect unsupported) | Expect status unsupported |

---

## Chat (`chat_*`)

| # | Tool | Test | Notes |
|---|------|------|--------|
| 36 | chat_list_spaces | List spaces, page_size=10 | Read-only |
| 37 | chat_get_space | Get first space by name | From list_spaces |
| 38 | chat_list_messages | List messages in one space, page_size=5 | Read-only |
| 39 | chat_get_message | Get one message by name | From list_messages |
| 40 | chat_create_message | Post "QA test" in first space (asks for consent) | Then delete |
| 41 | chat_update_message | Update message text | Use message from create |
| 42 | chat_delete_message | Delete QA message, force=True | After create |

---

## Resources (read by URI)

| # | URI / template | Test | Notes |
|---|----------------|------|--------|
| 44 | gmail://inbox/summary | Read resource | Inbox summary |
| 45 | gmail://labels | Read resource | Labels list |
| 46 | gmail://email/{message_id} | Read with ID from search_emails | One message |
| 47 | calendar://today | Read resource | Today events |
| 48 | calendar://week | Read resource | Week events |
| 49 | drive://recent | Read resource | Recent Drive files |
| 50 | drive://shared-drives | Read resource | Shared Drives list |
| 51 | drive://file/{file_id} | Read with ID from upload/create | One file metadata |
| 52 | keep://notes/recent | Read (if Keep enabled) | Recent notes |
| 53 | keep://note/{note_id} | Read with ID (if Keep enabled) | One note |
| 54 | chat://spaces | Read resource | Spaces list |
| 55 | chat://space/{space_id}/messages | Read with ID from list_spaces | Messages in space |
| 56 | chat://space/{space_id}/members | Read with space ID | Members |
| 57 | chat://users/{user_ref} | Read with e.g. me or user ID | User resource |

---

## Prompts (render by name with args)

| # | Prompt | Test | Notes |
|---|--------|------|--------|
| 58 | gmail_compose_email_prompt | Render topic="meeting", tone="professional" | Returns string |
| 59 | gmail_reply_email_prompt | Render original_email="Hi", intent="accept" | Returns string |
| 60 | gmail_summarize_inbox_prompt | Render count=5 | Returns string |
| 61 | keep_summarize_keep_note_prompt | Render note_text="Buy milk" (if Keep enabled) | Returns string |
| 62 | keep_extract_actions_from_keep_notes_prompt | Render notes_blob="Note 1" (if Keep enabled) | Returns string |
| 63 | chat_draft_chat_announcement_prompt | Render topic="Release", audience="team" | Returns string |
| 64 | chat_summarize_chat_thread_prompt | Render thread_messages="A: Hi\nB: Bye" | Returns string |

---

## Execution order (for script)

1. Auth once (lifespan / get_credentials).
2. Read-only tools first (list_*, get_*, search_*, find_free_time).
3. Create resources (create_event, create_note, create_message, manage_labels create, create_folder/upload_file if desired).
4. Update tools (update_event, update_message, manage_labels update, modify_messages, drive metadata updates and upload_file content replacement).
5. Resource reads (all URIs above).
6. Prompt renders (all prompt names + minimal args).
7. Destructive with created IDs: delete_event, delete_note, delete_message (each asks for consent), gmail_delete (mode="trash"), manage_labels delete, drive_delete_file.
8. Optional: download_attachment if a message with an attachment is found (download_id from read_emails); summarize_* tools.

---

## Running the QA script

From project root (with `credentials.json` and `token.json` in place):

```powershell
uv run python scripts/qa_run_all_tools.py
```

- **In-memory client**: Uses `Client(workspace_mcp)` so the server runs in the same process; credentials from lifespan are used.
- **Tool tier**: The script sets `MCP_TOOL_TIER=extended` (unless already set) so the Gmail filter/forwarding/vacation tools and `drive_update_drive` are registered.
- **Confirmations**: The script answers the server's confirmation questions with yes and prints each one; it deletes only what it created (plus moving the "unsubscribe" search hit to Trash).
- **Optional steps**: `ENABLE_DRAFT_SEND` (with `QA_DRAFT_TO`), `ENABLE_FORWARDING_CREATE_DELETE` (`QA_FORWARDING_EMAIL`), `ENABLE_VACATION_UPDATE`, `ENABLE_DRIVE_HIDE_UNHIDE`, `ENABLE_KEEP`, `ENABLE_CHAT`; `QA_CALENDAR_ATTENDEE` invites an attendee to the QA event.
- **Namespaced resources**: Mounted resources use a path prefix (e.g. `gmail://gmail/inbox/summary`, `calendar://calendar/today`).
- **Expected failures** (environment-dependent):
  - **Chat**: All `chat_*` tools and `chat://chat/*` resources fail with 404 if the Google Chat API / Chat app is not enabled and configured in the project.
  - **gmail_modify_messages**: May return 400 if messages are invalid (e.g. already trashed) or label state is inconsistent.
  - **Forwarding create/delete**: May fail due to Gmail policy, verification state, or workspace admin restrictions (create and delete need a service account with domain-wide delegation).
  - **Vacation update**: Mutating settings requires scope/consent and should be tested with rollback.
  - **chat://chat/users/me**: May show "Unknown resource" depending on Chat API/project setup.
