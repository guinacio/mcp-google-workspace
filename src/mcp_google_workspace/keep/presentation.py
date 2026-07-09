"""Compact Google Keep note representations."""

from __future__ import annotations

from typing import Any


def note_envelope(note: dict[str, Any], *, max_text: int | None = 500) -> dict[str, Any]:
    body = note.get("body", {})
    text = str(body.get("text", {}).get("text") or "").strip()
    items = body.get("list", {}).get("listItems", [])
    if items and not text:
        text = "\n".join(str(item.get("text", {}).get("text") or "") for item in items)
    attachments = note.get("attachments", [])
    return {
        "id": note.get("name"),
        "title": note.get("title") or (text.splitlines()[0] if text else "(untitled note)"),
        "text": text[:max_text] if max_text is not None else text,
        "text_truncated": max_text is not None and len(text) > max_text,
        "checklist": {"total": len(items), "completed": sum(bool(item.get("checked")) for item in items)},
        "attachment_count": len(attachments),
        "created_at": note.get("createTime"),
        "updated_at": note.get("updateTime"),
        "trashed_at": note.get("trashTime"),
    }
