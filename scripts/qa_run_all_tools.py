#!/usr/bin/env python3
"""
QA script: run all MCP tools, resources, and prompts for Google Workspace MCP.
Uses in-memory client (same process) so credentials from lifespan are available.
Run from project root: uv run python scripts/qa_run_all_tools.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Run from project root so credentials.json / token.json are found
os.chdir(PROJECT_ROOT)

from fastmcp import Client  # noqa: E402

# Import after cwd is set so auth can find credentials
from mcp_google_workspace.server import workspace_mcp  # noqa: E402

ENABLE_KEEP = os.getenv("ENABLE_KEEP", "").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_CHAT = os.getenv("ENABLE_CHAT", "").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_DRAFT_SEND = os.getenv("ENABLE_DRAFT_SEND", "").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_FORWARDING_CREATE_DELETE = os.getenv("ENABLE_FORWARDING_CREATE_DELETE", "").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_VACATION_UPDATE = os.getenv("ENABLE_VACATION_UPDATE", "").strip().lower() in {"1", "true", "yes", "on"}
QA_CALENDAR_ATTENDEE = os.getenv("QA_CALENDAR_ATTENDEE", "guilherme.inacio@franq.com.br")


def _state() -> dict:
    return {
        "message_ids": [],
        "one_message_id": None,
        "event_id": None,
        "space_name": None,
        "space_id_uri": None,
        "message_name": None,
        "note_name": None,
        "label_id": None,
        "existing_label_id": None,
        "filter_id": None,
        "draft_id": None,
        "thread_id": None,
        "history_id": None,
        "deleted_message_id": None,
        "vacation_settings": None,
        "forwarding_email": os.getenv("QA_FORWARDING_EMAIL", "qa-forwarding-test@example.com"),
        "search_message_ids": [],
        "drive_folder_id": None,
        "drive_file_id": None,
        "drive_copy_file_id": None,
        "drive_google_doc_id": None,
        "drive_permission_id": None,
        "drive_id": None,
        "drive_upload_path": "tmp/qa_drive_upload.txt",
        "drive_update_path": "tmp/qa_drive_update.txt",
        "drive_download_path": "tmp/qa_drive_download.txt",
        "drive_export_path": "tmp/qa_drive_export.pdf",
    }


def _now_iso(hours_offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_tools(client: Client, state: dict, results: list[tuple[str, bool, str]]) -> None:
    """Execute tools in dependency order; update state and results."""
    tools: list[tuple[str, dict | None]] = []
    upload_path = Path(state["drive_upload_path"])
    update_path = Path(state["drive_update_path"])
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text("QA Drive upload file\n", encoding="utf-8")
    update_path.write_text("QA Drive updated content\n", encoding="utf-8")

    # ---- Gmail (read-only first) ----
    tools.append(("gmail_list_labels", {}))
    tools.append(("gmail_list_emails", {"label_id": "INBOX", "max_results": 5}))
    tools.append(("gmail_search_emails", {"query": "unsubscribe", "max_results": 5}))

    # Gmail read one (after we have message_ids from list or search)
    # gmail_read_email: needs one_message_id (set after list_emails)
    # gmail_send_email: optional; use confirm_send=False
    # send_email omitted to avoid sending real email (would need recipients.to)
    tools.append(("gmail_read_email", None))  # filled from state
    tools.append(("gmail_create_label", {"name": "QA-test-label"}))
    tools.append(("gmail_list_filters", {}))
    tools.append(("gmail_create_filter", None))  # requires existing/created label id
    tools.append(("gmail_delete_filter", None))  # filter_id from state
    tools.append(("gmail_list_drafts", {"max_results": 10}))
    tools.append(("gmail_create_draft", None))  # creates then optionally sends/deletes
    tools.append(("gmail_get_draft", None))  # draft_id
    tools.append(("gmail_update_draft", None))  # draft_id
    if ENABLE_DRAFT_SEND:
        tools.append(("gmail_send_draft", None))  # draft_id
    tools.append(("gmail_delete_draft", None))  # draft_id
    tools.append(("gmail_list_threads", {"max_results": 10}))
    tools.append(("gmail_get_thread", None))  # thread_id
    tools.append(("gmail_modify_thread", None))  # thread_id + labels
    tools.append(("gmail_trash_thread", None))  # thread_id
    tools.append(("gmail_untrash_thread", None))  # thread_id
    tools.append(("gmail_list_history", None))  # start_history_id
    tools.append(("gmail_list_forwarding_addresses", {}))
    tools.append(("gmail_get_vacation_settings", {}))
    if ENABLE_VACATION_UPDATE:
        tools.append(("gmail_update_vacation_settings", None))
    if ENABLE_FORWARDING_CREATE_DELETE:
        tools.append(("gmail_create_forwarding_address", None))
        tools.append(("gmail_get_forwarding_address", None))
        tools.append(("gmail_delete_forwarding_address", None))
    tools.append(("gmail_mark_as_read", None))
    tools.append(("gmail_mark_as_unread", None))
    tools.append(("gmail_delete_email", None))  # use search_message_ids[0], permanent=False
    tools.append(("gmail_untrash_email", None))  # restore deleted message from trash
    tools.append(("gmail_mark_as_spam", None))
    tools.append(("gmail_mark_as_not_spam", None))
    tools.append(("gmail_move_email", None))  # message_id, add_label_ids/remove_label_ids
    tools.append(("gmail_list_attachments", None))  # message_id
    tools.append(("gmail_batch_modify", None))  # message_ids, add/remove
    tools.append(("gmail_batch_delete", None))  # message_ids, permanent=False
    tools.append(("gmail_apply_labels", None))  # message_id + label_id (before delete_label)
    tools.append(("gmail_update_label", None))  # label_id from state, name
    tools.append(("gmail_delete_label", None))  # label_id
    tools.append(("gmail_summarize_email", None))  # message_id (sampling - may need handler)

    # ---- Calendar ----
    tools.append(("calendar_list_calendars", {}))
    tools.append(("calendar_get_events", {"calendar_id": "primary", "max_results": 10}))
    tools.append(("calendar_get_event", None))  # event_id from state
    tools.append(("calendar_list_event_attachments", None))  # event_id from state
    tools.append(("calendar_get_timezone_info", {}))
    tools.append(("calendar_get_current_date", {}))
    # check_availability needs timeMin, timeMax, items
    tools.append(("calendar_check_availability", {
        "timeMin": _now_iso(0),
        "timeMax": _now_iso(24),
        "items": [{"id": "primary"}],
    }))
    tools.append(("calendar_create_event", {
        "summary": "QA test event",
        "start_datetime": _now_iso(1),
        "end_datetime": _now_iso(2),
        "attendees": [{"email": QA_CALENDAR_ATTENDEE}],
        "send_updates": "all",
    }))
    tools.append(("calendar_update_event", None))  # event_id, summary
    tools.append(("calendar_delete_event", None))  # event_id, force=True

    # ---- Drive ----
    tools.append(("drive_list_files", {"page_size": 10, "order_by": "modifiedTime desc"}))
    tools.append(("drive_list_drives", {"page_size": 10}))
    tools.append(("drive_create_folder", {"name": "QA Drive MCP Folder"}))
    tools.append(("drive_upload_file", None))  # local_path + parent from state
    tools.append(("drive_get_file", None))  # drive_file_id
    tools.append(("drive_update_file_metadata", None))  # drive_file_id
    tools.append(("drive_update_file_content", None))  # drive_file_id + local path
    tools.append(("drive_get_file_content_capabilities", None))  # drive_file_id
    tools.append(("drive_download_file", None))  # drive_file_id
    tools.append(("drive_copy_file", None))  # drive_file_id
    tools.append(("drive_create_file_metadata", {"name": "QA Drive Export Doc", "mime_type": "application/vnd.google-apps.document"}))
    tools.append(("drive_export_google_file", None))  # drive_google_doc_id
    tools.append(("drive_list_permissions", None))  # drive_file_id
    tools.append(("drive_create_permission", None))  # drive_file_id
    tools.append(("drive_get_permission", None))  # drive_file_id + permission_id
    tools.append(("drive_update_permission", None))  # drive_file_id + permission_id
    tools.append(("drive_delete_permission", None))  # drive_file_id + permission_id
    tools.append(("drive_delete_file", None))  # cleanup copied file
    tools.append(("drive_delete_file", None))  # cleanup exported Google Doc
    tools.append(("drive_delete_file", None))  # cleanup uploaded file
    tools.append(("drive_delete_file", None))  # cleanup folder

    # ---- Keep (optional) ----
    if ENABLE_KEEP:
        tools.append(("keep_list_notes", {"page_size": 5}))
        tools.append(("keep_create_note", {"title": "QA test note", "confirm_create": False}))
        tools.append(("keep_get_note", None))  # note_name from state
        tools.append(("keep_delete_note", None))  # note_name, confirm_delete=False
        tools.append(("keep_summarize_note", None))  # note name

    # ---- Chat (optional) ----
    if ENABLE_CHAT:
        tools.append(("chat_list_spaces", {"page_size": 10}))
        tools.append(("chat_get_space", None))  # space_name from state
        tools.append(("chat_list_messages", None))  # space_name
        tools.append(("chat_get_message", None))  # message_name
        tools.append(("chat_create_message", None))  # space_name, text, notify=False
        tools.append(("chat_update_message", None))  # message_name, text
        tools.append(("chat_delete_message", None))  # message_name, force=True

    for name, args in tools:
        if args is None:
            args = _tool_args(name, state)
        if args is None:
            results.append((f"tool:{name}", False, "skip (no args/state)"))
            continue
        # Tools with (request: PydanticModel, ctx) need args under "request"; others take flat args
        flat_tools = {
            "gmail_mark_as_read", "gmail_mark_as_unread", "gmail_summarize_email",
            "gmail_list_forwarding_addresses", "gmail_get_vacation_settings",
        }
        payload = args if (name in flat_tools or not args) else {"request": args}
        try:
            out = await client.call_tool(name, payload, raise_on_error=True)
            _update_state(name, args, out, state)
            results.append((f"tool:{name}", True, ""))
        except Exception as e:
            results.append((f"tool:{name}", False, str(e)))


def _tool_args(name: str, state: dict) -> dict | None:
    if name == "gmail_read_email" and state.get("one_message_id"):
        return {"message_id": state["one_message_id"]}
    if name == "gmail_mark_as_read" and state.get("one_message_id"):
        return {"message_id": state["one_message_id"]}
    if name == "gmail_mark_as_unread" and state.get("one_message_id"):
        return {"message_id": state["one_message_id"]}
    if name == "gmail_delete_email" and state.get("search_message_ids"):
        return {"message_id": state["search_message_ids"][0], "permanent": False}
    if name == "gmail_untrash_email" and state.get("deleted_message_id"):
        return {"message_id": state["deleted_message_id"]}
    if name == "gmail_mark_as_spam" and state.get("search_message_ids"):
        return {"message_id": state["search_message_ids"][0]}
    if name == "gmail_mark_as_not_spam" and state.get("search_message_ids"):
        return {"message_id": state["search_message_ids"][0], "add_to_inbox": True}
    if name == "gmail_move_email" and state.get("one_message_id"):
        label_id = state.get("label_id") or state.get("existing_label_id")
        if label_id:
            return {"message_id": state["one_message_id"], "add_label_ids": [label_id], "remove_label_ids": []}
        return None
    if name == "gmail_list_attachments" and state.get("one_message_id"):
        return {"message_id": state["one_message_id"]}
    if name == "gmail_batch_modify" and len(state.get("message_ids", [])) >= 2:
        label_id = state.get("label_id") or state.get("existing_label_id")
        if label_id:
            return {"message_ids": state["message_ids"][:2], "add_label_ids": [label_id], "remove_label_ids": []}
        return None
    if name == "gmail_batch_delete" and state.get("search_message_ids"):
        ids = state["search_message_ids"][:1]
        return {"message_ids": ids, "permanent": False}
    if name == "gmail_update_label" and state.get("label_id"):
        return {"label_id": state["label_id"], "name": "QA-test-label-updated"}
    if name == "gmail_delete_label" and state.get("label_id"):
        return {"label_id": state["label_id"]}
    if name == "gmail_apply_labels" and state.get("one_message_id") and state.get("label_id"):
        return {"message_id": state["one_message_id"], "add_label_ids": [state["label_id"]], "remove_label_ids": []}
    if name == "gmail_summarize_email" and state.get("one_message_id"):
        return {"message_id": state["one_message_id"]}
    if name == "gmail_create_filter":
        label_id = state.get("label_id") or state.get("existing_label_id")
        if label_id:
            return {
                "criteria": {"subject": "[QA] Gmail MCP send test"},
                "action": {"add_label_ids": [label_id]},
            }
        return None
    if name == "gmail_delete_filter" and state.get("filter_id"):
        return {"filter_id": state["filter_id"]}
    if name == "gmail_create_draft":
        return {
            "recipients": {"to": ["gsilvainacio@gmail.com"]},
            "subject": "[QA] Draft create test",
            "text_body": "Draft body created by QA script.",
        }
    if name == "gmail_get_draft" and state.get("draft_id"):
        return {"draft_id": state["draft_id"], "format": "full"}
    if name == "gmail_update_draft" and state.get("draft_id"):
        return {
            "draft_id": state["draft_id"],
            "recipients": {"to": ["gsilvainacio@gmail.com"]},
            "subject": "[QA] Draft update test",
            "text_body": "Updated draft body from QA script.",
        }
    if name == "gmail_send_draft" and state.get("draft_id"):
        return {"draft_id": state["draft_id"]}
    if name == "gmail_delete_draft" and state.get("draft_id"):
        return {"draft_id": state["draft_id"]}
    if name == "gmail_get_thread" and state.get("thread_id"):
        return {"thread_id": state["thread_id"], "format": "minimal"}
    if name == "gmail_modify_thread" and state.get("thread_id"):
        label_id = state.get("label_id") or state.get("existing_label_id")
        if label_id:
            return {"thread_id": state["thread_id"], "add_label_ids": [label_id], "remove_label_ids": []}
        return None
    if name == "gmail_trash_thread" and state.get("thread_id"):
        return {"thread_id": state["thread_id"]}
    if name == "gmail_untrash_thread" and state.get("thread_id"):
        return {"thread_id": state["thread_id"]}
    if name == "gmail_list_history" and state.get("history_id"):
        return {"start_history_id": state["history_id"], "max_results": 10}
    if name == "gmail_update_vacation_settings":
        vac = state.get("vacation_settings") or {}
        return {
            "enable_auto_reply": bool(vac.get("enableAutoReply", False)),
            "response_subject": vac.get("responseSubject"),
            "response_body_plain_text": vac.get("responseBodyPlainText"),
            "response_body_html": vac.get("responseBodyHtml"),
            "restrict_to_contacts": bool(vac.get("restrictToContacts", False)),
            "restrict_to_domain": bool(vac.get("restrictToDomain", False)),
            "start_time": vac.get("startTime"),
            "end_time": vac.get("endTime"),
        }
    if name == "gmail_create_forwarding_address" and state.get("forwarding_email"):
        return {"forwarding_email": state["forwarding_email"]}
    if name == "gmail_get_forwarding_address" and state.get("forwarding_email"):
        return {"forwarding_email": state["forwarding_email"]}
    if name == "gmail_delete_forwarding_address" and state.get("forwarding_email"):
        return {"forwarding_email": state["forwarding_email"]}
    if name == "calendar_update_event" and state.get("event_id"):
        return {"event_id": state["event_id"], "summary": "QA test event updated"}
    if name == "calendar_get_event" and state.get("event_id"):
        return {"event_id": state["event_id"]}
    if name == "calendar_list_event_attachments" and state.get("event_id"):
        return {"event_id": state["event_id"]}
    if name == "calendar_delete_event" and state.get("event_id"):
        return {"event_id": state["event_id"], "force": True}
    if name == "drive_upload_file":
        args: dict = {
            "local_path": state["drive_upload_path"],
            "name": "qa-drive-upload.txt",
        }
        if state.get("drive_folder_id"):
            args["parent_ids"] = [state["drive_folder_id"]]
        return args
    if name == "drive_get_file" and state.get("drive_file_id"):
        return {"file_id": state["drive_file_id"]}
    if name == "drive_update_file_metadata" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "name": "qa-drive-upload-updated.txt",
            "description": "Updated by QA script",
            "properties": {"qa": "true"},
        }
    if name == "drive_update_file_content" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "local_path": state["drive_update_path"],
        }
    if name == "drive_get_file_content_capabilities" and state.get("drive_file_id"):
        return {"file_id": state["drive_file_id"]}
    if name == "drive_download_file" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "output_path": state["drive_download_path"],
            "overwrite": True,
        }
    if name == "drive_copy_file" and state.get("drive_file_id"):
        args = {
            "file_id": state["drive_file_id"],
            "name": "qa-drive-copy.txt",
        }
        if state.get("drive_folder_id"):
            args["parent_ids"] = [state["drive_folder_id"]]
        return args
    if name == "drive_export_google_file" and state.get("drive_google_doc_id"):
        return {
            "file_id": state["drive_google_doc_id"],
            "mime_type": "application/pdf",
            "output_path": state["drive_export_path"],
            "overwrite": True,
        }
    if name == "drive_list_permissions" and state.get("drive_file_id"):
        return {"file_id": state["drive_file_id"], "page_size": 50}
    if name == "drive_create_permission" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "type": "anyone",
            "role": "reader",
            "allow_file_discovery": False,
        }
    if name == "drive_get_permission" and state.get("drive_file_id") and state.get("drive_permission_id"):
        return {
            "file_id": state["drive_file_id"],
            "permission_id": state["drive_permission_id"],
        }
    if name == "drive_update_permission" and state.get("drive_file_id") and state.get("drive_permission_id"):
        return {
            "file_id": state["drive_file_id"],
            "permission_id": state["drive_permission_id"],
            "role": "commenter",
        }
    if name == "drive_delete_permission" and state.get("drive_file_id") and state.get("drive_permission_id"):
        return {
            "file_id": state["drive_file_id"],
            "permission_id": state["drive_permission_id"],
        }
    if name == "drive_get_drive" and state.get("drive_id"):
        return {"drive_id": state["drive_id"]}
    if name == "drive_delete_file":
        target = (
            state.get("drive_copy_file_id")
            or state.get("drive_google_doc_id")
            or state.get("drive_file_id")
            or state.get("drive_folder_id")
        )
        if target:
            return {"file_id": target}
        return None
    if name == "keep_get_note" and state.get("note_name"):
        return {"note_name": state["note_name"]}
    if name == "keep_delete_note" and state.get("note_name"):
        return {"note_name": state["note_name"], "confirm_delete": False}
    if name == "keep_summarize_note" and state.get("note_name"):
        return {"note_name": state["note_name"]}
    if name == "chat_get_space" and state.get("space_name"):
        return {"space_name": state["space_name"]}
    if name == "chat_list_messages" and state.get("space_name"):
        return {"space_name": state["space_name"], "page_size": 5}
    if name == "chat_get_message" and state.get("message_name"):
        return {"message_name": state["message_name"]}
    if name == "chat_create_message" and state.get("space_name"):
        return {"space_name": state["space_name"], "text": "QA test message", "notify": False}
    if name == "chat_update_message" and state.get("message_name"):
        return {"message_name": state["message_name"], "text": "QA test message updated"}
    if name == "chat_delete_message" and state.get("message_name"):
        return {"message_name": state["message_name"], "force": True}
    return None


def _get_data(out: object) -> dict | None:
    data = getattr(out, "data", out)
    return data if isinstance(data, dict) else None


def _update_state(name: str, args: dict, out: object, state: dict) -> None:
    try:
        data = _get_data(out)
        if not data:
            return
        if name == "gmail_list_emails" and "messages" in data and data["messages"]:
            state["message_ids"] = [m["id"] for m in data["messages"]]
            state["one_message_id"] = state["message_ids"][0]
            # Reuse first known thread id for thread tool coverage.
            first = data["messages"][0]
            if isinstance(first, dict):
                state["thread_id"] = first.get("threadId") or state["thread_id"]
        if name == "gmail_list_labels" and "labels" in data:
            labels = data.get("labels") or []
            existing_user_labels = [
                lb for lb in labels
                if isinstance(lb, dict) and lb.get("type") == "user" and lb.get("id")
            ]
            if existing_user_labels:
                state["existing_label_id"] = existing_user_labels[0]["id"]
        if name == "gmail_search_emails" and "messages" in data:
            state["search_message_ids"] = [m["id"] for m in data["messages"]]
        if name == "gmail_read_email":
            state["thread_id"] = data.get("thread_id") or state.get("thread_id")
            state["history_id"] = data.get("history_id") or state.get("history_id")
        if name == "gmail_create_label" and "label" in data:
            lab = data["label"]
            state["label_id"] = lab.get("id") if isinstance(lab, dict) else getattr(lab, "id", None)
        if name == "gmail_create_filter" and "filter" in data:
            flt = data["filter"]
            state["filter_id"] = flt.get("id") if isinstance(flt, dict) else getattr(flt, "id", None)
        if name == "gmail_create_draft" and "draft" in data:
            dr = data["draft"]
            state["draft_id"] = dr.get("id") if isinstance(dr, dict) else getattr(dr, "id", None)
        if name == "gmail_delete_email" and args.get("message_id"):
            state["deleted_message_id"] = args["message_id"]
        if name == "gmail_get_vacation_settings" and "vacation" in data:
            state["vacation_settings"] = data["vacation"]
        if name == "calendar_create_event" and "event" in data:
            ev = data["event"]
            state["event_id"] = ev.get("id") if isinstance(ev, dict) else getattr(ev, "id", None)
        if name == "drive_list_drives" and "drives" in data and data["drives"]:
            first = data["drives"][0]
            if isinstance(first, dict):
                state["drive_id"] = first.get("id")
        if name == "drive_create_folder" and "file" in data:
            item = data["file"]
            state["drive_folder_id"] = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        if name == "drive_upload_file" and "file" in data:
            item = data["file"]
            state["drive_file_id"] = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        if name == "drive_create_file_metadata" and "file" in data:
            item = data["file"]
            created_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            mime = args.get("mime_type")
            if mime == "application/vnd.google-apps.document":
                state["drive_google_doc_id"] = created_id
            elif created_id and not state.get("drive_file_id"):
                state["drive_file_id"] = created_id
        if name == "drive_copy_file" and "file" in data:
            item = data["file"]
            state["drive_copy_file_id"] = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        if name == "drive_create_permission" and "permission" in data:
            item = data["permission"]
            state["drive_permission_id"] = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        if name == "drive_delete_permission" and args.get("permission_id"):
            state["drive_permission_id"] = None
        if name == "drive_delete_file" and args.get("file_id"):
            deleted_id = args["file_id"]
            if state.get("drive_copy_file_id") == deleted_id:
                state["drive_copy_file_id"] = None
            if state.get("drive_google_doc_id") == deleted_id:
                state["drive_google_doc_id"] = None
            if state.get("drive_file_id") == deleted_id:
                state["drive_file_id"] = None
            if state.get("drive_folder_id") == deleted_id:
                state["drive_folder_id"] = None
        if name == "keep_create_note" and "note" in data:
            note = data["note"]
            state["note_name"] = note.get("name") if isinstance(note, dict) else getattr(note, "name", None)
        if name == "chat_list_spaces" and "spaces" in data and data["spaces"]:
            first = data["spaces"][0]
            name_val = first.get("name") if isinstance(first, dict) else getattr(first, "name", None)
            state["space_name"] = name_val
            state["space_id_uri"] = (name_val or "").replace("spaces/", "") or name_val
        if name == "chat_create_message" and "message" in data:
            msg = data["message"]
            state["message_name"] = msg.get("name") if isinstance(msg, dict) else getattr(msg, "name", None)
    except Exception:
        pass


async def run_resources(client: Client, state: dict, results: list[tuple[str, bool, str]]) -> None:
    # Mounted resources are namespaced: scheme stays, path prefixed with namespace (e.g. gmail://gmail/inbox/summary)
    uris = [
        "gmail://gmail/inbox/summary",
        "gmail://gmail/labels",
    ]
    if state.get("one_message_id"):
        uris.append(f"gmail://gmail/email/{state['one_message_id']}")
    uris.extend(["calendar://calendar/today", "calendar://calendar/week"])
    uris.extend(["drive://drive/recent", "drive://drive/shared-drives"])
    if state.get("drive_file_id"):
        uris.append(f"drive://drive/file/{state['drive_file_id']}")
    if ENABLE_KEEP:
        uris.append("keep://keep/notes/recent")
        if state.get("note_name"):
            nid = state["note_name"].split("/")[-1] if "/" in state.get("note_name", "") else state.get("note_name")
            uris.append(f"keep://keep/note/{nid}")
    if ENABLE_CHAT:
        uris.extend(["chat://chat/spaces"])
        if state.get("space_id_uri"):
            uris.append(f"chat://chat/space/{state['space_id_uri']}/messages")
            uris.append(f"chat://chat/space/{state['space_id_uri']}/members")
        uris.append("chat://chat/users/me")

    for uri in uris:
        try:
            _ = await client.read_resource(uri)
            results.append((f"resource:{uri}", True, ""))
        except Exception as e:
            results.append((f"resource:{uri}", False, str(e)))


async def run_prompts(client: Client, results: list[tuple[str, bool, str]]) -> None:
    prompts: list[tuple[str, dict]] = [
        ("gmail_compose_email_prompt", {"topic": "meeting", "tone": "professional"}),
        ("gmail_reply_email_prompt", {"original_email": "Hi", "intent": "accept"}),
        ("gmail_summarize_inbox_prompt", {"count": 5}),
    ]
    if ENABLE_CHAT:
        prompts.extend([
            ("chat_draft_chat_announcement_prompt", {"topic": "Release", "audience": "team"}),
            ("chat_summarize_chat_thread_prompt", {"thread_messages": "A: Hi\nB: Bye"}),
        ])
    if ENABLE_KEEP:
        prompts.extend([
            ("keep_summarize_keep_note_prompt", {"note_text": "Buy milk"}),
            ("keep_extract_actions_from_keep_notes_prompt", {"notes_blob": "Note 1"}),
        ])
    for name, args in prompts:
        try:
            _ = await client.get_prompt(name, args)
            results.append((f"prompt:{name}", True, ""))
        except Exception as e:
            results.append((f"prompt:{name}", False, str(e)))


async def main() -> None:
    results: list[tuple[str, bool, str]] = []
    state = _state()
    client = Client(workspace_mcp)

    async with client:
        await run_tools(client, state, results)
        await run_resources(client, state, results)
        await run_prompts(client, results)

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n--- QA results ---")
    for item, ok, err in results:
        status = "PASS" if ok else "FAIL"
        extra = f" | {err[:80]}" if err else ""
        print(f"  {status}  {item}{extra}")
    print(f"\nTotal: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
