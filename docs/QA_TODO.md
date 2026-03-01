# Google Workspace MCP – QA test list

All tests assume real credentials and APIs enabled. Destructive tests create then delete where noted.

---

## Calendar full matrix run (2026-02-28)

Scope executed with live API calls and attendee invite target `guilherme.inacio@franq.com.br`.

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
  - [x] verify persisted fields (validated in focused rerun with `calendar_get_event`)
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
- Focused mutation persistence rerun succeeded on 2026-03-01 using create -> update -> `calendar_get_event` verification (summary/description/attendees/reminders/recurrence all persisted).

---

## Gmail (`gmail_*`)

| # | Tool | Test | Notes |
|---|------|------|--------|
| 1 | gmail_list_labels | List all labels | Read-only |
| 2 | gmail_list_emails | List INBOX, max_results=5 | Read-only |
| 3 | gmail_search_emails | Search e.g. "unsubscribe", max_results=5 | Find ad-like mail for delete |
| 4 | gmail_read_email | Get one message by ID from list_emails | Read-only |
| 5 | gmail_send_email | Send to self, confirm_send=False | Use existing address |
| 6 | gmail_mark_as_read | Mark one message read | Id from list |
| 7 | gmail_mark_as_unread | Mark same message unread | Id from list |
| 8 | gmail_delete_email | Trash one message (ad/unsubscribe), permanent=False | Use ID from search |
| 9 | gmail_move_email | Add/remove label on one message | ModifyMessageRequest |
| 10 | gmail_list_attachments | List attachments for a message that has one | Optional if no such mail |
| 11 | gmail_batch_modify | Batch add/remove labels on 2 message IDs | Small batch |
| 12 | gmail_batch_delete | Trash 1–2 messages, permanent=False | Small batch |
| 13 | gmail_create_label | Create label "QA-test-label" | Then delete_label |
| 14 | gmail_update_label | Update label (e.g. name) | If create succeeded |
| 15 | gmail_delete_label | Delete "QA-test-label" | After create/update |
| 16 | gmail_apply_labels | Apply label to message | Id + label from above |
| 17 | gmail_summarize_email | Summarize one message by ID | Sampling tool |
| 18 | gmail_download_attachment | Download one attachment to temp file | If message with attachment exists |
| 19 | gmail_list_filters | List all existing Gmail filters | Read-only |
| 20 | gmail_create_filter | Create filter with criteria+action | Requires label or forward target |
| 21 | gmail_delete_filter | Delete created test filter | Cleanup after create |
| 22 | gmail_list_drafts | List drafts | Read-only |
| 23 | gmail_create_draft | Create test draft | Then get/update/delete |
| 24 | gmail_get_draft | Get draft by id | From create_draft |
| 25 | gmail_update_draft | Update existing draft | Optional content update |
| 26 | gmail_send_draft | Send draft by id | Optional, side-effectful |
| 27 | gmail_delete_draft | Delete draft by id | Cleanup |
| 28 | gmail_list_threads | List threads | Read-only |
| 29 | gmail_get_thread | Get one thread | Use thread_id from read_email/list_threads |
| 30 | gmail_modify_thread | Add/remove labels on a thread | Requires label ids |
| 31 | gmail_trash_thread | Move thread to trash | Then untrash_thread |
| 32 | gmail_untrash_thread | Restore trashed thread | Cleanup |
| 33 | gmail_delete_thread | Permanently delete thread | Optional, destructive |
| 34 | gmail_list_history | List history from startHistoryId | Use history id from message/thread |
| 35 | gmail_list_forwarding_addresses | List forwarding addresses | Read-only |
| 36 | gmail_get_forwarding_address | Get one forwarding address | If known address exists |
| 37 | gmail_create_forwarding_address | Create forwarding address | May require account policy/verification |
| 38 | gmail_delete_forwarding_address | Delete forwarding address | Cleanup |
| 39 | gmail_get_vacation_settings | Read vacation responder settings | Read-only |
| 40 | gmail_update_vacation_settings | Update vacation settings | Revert after test |
| 41 | gmail_untrash_email | Restore one trashed message | Use message_id from delete_email |
| 42 | gmail_mark_as_spam | Mark one message as spam | Then mark_as_not_spam |
| 43 | gmail_mark_as_not_spam | Mark message as not spam | Restore to inbox |

---

## Calendar (`calendar_*`)

| # | Tool | Test | Notes |
|---|------|------|--------|
| 22 | calendar_list_calendars | List calendars | Read-only |
| 23 | calendar_get_events | List primary, next 7 days, max 10 | Read-only |
| 23a | calendar_get_event | Get one event by event_id | Deterministic verification for mutation |
| 23b | calendar_list_event_attachments | List attachments on one event | Read-only metadata |
| 23c | calendar_add_event_attachment | Add one attachment by fileUrl | Requires valid Drive-style file URL + supportsAttachments |
| 23d | calendar_remove_event_attachment | Remove attachment by fileUrl/fileId | Cleanup |
| 23e | calendar_download_event_attachment | Download/export attached file to local path | Uses Drive API; Google-native docs are exported (e.g. PDF) |
| 24 | calendar_get_timezone_info | Get timezone info | Read-only |
| 25 | calendar_get_current_date | Get current date | Read-only |
| 26 | calendar_check_availability | FreeBusy for primary, 1-day window | Read-only |
| 27 | calendar_create_event | Create "QA test event" 1h from now | Then delete with force=True |
| 28 | calendar_update_event | Update event summary/description | Use event from create |
| 29 | calendar_delete_event | Delete QA test event, force=True | After create |

---

