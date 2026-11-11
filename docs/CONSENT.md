# Consent for consequential calls

One model decides when a tool call needs the user's consent and how that
consent is collected. The table is `CONSENT_TABLE` in
`src/mcp_google_workspace/common/consent.py`; the tool guard, the task
submission check, `prepare_workspace_action`, the tool annotations and the
contract tests all read it.

## Classes

Every call is classified from its tool and its validated arguments (defaults
applied) into one of three classes:

| Class | Meaning | Consent |
| --- | --- | --- |
| `read` | Changes nothing. | No |
| `write` | A reversible change: a draft, a label, a document edit, a move to Trash, fewer than 10 items. | No |
| `consequential` | Sends mail, replies, drafts, invitations or Chat messages; shares or unshares; deletes permanently; changes forwarding, a filter that forwards or trashes, or turns the auto-reply on; disconnects the account; changes 10 or more items in one call (`BULK_THRESHOLD`). | Yes |

Arguments that change what happens decide the class: `mode: permanent`,
`delete_mode: permanent`, `send_updates` that emails guests, a guest list, the
number of distinct message IDs, the kinds of raw batch requests. No argument
can skip consent for a call that is consequential.

## The table

Rows of `CONSENT_TABLE` that can be consequential; every other tool is `read`
or `write`. The first matching condition of a tool selects the action name
the user sees and the continuation binds.

| Tool | Consequential when | Action | Tier |
| --- | --- | --- | --- |
| `calendar_create_event` | attendees not empty | `create_event` | core |
| `calendar_delete_event` | always | `delete_event` | core |
| `calendar_set_event_attachments` | remove not empty | `remove_event_attachment` | core |
| `calendar_set_event_attachments` | send_updates 'all' or 'externalOnly' | `update_event_attachments` | core |
| `calendar_update_event` | attendees or recurrence given, or send_updates 'all' or 'externalOnly' | `update_event` | core |
| `chat_create_message` | always | `create_message` | optional |
| `chat_delete_message` | always | `delete_message` | optional |
| `chat_post_message_simple` | always | `post_message_simple` | optional |
| `chat_reply_to_message` | always | `reply_to_message` | optional |
| `disconnect_google_workspace` | always | `disconnect` | core |
| `docs_batch_update_document` | 10 or more requests, or any delete* request | `batch_update_document` | extended |
| `drive_delete_file` | delete_mode 'permanent' | `delete_file` | core |
| `drive_share_file` | permission_id given | `update_permission` | core |
| `drive_share_file` | always (a new grant, any type) | `create_permission` | core |
| `drive_unshare_file` | always | `delete_permission` | core |
| `forms_batch_update_form` | 10 or more requests, or any delete* request | `batch_update_form` | extended |
| `gmail_delete` | mode 'permanent' with thread_id | `delete_thread` | core |
| `gmail_delete` | mode 'permanent' with one message | `delete_email` | core |
| `gmail_delete` | mode 'permanent' with several messages | `batch_delete` | core |
| `gmail_delete` | mode 'trash' with 10 or more distinct message_ids | `batch_trash` | core |
| `gmail_delete_draft` | always | `delete_draft` | core |
| `gmail_manage_filters` | action 'delete' | `delete_filter` | extended |
| `gmail_manage_filters` | action 'create' with filter_action.forward or a TRASH label | `create_filter` | extended |
| `gmail_manage_forwarding` | action 'create' | `create_forwarding_address` | extended |
| `gmail_manage_forwarding` | action 'delete' | `delete_forwarding_address` | extended |
| `gmail_manage_labels` | action 'delete' | `delete_label` | core |
| `gmail_manage_vacation` | action 'update' with enable_auto_reply true | `update_vacation` | extended |
| `gmail_modify_messages` | 10 or more distinct message_ids | `modify_messages` | core |
| `gmail_reply` | reply_all true | `reply_all_email` | core |
| `gmail_reply` | always (reply_all false) | `reply_email` | core |
| `gmail_send_draft` | always | `send_draft` | core |
| `gmail_send_email` | always | `send_email` | core |
| `keep_append_note_content` | apply_via_replacement and delete_original_on_apply true | `delete_note` | optional |
| `keep_create_note` | collaborator_emails not empty | `create_note` | optional |
| `keep_delete_note` | always | `delete_note` | optional |
| `keep_patch_note_checklist` | apply_via_replacement and delete_original_on_apply true | `delete_note` | optional |
| `keep_share_note` | always | `share_note` | optional |
| `keep_unshare_note` | always | `unshare_note` | optional |
| `meet_end_active_conference` | always | `end_active_conference` | optional |
| `meet_update_space` | config.accessType 'OPEN' | `update_space` | optional |
| `people_delete_contact` | always | `delete_contact` | core |
| `sheets_batch_update_spreadsheet` | 10 or more requests, or any delete* request | `batch_update_spreadsheet` | extended |
| `slides_batch_update_presentation` | 10 or more requests, or any delete* request | `batch_update_presentation` | extended |
| `tasks_delete_task` | always | `delete_task` | core |
| `tasks_update_task` | deleted true | `delete_task` | core |

