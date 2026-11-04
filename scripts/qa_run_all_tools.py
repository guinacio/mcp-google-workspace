#!/usr/bin/env python3
"""
QA script: run all MCP tools, resources, and prompts for Google Workspace MCP.
Uses in-memory client (same process) so credentials from lifespan are available.
Run from project root: uv run python scripts/qa_run_all_tools.py

The Gmail settings tools (gmail_manage_filters, gmail_manage_forwarding,
gmail_manage_vacation) and drive_update_drive are extended-tier tools, so the
script sets MCP_TOOL_TIER=extended before the server is imported unless the
environment already chooses a tier ("complete" also includes them).

Confirmation questions (deleting the QA label, filter, draft, permission, ...)
are answered "yes" by the script's elicitation handler; every question it
accepts is printed. It only deletes what it created, except where noted
(the "unsubscribe" search hit is moved to Trash, as before).

Optional environment variables:
  ENABLE_KEEP, ENABLE_CHAT         also run the Keep / Chat tools
  ENABLE_DRAFT_SEND                send the QA draft (needs QA_DRAFT_TO)
  ENABLE_FORWARDING_CREATE_DELETE  create, read and delete QA_FORWARDING_EMAIL
  ENABLE_VACATION_UPDATE           re-save the current vacation settings
  ENABLE_DRIVE_HIDE_UNHIDE         hide then unhide the first Shared Drive
  QA_DRAFT_TO                      recipient of the QA draft (none by default)
  QA_CALENDAR_ATTENDEE             attendee invited to the QA event (none by default)
  QA_FORWARDING_EMAIL              forwarding address for the forwarding steps
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Run from project root so credentials.json / token.json are found
os.chdir(PROJECT_ROOT)

# The tier is applied when the server module is imported.
os.environ.setdefault("MCP_TOOL_TIER", "extended")

from fastmcp import Client  # noqa: E402

# Import after cwd is set so auth can find credentials
from mcp_google_workspace.server import workspace_mcp  # noqa: E402


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


ENABLE_KEEP = _flag("ENABLE_KEEP")
ENABLE_CHAT = _flag("ENABLE_CHAT")
ENABLE_DRAFT_SEND = _flag("ENABLE_DRAFT_SEND")
ENABLE_FORWARDING_CREATE_DELETE = _flag("ENABLE_FORWARDING_CREATE_DELETE")
ENABLE_VACATION_UPDATE = _flag("ENABLE_VACATION_UPDATE")
ENABLE_DRIVE_HIDE_UNHIDE = _flag("ENABLE_DRIVE_HIDE_UNHIDE")
QA_DRAFT_TO = os.getenv("QA_DRAFT_TO", "").strip()
QA_CALENDAR_ATTENDEE = os.getenv("QA_CALENDAR_ATTENDEE", "").strip()
QA_LABEL_NAME = "QA-test-label"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


def _state() -> dict:
    return {
        "message_ids": [],
        "one_message_id": None,
        "event_id": None,
        "read_event_id": None,
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
        "attachment_message_id": None,
        "attachment_download_id": None,
        "vacation_settings": None,
        "forwarding_email": os.getenv("QA_FORWARDING_EMAIL", "qa-forwarding-test@example.com"),
        "search_message_ids": [],
        "drive_folder_id": None,
        "drive_file_id": None,
        "drive_copy_file_id": None,
        "drive_google_doc_id": None,
        "drive_permission_id": None,
        "drive_commit_token": None,
        "drive_id": None,
        "drive_upload_path": "tmp/qa_drive_upload.txt",
        "drive_update_path": "tmp/qa_drive_update.txt",
        "drive_download_path": "tmp/qa_drive_download.txt",
        "drive_export_path": "tmp/qa_drive_export.pdf",
        "gmail_attachment_path": "tmp/qa_gmail_attachment.bin",
    }


def _now_iso(hours_offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _accept_confirmation(message: str, _response_type: Any, params: Any, _context: Any) -> Any:
    """Answer the server's confirmation questions with yes (QA cleanup of its own resources)."""
    print(f"  confirm: {message}")
    return {name: True for name in params.requested_schema.get("properties", {})}


