"""Reusable prompt templates for email workflows."""

from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(server: FastMCP) -> None:
    @server.prompt(name="compose_email_prompt")
    def compose_email_prompt(topic: str, tone: str = "professional") -> str:
        return (
            f"Write an email about: {topic}\n"
            f"Tone: {tone}\n"
            "Return a clear subject and a concise body."
        )

    @server.prompt(name="reply_email_prompt")
    def reply_email_prompt(original_email: str, intent: str) -> str:
        return (
            "You are writing a reply email.\n"
            f"Intent: {intent}\n"
            "Original email:\n"
            f"{original_email}\n"
            "Draft a polite and precise response."
        )

    @server.prompt(name="summarize_inbox_prompt")
    def summarize_inbox_prompt(count: int = 10) -> str:
        return (
            f"Summarize the {count} most recent inbox emails.\n"
            "Highlight priorities, deadlines, and required actions."
        )