## Drive (`drive_*`)

| # | Tool | Test | Notes |
|---|------|------|--------|
| 30 | drive_list_files | List recent files, page_size=10 | Read-only |
| 31 | drive_get_file | Read one file by id | Use id from upload/create |
| 32 | drive_create_folder | Create folder "QA Drive MCP Folder" | Cleanup optional |
| 33 | drive_create_file_metadata | Create metadata-only file (and Google Doc for export test) | Side-effectful |
| 34 | drive_upload_file | Upload local text fixture file | Then download/update/copy |
| 35 | drive_update_file_metadata | Rename/update description/properties | Mutation |
| 36 | drive_update_file_content | Replace content from local file | Mutation |
| 37 | drive_move_file | Move by addParents/removeParents | Optional depending on structure |
| 38 | drive_copy_file | Copy uploaded file | Then cleanup |
| 39 | drive_delete_file | Delete one generated test file | Cleanup |
| 40 | drive_download_file | Download uploaded file to tmp path | Validate bytes > 0 |
| 41 | drive_export_google_file | Export Google Doc to PDF | Requires google-apps file id |
| 42 | drive_get_file_content_capabilities | Read downloadable/exportable metadata | Read-only |
| 43 | drive_list_permissions | List sharing permissions for test file | Read-only |
| 44 | drive_create_permission | Create test permission (e.g. anyone reader) | Cleanup required |
| 45 | drive_get_permission | Get created permission | Uses permission id |
| 46 | drive_update_permission | Update role on created permission | Policy dependent |
| 47 | drive_delete_permission | Delete created permission | Cleanup |
| 48 | drive_list_drives | List Shared Drives | Read-only |
| 49 | drive_get_drive | Get one Shared Drive by id | If list_drives returns one |
| 50 | drive_hide_drive | Hide Shared Drive | Optional/admin side-effect |
| 51 | drive_unhide_drive | Unhide Shared Drive | Optional/admin side-effect |

---

## Keep (`keep_*`) – only when ENABLE_KEEP=true

| # | Tool | Test | Notes |
|---|------|------|--------|
| 27 | keep_list_notes | List notes, page_size=5 | Read-only |
| 28 | keep_create_note | Create note "QA test note", confirm_create=False | Then delete |
| 29 | keep_get_note | Get note by ID from list/create | Read-only |
| 30 | keep_delete_note | Delete QA note, confirm_delete=False | After create |
| 31 | keep_share_note | (Optional) Share note if we have collaborator | May skip |
| 32 | keep_update_note | Call (expect unsupported) | Expect status unsupported |
| 33 | keep_archive_note | Call (expect unsupported) | Expect status unsupported |
| 34 | keep_list_keep_labels | Call (expect unsupported) | Expect status unsupported |
| 35 | keep_summarize_note | Summarize one note by name | Sampling tool |

---

## Chat (`chat_*`)

| # | Tool | Test | Notes |
|---|------|------|--------|
| 36 | chat_list_spaces | List spaces, page_size=10 | Read-only |
| 37 | chat_get_space | Get first space by name | From list_spaces |
| 38 | chat_list_messages | List messages in one space, page_size=5 | Read-only |
| 39 | chat_get_message | Get one message by name | From list_messages |
| 40 | chat_create_message | Post "QA test" in first space, notify=False | Then delete |
| 41 | chat_update_message | Update message text | Use message from create |
| 42 | chat_delete_message | Delete QA message, force=True | After create |
| 43 | chat_summarize_space_messages | Summarize messages in one space | Sampling (needs ctx) |

---

## Resources (read by URI)

| # | URI / template | Test | Notes |
|---|----------------|------|--------|
| 44 | gmail://inbox/summary | Read resource | Inbox summary |
| 45 | gmail://labels | Read resource | Labels list |
| 46 | gmail://email/{message_id} | Read with ID from list_emails | One message |
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
2. Read-only tools first (list_*, get_*, search_*, check_availability).
3. Create resources (create_event, create_note, create_message, create_label, create_folder/upload_file if desired).
4. Update tools (update_event, update_message, update_label, move_email, apply_labels, drive metadata/content updates).
5. Resource reads (all URIs above).
6. Prompt renders (all prompt names + minimal args).
7. Destructive with created IDs: delete_event (force), delete_note (confirm_delete=False), delete_message (force=True), delete_email (trash), delete_label, drive_delete_file, batch_delete (trash).
8. Optional: download_attachment if message with attachment found; summarize_* tools.

---

## Running the QA script

From project root (with `credentials.json` and `token.json` in place):

```powershell
uv run python scripts/qa_run_all_tools.py
```

- **In-memory client**: Uses `Client(workspace_mcp)` so the server runs in the same process; credentials from lifespan are used.
- **Namespaced resources**: Mounted resources use a path prefix (e.g. `gmail://gmail/inbox/summary`, `calendar://calendar/today`).
- **Expected failures** (environment-dependent):
  - **Chat**: All `chat_*` tools and `chat://chat/*` resources fail with 404 if the Google Chat API / Chat app is not enabled and configured in the project.
  - **gmail_summarize_email**: Fails with "Client does not support sampling" unless the client provides a `sampling_handler`.
  - **gmail_move_email / gmail_batch_modify**: May return 400 if messages are invalid (e.g. already trashed) or label state is inconsistent.
  - **Forwarding create/delete**: May fail due to Gmail policy, verification state, or workspace admin restrictions.
  - **Vacation update**: Mutating settings requires scope/consent and should be tested with rollback.
  - **chat://chat/users/me**: May show "Unknown resource" depending on Chat API/project setup.