def _tool_name(step: str) -> str:
    """A plan step is 'tool_name' or 'tool_name:variant' when one tool runs several times."""
    return step.split(":", 1)[0]


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
    tools.append(("gmail_search_emails:inbox", {"labels": ["INBOX"], "max_results": 5}))
    tools.append(("gmail_search_emails:unsubscribe", {"query": "unsubscribe", "max_results": 5}))

    # Gmail read one or more (after we have message_ids from search)
    # gmail_send_email: optional; use confirm_send=False
    # send_email omitted to avoid sending real email (would need recipients.to)
    tools.append(("gmail_read_emails", None))  # filled from state
    tools.append(("gmail_manage_labels:create", {"action": "create", "name": QA_LABEL_NAME}))
    tools.append(("gmail_manage_filters:list", {"action": "list"}))
    tools.append(("gmail_manage_filters:create", None))  # requires existing/created label
    tools.append(("gmail_manage_filters:delete", None))  # filter_id from state
    tools.append(("gmail_list_drafts", {"max_results": 10}))
    tools.append(("gmail_save_draft:create", None))  # creates, then read/rewrite/delete
    tools.append(("gmail_list_drafts:one", None))  # draft_id
    tools.append(("gmail_save_draft:update", None))  # draft_id
    if ENABLE_DRAFT_SEND:
        tools.append(("gmail_send_draft", None))  # draft_id
    tools.append(("gmail_delete_draft", None))  # draft_id
    tools.append(("gmail_search_emails:threads", {"group_by_thread": True, "max_results": 10}))
    tools.append(("gmail_get_thread", None))  # thread_id
    tools.append(("gmail_modify_messages:thread_labels", None))  # thread_id + labels
    tools.append(("gmail_delete:thread_trash", None))  # thread_id, mode=trash
    tools.append(("gmail_modify_messages:thread_untrash", None))  # thread_id, untrash=True
    tools.append(("gmail_manage_forwarding:list", {"action": "list"}))
    tools.append(("gmail_manage_vacation:get", {"action": "get"}))
    if ENABLE_VACATION_UPDATE:
        tools.append(("gmail_manage_vacation:update", None))
    if ENABLE_FORWARDING_CREATE_DELETE:
        tools.append(("gmail_manage_forwarding:create", None))
        tools.append(("gmail_manage_forwarding:get", None))
        tools.append(("gmail_manage_forwarding:delete", None))
    tools.append(("gmail_modify_messages:mark_read", None))
    tools.append(("gmail_modify_messages:mark_unread", None))
    tools.append(("gmail_delete:message_trash", None))  # search_message_ids[0], mode=trash
    tools.append(("gmail_modify_messages:untrash", None))  # restore deleted message from trash
    tools.append(("gmail_modify_messages:spam", None))
    tools.append(("gmail_modify_messages:not_spam", None))
    tools.append(("gmail_modify_messages:add_label", None))  # one message, label by ID
    tools.append(("gmail_search_emails:has_attachment", {"has_attachment": True, "max_results": 1}))
    tools.append(("gmail_read_emails:attachment", None))  # attachments carry download_id
    tools.append(("gmail_download_attachment", None))  # message_id + download_id
    tools.append(("gmail_modify_messages:batch_labels", None))  # 2 message_ids, add label
    tools.append(("gmail_delete:batch_trash", None))  # message_ids, mode=trash
    tools.append(("gmail_modify_messages:label_by_name", None))  # label by name (before label delete)
    tools.append(("gmail_manage_labels:update", None))  # label_id from state, new name
    tools.append(("gmail_manage_labels:delete", None))  # label_id

    # ---- Calendar ----
    tools.append(("calendar_get_calendar_context:calendars", {"include_calendars": True}))
    tools.append(("calendar_search_events", {"calendar_id": "primary", "max_results": 10}))
    tools.append(("calendar_read_events", None))  # event id from search_events
    tools.append(("calendar_get_calendar_context", {"include_calendars": False}))
    tools.append(("calendar_find_free_time", {
        "participants": ["primary"],
        "time_min": _now_iso(0),
        "time_max": _now_iso(24),
        "slot_duration_minutes": 30,
        "max_results": 5,
    }))
    event: dict[str, Any] = {
        "summary": "QA test event",
        "start_datetime": _now_iso(1),
        "end_datetime": _now_iso(2),
        "send_updates": "all",
    }
    if QA_CALENDAR_ATTENDEE:
        event["attendees"] = [{"email": QA_CALENDAR_ATTENDEE}]
    tools.append(("calendar_create_event", event))
    tools.append(("calendar_read_events:created", None))  # event_id from create
    tools.append(("calendar_update_event", None))  # event_id, summary
    tools.append(("calendar_delete_event", None))  # event_id, force=True

    # ---- Drive ----
    tools.append(("drive_list_files", {"page_size": 10, "order_by": "modifiedTime desc"}))
    tools.append(("drive_list_drives", {"page_size": 10}))
    tools.append(("drive_list_drives:one", None))  # drive_id from list
    if ENABLE_DRIVE_HIDE_UNHIDE:
        tools.append(("drive_update_drive:hide", None))  # drive_id, hidden=True
        tools.append(("drive_update_drive:unhide", None))  # drive_id, hidden=False
    tools.append(("drive_create_folder", {"name": "QA Drive MCP Folder"}))
    tools.append(("drive_upload_file", None))  # local_path + parent from state
    tools.append(("drive_get_file", None))  # drive_file_id
    tools.append(("drive_update_file_metadata", None))  # drive_file_id
    tools.append(("drive_upload_file:replace", None))  # file_id + local path: new content
    tools.append(("drive_download_file", None))  # drive_file_id
    tools.append(("drive_copy_file", None))  # drive_file_id
    tools.append(("drive_upload_file:google_doc", {"name": "QA Drive Export Doc", "mime_type": GOOGLE_DOC_MIME}))
    tools.append(("drive_get_file:google_doc", None))  # is_google_native / export_formats
    tools.append(("drive_download_file:export", None))  # drive_google_doc_id, export_mime_type
    tools.append(("drive_list_permissions", None))  # drive_file_id
    # A link ('anyone') share needs prepare/commit before drive_share_file runs.
    tools.append(("prepare_workspace_action:drive_share_file", None))  # drive_file_id
    tools.append(("commit_workspace_action:drive_share_file", None))  # commit token
    tools.append(("drive_list_permissions:one", None))  # drive_file_id + permission_id
    tools.append(("drive_share_file:update", None))  # drive_file_id + permission_id
    tools.append(("drive_unshare_file", None))  # drive_file_id + permission_id
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

    # ---- Chat (optional) ----
    if ENABLE_CHAT:
        tools.append(("chat_list_spaces", {"page_size": 10}))
        tools.append(("chat_get_space", None))  # space_name from state
        tools.append(("chat_list_messages", None))  # space_name
        tools.append(("chat_get_message", None))  # message_name
        tools.append(("chat_create_message", None))  # space_name, text, notify=False
        tools.append(("chat_update_message", None))  # message_name, text
        tools.append(("chat_delete_message", None))  # message_name, force=True

    # Tools whose only parameter is a `request` model (Keep, Chat) take their
    # arguments under "request"; every other tool takes flat arguments.
    catalog = await client.list_tools()
    request_tools = {
        tool.name for tool in catalog
        if set((tool.input_schema or {}).get("properties", {})) == {"request"}
    }
    listed = {tool.name for tool in catalog}

    for step, args in tools:
        name = _tool_name(step)
        if args is None:
            args = _tool_args(step, state)
        if args is None:
            results.append((f"tool:{step}", False, "skip (no args/state)"))
            continue
        payload = {"request": args} if (name in request_tools and args) else args
        try:
            out = await client.call_tool(name, payload, raise_on_error=True)
            _update_state(step, args, out, state)
            results.append((f"tool:{step}", True, ""))
        except Exception as e:
            hint = "" if name in listed else " (not listed: check MCP_TOOL_TIER / ENABLE_* flags)"
            results.append((f"tool:{step}", False, f"{e}{hint}"))