## How consent is collected

`MCP_CONSENT` (default `required`) selects the policy.

| Client | `required` | `preview_only` | `off` |
| --- | --- | --- | --- |
| Declared elicitation, MCP 2026-07-28 | The call returns `input_required` with the preview; the answering retry runs it once. | `confirmation_required` + prepare step | Runs |
| Declared elicitation, handshake era | `ctx.elicit` with the preview. | `confirmation_required` + prepare step | Runs |
| No elicitation (either era) | `confirmation_required` + prepare step | `confirmation_required` + prepare step | Runs |
| Background task, client can elicit | The task parks in `input_required`; `tasks/update` answers. | Refused at submission with the prepare step | Runs |
| Background task, client cannot elicit | Refused at submission with the prepare step (the worker cannot see the client's capabilities). | Same | Runs |

The prepare step: the error's `required_action` is
`{"tool": "prepare_workspace_action", "arguments": {"tool_name": ..., "arguments": ...}}`
and `details` carries `action` and `preview`. `prepare_workspace_action` runs
the call up to its consent gate through the full middleware chain (input
schema, authorization, admission) without running the tool, and returns
`action`, the same `preview` text, a one-time `commit_token` and
`expires_at`. `commit_workspace_action` runs exactly that call once:

- The token is bound to the principal and to the digest of the validated
  arguments; a record whose arguments no longer match is rejected
  (`confirmation_invalid`), and nothing else can be committed with it.
- A repeated commit returns the saved result; an expired, unknown or other
  user's token is rejected; an uncertain outcome reports `outcome_unknown`.
- The grant covers one call of one tool. A client that can elicit is still
  asked when it commits, with the same preview, so prepare/commit never
  replaces the user's own answer.
- For clients without elicitation, the host's permission prompt on
  `commit_workspace_action` is the human gate. Hosts should not auto-approve
  that tool; it is annotated `destructiveHint: true`.

Declining or cancelling returns
`{"status": "cancelled", "action": ..., "reason": ...}` with nothing changed.
`MCP_CONSENT=off` is for unattended deployments that accept the risk; the
server logs a warning at every start.

## Previews

The preview renderer of each row produces the exact text shown in the
elicitation, in `details.preview` and in the prepare result: who (every
address, deduplicated, with the total), what (subject, the start of the text,
IDs, roles, labels, request kinds) and how many. Replies and draft sends read
the source message or the draft to show the real recipients; those reads are
made once per call and reused by the tool. `MCP_EMAIL_PREPARE_RECIPIENTS`
(default 10) only adds a "large send" line to a send preview.

`gmail_modify_messages`, `gmail_delete` and the four `*_batch_update_*` tools
take `dry_run: true`: the result is `{"status": "dry_run", "preview",
"consent_required", "action"}` and nothing is sent to Google. A dry run never
asks the user; `consent_required` says whether the same call without
`dry_run` would ask, and `action` names the confirmation it would show. The
two Gmail tools add `count` (distinct message IDs, null for a `thread_id`) and
`requested_count` (the IDs as given), since the preview text lists only the
first few.

## Annotations

`readOnlyHint` is true only for `read` tools; `destructiveHint` is true for
every tool some call of which is consequential (and for
`commit_workspace_action`); `idempotentHint` is stated per row. Name prefixes
play no part.

## Known limits

- Classification is per call. Ten changes split into calls of nine are not
  detected as one bulk change.
- A whole thread counts as one item: trashing or relabelling a thread is a
  `write` however many messages it holds (trash is reversible for 30 days).
- Additive raw batch updates under 10 requests are `write`: Docs, Sheets and
  Slides keep version history, but an `updateCells` that overwrites values is
  not distinguished from one that fills empty cells.
- `drive_move_file` into a shared folder can widen who sees a file; moves are
  `write`.
- `calendar_respond_to_event` notifies the organizer (default
  `send_updates: all`); answering an invitation is `write`.
- Preview text shows arguments as given (a label name, a file ID); it does
  not read names for every ID.
- With prepare/commit the human gate is the host's permission prompt; a host
  configured to approve every tool call gives the model that consent.
