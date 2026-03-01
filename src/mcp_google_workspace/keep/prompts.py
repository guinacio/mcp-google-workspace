"""Prompt templates for Keep note workflows."""

from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(server: FastMCP) -> None:
    @server.prompt(name="summarize_keep_note_prompt")
    def summarize_keep_note_prompt(note_text: str) -> str:
        return (
            "Summarize this Google Keep note into actionable bullets:\n\n"
            f"{note_text}"
        )

    @server.prompt(name="extract_actions_from_keep_notes_prompt")
    def extract_actions_from_keep_notes_prompt(notes_blob: str) -> str:
        return (
            "Extract action items, deadlines, and owners from these Keep notes:\n\n"
            f"{notes_blob}"
        )