def _label(state: dict) -> str | None:
    return state.get("label_id") or state.get("existing_label_id")


def _tool_args(step: str, state: dict) -> dict | None:
    if step == "gmail_read_emails" and state.get("one_message_id"):
        return {"message_ids": [state["one_message_id"]], "format": "clean"}
    if step == "gmail_read_emails:attachment" and state.get("attachment_message_id"):
        return {"message_ids": [state["attachment_message_id"]], "format": "metadata"}
    if step == "gmail_download_attachment" and state.get("attachment_download_id"):
        return {
            "message_id": state["attachment_message_id"],
            "attachment_id": state["attachment_download_id"],
            "output_path": state["gmail_attachment_path"],
        }
    if step == "gmail_modify_messages:mark_read" and state.get("one_message_id"):
        return {"message_ids": [state["one_message_id"]], "mark_read": True}
    if step == "gmail_modify_messages:mark_unread" and state.get("one_message_id"):
        return {"message_ids": [state["one_message_id"]], "mark_read": False}
    if step == "gmail_delete:message_trash" and state.get("search_message_ids"):
        return {"message_ids": [state["search_message_ids"][0]], "mode": "trash"}
    if step == "gmail_modify_messages:untrash" and state.get("deleted_message_id"):
        return {"message_ids": [state["deleted_message_id"]], "untrash": True}
    if step == "gmail_modify_messages:spam" and state.get("search_message_ids"):
        return {"message_ids": [state["search_message_ids"][0]], "spam": True}
    if step == "gmail_modify_messages:not_spam" and state.get("search_message_ids"):
        # spam=False removes SPAM and puts the message back in the inbox.
        return {"message_ids": [state["search_message_ids"][0]], "spam": False}
    if step == "gmail_modify_messages:add_label" and state.get("one_message_id"):
        label_id = _label(state)
        if label_id:
            return {"message_ids": [state["one_message_id"]], "add_labels": [label_id]}
        return None
    if step == "gmail_modify_messages:batch_labels" and len(state.get("message_ids", [])) >= 2:
        label_id = _label(state)
        if label_id:
            return {"message_ids": state["message_ids"][:2], "add_labels": [label_id]}
        return None
    if step == "gmail_delete:batch_trash" and state.get("search_message_ids"):
        ids = state["search_message_ids"][:1]
        return {"message_ids": ids, "mode": "trash"}
    if step == "gmail_modify_messages:label_by_name" and state.get("one_message_id") and state.get("label_id"):
        return {"message_ids": [state["one_message_id"]], "add_labels": [QA_LABEL_NAME]}
    if step == "gmail_manage_labels:update" and state.get("label_id"):
        return {"action": "update", "label": state["label_id"], "name": "QA-test-label-updated"}
    if step == "gmail_manage_labels:delete" and state.get("label_id"):
        return {"action": "delete", "label": state["label_id"]}
    if step == "gmail_manage_filters:create":
        label_id = _label(state)
        if label_id:
            return {
                "action": "create",
                "criteria": {"subject": "[QA] Gmail MCP send test"},
                "filter_action": {"add_labels": [label_id]},
            }
        return None
    if step == "gmail_manage_filters:delete" and state.get("filter_id"):
        return {"action": "delete", "filter_id": state["filter_id"]}
    if step == "gmail_save_draft:create":
        args: dict[str, Any] = {
            "subject": "[QA] Draft create test",
            "text_body": "Draft body created by QA script.",
        }
        if QA_DRAFT_TO:
            args["to"] = [QA_DRAFT_TO]
        return args
    if step == "gmail_list_drafts:one" and state.get("draft_id"):
        return {"draft_id": state["draft_id"], "format": "full"}
    if step == "gmail_save_draft:update" and state.get("draft_id"):
        args = {
            "draft_id": state["draft_id"],
            "subject": "[QA] Draft update test",
            "text_body": "Updated draft body from QA script.",
        }
        if QA_DRAFT_TO:
            args["to"] = [QA_DRAFT_TO]
        return args
    if step == "gmail_send_draft" and state.get("draft_id") and QA_DRAFT_TO:
        return {"draft_id": state["draft_id"]}
    if step == "gmail_delete_draft" and state.get("draft_id"):
        return {"draft_id": state["draft_id"]}
    if step == "gmail_get_thread" and state.get("thread_id"):
        return {"thread_id": state["thread_id"], "format": "minimal"}
    if step == "gmail_modify_messages:thread_labels" and state.get("thread_id"):
        label_id = _label(state)
        if label_id:
            return {"thread_id": state["thread_id"], "add_labels": [label_id]}
        return None
    if step == "gmail_delete:thread_trash" and state.get("thread_id"):
        return {"thread_id": state["thread_id"], "mode": "trash"}
    if step == "gmail_modify_messages:thread_untrash" and state.get("thread_id"):
        return {"thread_id": state["thread_id"], "untrash": True}
    if step == "gmail_manage_vacation:update":
        # Re-save the current settings unchanged (read by gmail_manage_vacation:get).
        vac = state.get("vacation_settings") or {}
        args = {
            "action": "update",
            "enable_auto_reply": bool(vac.get("enableAutoReply", False)),
            "response_subject": vac.get("responseSubject"),
            "response_body_plain_text": vac.get("responseBodyPlainText"),
            "response_body_html": vac.get("responseBodyHtml"),
            "restrict_to_contacts": bool(vac.get("restrictToContacts", False)),
            "restrict_to_domain": bool(vac.get("restrictToDomain", False)),
            # Gmail reports the epoch-millisecond bounds as strings.
            "start_time": int(vac["startTime"]) if vac.get("startTime") else None,
            "end_time": int(vac["endTime"]) if vac.get("endTime") else None,
        }
        return {key: value for key, value in args.items() if value is not None}
    if step == "gmail_manage_forwarding:create" and state.get("forwarding_email"):
        return {"action": "create", "forwarding_email": state["forwarding_email"]}
    if step == "gmail_manage_forwarding:get" and state.get("forwarding_email"):
        return {"action": "get", "forwarding_email": state["forwarding_email"]}
    if step == "gmail_manage_forwarding:delete" and state.get("forwarding_email"):
        return {"action": "delete", "forwarding_email": state["forwarding_email"]}
    if step == "calendar_update_event" and state.get("event_id"):
        return {"event_id": state["event_id"], "summary": "QA test event updated"}
    if step == "calendar_read_events" and state.get("read_event_id"):
        return {"event_ids": [state["read_event_id"]]}
    if step == "calendar_read_events:created" and state.get("event_id"):
        return {"event_ids": [state["event_id"]]}
    if step == "calendar_delete_event" and state.get("event_id"):
        return {"event_id": state["event_id"], "force": True}
    if step == "drive_upload_file":
        args = {
            "local_path": state["drive_upload_path"],
            "name": "qa-drive-upload.txt",
        }
        if state.get("drive_folder_id"):
            args["parent_ids"] = [state["drive_folder_id"]]
        return args
    if step == "drive_get_file" and state.get("drive_file_id"):
        return {"file_id": state["drive_file_id"]}
    if step == "drive_update_file_metadata" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "name": "qa-drive-upload-updated.txt",
            "description": "Updated by QA script",
            "properties": {"qa": "true"},
        }
    if step == "drive_upload_file:replace" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "local_path": state["drive_update_path"],
        }
    if step == "drive_get_file:google_doc" and state.get("drive_google_doc_id"):
        return {"file_id": state["drive_google_doc_id"]}
    if step == "drive_download_file" and state.get("drive_file_id"):
        return {
            "file_id": state["drive_file_id"],
            "output_path": state["drive_download_path"],
            "overwrite": True,
        }
    if step == "drive_copy_file" and state.get("drive_file_id"):
        args = {
            "file_id": state["drive_file_id"],
            "name": "qa-drive-copy.txt",
        }
        if state.get("drive_folder_id"):
            args["parent_ids"] = [state["drive_folder_id"]]
        return args
    if step == "drive_download_file:export" and state.get("drive_google_doc_id"):
        return {
            "file_id": state["drive_google_doc_id"],
            "export_mime_type": "application/pdf",
            "output_path": state["drive_export_path"],
            "overwrite": True,
        }
    if step == "drive_list_permissions" and state.get("drive_file_id"):
        return {"file_id": state["drive_file_id"], "page_size": 50}
    if step == "prepare_workspace_action:drive_share_file" and state.get("drive_file_id"):
        return {
            "tool_name": "drive_share_file",
            "arguments": {
                "file_id": state["drive_file_id"],
                "type": "anyone",
                "role": "reader",
                "allow_file_discovery": False,
            },
        }
    if step == "commit_workspace_action:drive_share_file" and state.get("drive_commit_token"):
        return {"commit_token": state["drive_commit_token"]}
    if step == "drive_list_permissions:one" and state.get("drive_file_id") and state.get("drive_permission_id"):
        return {
            "file_id": state["drive_file_id"],
            "permission_id": state["drive_permission_id"],
        }
    if step == "drive_share_file:update" and state.get("drive_file_id") and state.get("drive_permission_id"):
        return {
            "file_id": state["drive_file_id"],
            "permission_id": state["drive_permission_id"],
            "role": "commenter",
        }
    if step == "drive_unshare_file" and state.get("drive_file_id") and state.get("drive_permission_id"):
        return {
            "file_id": state["drive_file_id"],
            "permission_id": state["drive_permission_id"],
        }
    if step == "drive_list_drives:one" and state.get("drive_id"):
        return {"drive_id": state["drive_id"]}
    if step == "drive_update_drive:hide" and state.get("drive_id"):
        return {"drive_id": state["drive_id"], "hidden": True}
    if step == "drive_update_drive:unhide" and state.get("drive_id"):
        return {"drive_id": state["drive_id"], "hidden": False}
    if step == "drive_delete_file":
        target = (
            state.get("drive_copy_file_id")
            or state.get("drive_google_doc_id")
            or state.get("drive_file_id")
            or state.get("drive_folder_id")
        )
        if target:
            return {"file_id": target}
        return None
    if step == "keep_get_note" and state.get("note_name"):
        return {"note_name": state["note_name"]}
    if step == "keep_delete_note" and state.get("note_name"):
        return {"note_name": state["note_name"], "confirm_delete": False}
    if step == "chat_get_space" and state.get("space_name"):
        return {"space_name": state["space_name"]}
    if step == "chat_list_messages" and state.get("space_name"):
        return {"space_name": state["space_name"], "page_size": 5}
    if step == "chat_get_message" and state.get("message_name"):
        return {"message_name": state["message_name"]}
    if step == "chat_create_message" and state.get("space_name"):
        return {"space_name": state["space_name"], "text": "QA test message", "notify": False}
    if step == "chat_update_message" and state.get("message_name"):
        return {"message_name": state["message_name"], "text": "QA test message updated"}
    if step == "chat_delete_message" and state.get("message_name"):
        return {"message_name": state["message_name"], "force": True}
    return None


