"""Prompt templates for Chat workflows."""

from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(server: FastMCP) -> None:
    @server.prompt(name="draft_chat_announcement_prompt")
    def draft_chat_announcement_prompt(topic: str, audience: str = "team") -> str:
        return (
            f"Draft a concise Google Chat announcement about: {topic}\n"
            f"Audience: {audience}\n"
            "Use clear action-oriented language."
        )

    @server.prompt(name="summarize_chat_thread_prompt")
    def summarize_chat_thread_prompt(thread_messages: str) -> str:
        return (
            "Summarize this Google Chat thread into key decisions and action items:\n\n"
            f"{thread_messages}"
        )
