"""Gmail tools registration."""

from fastmcp import FastMCP

from . import (
    attachments,
    batch,
    drafts,
    filters,
    history,
    labels,
    message_state,
    messages,
    search,
    settings,
    threads,
)


def register_tools(server: FastMCP) -> None:
    messages.register(server)
    search.register(server)
    labels.register(server)
    filters.register(server)
    attachments.register(server)
    batch.register(server)
    drafts.register(server)
    threads.register(server)
    history.register(server)
    settings.register(server)
    message_state.register(server)