def _get_data(out: object) -> dict | None:
    data = getattr(out, "data", out)
    return data if isinstance(data, dict) else None


def _item_id(item: object, key: str = "id") -> str | None:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _update_state(step: str, args: dict, out: object, state: dict) -> None:
    try:
        data = _get_data(out)
        if not data:
            return
        if step == "gmail_search_emails:inbox" and data.get("messages"):
            state["message_ids"] = [m["id"] for m in data["messages"]]
            state["one_message_id"] = state["message_ids"][0]
            # Reuse first known thread id for thread tool coverage.
            first = data["messages"][0]
            if isinstance(first, dict):
                state["thread_id"] = first.get("thread_id") or state["thread_id"]
        if step == "gmail_search_emails:unsubscribe" and "messages" in data:
            state["search_message_ids"] = [m["id"] for m in data["messages"]]
        if step == "gmail_search_emails:threads" and data.get("threads"):
            first = data["threads"][0]
            if isinstance(first, dict):
                state["thread_id"] = first.get("thread_id") or state["thread_id"]
        if step == "gmail_search_emails:has_attachment" and data.get("messages"):
            state["attachment_message_id"] = data["messages"][0]["id"]
        if step == "gmail_list_labels" and "labels" in data:
            labels = data.get("labels") or []
            existing_user_labels = [
                lb for lb in labels
                if isinstance(lb, dict) and lb.get("type") == "user" and lb.get("id")
            ]
            if existing_user_labels:
                state["existing_label_id"] = existing_user_labels[0]["id"]
        if step == "gmail_read_emails":
            messages = data.get("messages") or []
            first = messages[0] if messages and isinstance(messages[0], dict) else {}
            state["thread_id"] = first.get("thread_id") or state.get("thread_id")
            state["history_id"] = first.get("history_id") or state.get("history_id")
        if step == "gmail_read_emails:attachment":
            messages = data.get("messages") or []
            first = messages[0] if messages and isinstance(messages[0], dict) else {}
            attachments = [a for a in first.get("attachments") or [] if isinstance(a, dict) and a.get("download_id")]
            if attachments:
                state["attachment_download_id"] = attachments[0]["download_id"]
        if step == "gmail_manage_labels:create" and "label" in data:
            state["label_id"] = _item_id(data["label"])
        if step == "gmail_manage_labels:delete" and data.get("status") == "ok":
            state["label_id"] = None
        if step == "gmail_manage_filters:create" and "filter" in data:
            state["filter_id"] = _item_id(data["filter"])
        if step == "gmail_save_draft:create" and "draft" in data:
            state["draft_id"] = _item_id(data["draft"])
        if step == "gmail_delete:message_trash" and args.get("message_ids"):
            state["deleted_message_id"] = args["message_ids"][0]
        if step == "gmail_manage_vacation:get" and "vacation" in data:
            state["vacation_settings"] = data["vacation"]
        if step == "calendar_search_events" and data.get("events"):
            state["read_event_id"] = _item_id(data["events"][0])
        if step == "calendar_create_event" and "event" in data:
            state["event_id"] = _item_id(data["event"])
        if step == "drive_list_drives" and data.get("drives"):
            first = data["drives"][0]
            if isinstance(first, dict):
                state["drive_id"] = first.get("id")
        if step == "drive_create_folder" and "file" in data:
            state["drive_folder_id"] = _item_id(data["file"])
        if step == "drive_upload_file" and "file" in data:
            state["drive_file_id"] = _item_id(data["file"])
        if step == "drive_upload_file:google_doc" and "file" in data:
            state["drive_google_doc_id"] = _item_id(data["file"])
        if step == "drive_copy_file" and "file" in data:
            state["drive_copy_file_id"] = _item_id(data["file"])
        if step == "prepare_workspace_action:drive_share_file" and data.get("commit_token"):
            state["drive_commit_token"] = data["commit_token"]
        if step == "commit_workspace_action:drive_share_file":
            shared = data.get("result") if isinstance(data.get("result"), dict) else {}
            if "permission" in shared:
                state["drive_permission_id"] = _item_id(shared["permission"])
        if step == "drive_unshare_file" and args.get("permission_id"):
            state["drive_permission_id"] = None
        if step == "drive_delete_file" and args.get("file_id"):
            deleted_id = args["file_id"]
            if state.get("drive_copy_file_id") == deleted_id:
                state["drive_copy_file_id"] = None
            if state.get("drive_google_doc_id") == deleted_id:
                state["drive_google_doc_id"] = None
            if state.get("drive_file_id") == deleted_id:
                state["drive_file_id"] = None
            if state.get("drive_folder_id") == deleted_id:
                state["drive_folder_id"] = None
        if step == "keep_create_note" and "note" in data:
            state["note_name"] = _item_id(data["note"], "name")
        if step == "chat_list_spaces" and "spaces" in data and data["spaces"]:
            name_val = _item_id(data["spaces"][0], "name")
            state["space_name"] = name_val
            state["space_id_uri"] = (name_val or "").replace("spaces/", "") or name_val
        if step == "chat_create_message" and "message" in data:
            state["message_name"] = _item_id(data["message"], "name")
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
    client = Client(workspace_mcp, elicitation_handler=_accept_confirmation)

    async with client:
        await run_tools(client, state, results)
        await run_resources(client, state, results)
        await run_prompts(client, results)

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n--- QA results (MCP_TOOL_TIER={os.environ['MCP_TOOL_TIER']}) ---")
    for item, ok, err in results:
        status = "PASS" if ok else "FAIL"
        extra = f" | {err[:80]}" if err else ""
        print(f"  {status}  {item}{extra}")
    print(f"\nTotal: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
