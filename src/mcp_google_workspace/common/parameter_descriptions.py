"""Shared tool parameter descriptions."""

from __future__ import annotations

# Descriptions for parameter names whose meaning is identical in every tool
# that uses them. A tool documents any parameter not listed here (or whose
# meaning differs from this text) itself, with an ``Annotated`` description;
# there is no generated fallback, and tests/test_description_lint.py fails on
# a parameter without one.
SHARED_PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "page_token": "The next_page_token from the previous response of this same call; omit for the first page.",  # nosec B105 -- schema documentation, not a credential
    "idempotency_key": (
        "Caller-chosen key that makes a retry safe: repeating the call with the same key and "
        "arguments returns the first call's result instead of doing the work twice."
    ),
    "message_id": "Gmail message ID, from gmail_search_emails, gmail_get_mail_digest, gmail_check_mail_updates or gmail_get_thread results.",
    "message_ids": "Gmail message IDs, from gmail_search_emails, gmail_get_mail_digest, gmail_check_mail_updates or gmail_get_thread results.",
    "thread_id": "Gmail thread ID, from the thread_id field of Gmail search, digest and read results.",
    "draft_id": "Gmail draft ID, as returned by gmail_create_draft or gmail_list_drafts (not the draft's message ID).",
    "label_id": "Gmail label ID: a system label such as INBOX or STARRED, or a user label ID (Label_...) from gmail_list_labels.",
    "add_label_ids": (
        "Label IDs to add: system labels such as INBOX, STARRED, IMPORTANT, UNREAD, or user label "
        "IDs (Label_...) from gmail_list_labels. Label names are not accepted."
    ),
    "remove_label_ids": (
        "Label IDs to remove: system labels such as INBOX, STARRED, IMPORTANT, UNREAD, or user label "
        "IDs (Label_...) from gmail_list_labels. Label names are not accepted."
    ),
    "filter_id": "Gmail filter ID, from gmail_list_filters or gmail_create_filter.",
    "attachment_id": "Gmail attachment ID (the message part's attachmentId) of the same message, listed as download_id by gmail_list_attachments.",
    "include_spam_trash": "Also return items in Spam and Trash; by default they are excluded.",
    "label_list_visibility": "Whether the label appears in Gmail's label list: labelShow, labelShowIfUnread (only while it has unread mail), or labelHide.",
    "message_list_visibility": "Whether the label is shown on messages in Gmail's message list: show or hide.",
    "background_color": "Label background color as a hex string from Gmail's fixed label palette, e.g. '#16a766'; set together with text_color.",
    "text_color": "Label text color as a hex string from Gmail's fixed label palette, e.g. '#ffffff'; set together with background_color.",
    "calendar_id": "Calendar ID: 'primary' for the user's main calendar, or an ID from calendar_list_calendars (often an email address).",
    "event_id": "Calendar event ID, from calendar_search_events, calendar_get_calendar_digest or calendar_read_events results.",
    "event_ids": "Calendar event IDs, all from the same calendar_id, as returned by calendar search and digest results.",
    "color_id": "Event color ID from '1' to '11' (Google Calendar's fixed event palette); omit to use the calendar's color.",
    "send_updates": (
        "Who Google notifies about this change: 'all' guests, 'externalOnly' (only guests who do not "
        "use Google Calendar, by email), or 'none'."
    ),
    "time_zone": "IANA time zone name, e.g. 'Europe/Berlin'; defaults to the account's calendar time zone.",
    "file_id": "Drive file ID: the id field of Drive and search_workspace results, or the long ID in a Drive, Docs, Sheets or Slides URL.",
    "permission_id": "Drive permission ID, from drive_list_permissions or the response of drive_create_permission.",
    "drive_id": "Shared drive ID, from drive_list_drives.",
    "role": (
        "Access level: reader, commenter, writer, fileOrganizer or organizer (shared drives only), "
        "or owner (ownership transfer)."
    ),
    "allow_file_discovery": "For type 'domain' or 'anyone' only: whether people can find the file by searching instead of needing the link.",
    "expiration_time": "RFC3339 time at which the access expires, e.g. '2026-12-31T23:59:59Z'; must be in the future and at most a year ahead. Applies to user and group grants only.",
    "transfer_ownership": "Must be true when role is 'owner', confirming that ownership of the file moves to the grantee.",
    "task_id": "Task ID, from tasks_list_tasks, tasks_tasks_digest or tasks_create_task results.",
    "tasklist_id": "Task list ID from tasks_list_tasklists; '@default' selects the account's default list.",
    "document_id": "Google Docs document ID: the Drive file ID, also the long ID in the document's URL.",
    "spreadsheet_id": "Google Sheets spreadsheet ID: the Drive file ID, also the long ID in the spreadsheet's URL.",
    "presentation_id": "Google Slides presentation ID: the Drive file ID, also the long ID in the presentation's URL.",
    "page_object_id": "Slide object ID, from the objectId of an entry in slides_get_presentation's slides list.",
    "form_id": "Google Forms form ID: the Drive file ID, also the long ID in the form's edit URL (not the responder URL).",
    "response_id": "Form response ID, from the responses returned by forms_list_form_responses.",
    "contains_text": "Exact text to find; every occurrence is replaced.",
    "replace_text": "Text that replaces each occurrence; an empty string deletes the matches.",
    "value_input_option": "How values are interpreted: USER_ENTERED parses them as if typed into the UI (formulas, numbers, dates); RAW stores them exactly as given, so a string such as '=SUM(A1:A3)' stays text.",
    "value_render_option": "How cell values are returned: FORMATTED_VALUE as displayed, UNFORMATTED_VALUE as raw numbers, or FORMULA to return formulas instead of results.",
    "date_time_render_option": "How dates and times are returned when value_render_option is not FORMATTED_VALUE: SERIAL_NUMBER (days since 1899-12-30) or FORMATTED_STRING.",
    "major_dimension": "Whether values are returned as a list of ROWS or a list of COLUMNS.",
    "person_name": "Contact resource name, e.g. 'people/c123', from people_list_contacts or people_search_contacts; a bare ID is prefixed automatically.",
    "group_name": "Contact group resource name, e.g. 'contactGroups/myContacts' or 'contactGroups/abc123', from people_list_contact_groups; a bare ID is prefixed automatically.",
    "given_name": "Contact's given (first) name.",
    "family_name": "Contact's family (last) name.",
    "organization": "Company or organization name.",
    "biography": "Free-text notes about the contact.",
    "resource_names_to_add": "Contact resource names (people/...) to add to the group.",
    "resource_names_to_remove": "Contact resource names (people/...) to remove from the group.",
}


def shared_parameter_description(name: str) -> str | None:
    """Return the shared description for ``name``, if it has one."""
    return SHARED_PARAMETER_DESCRIPTIONS.get(name)
