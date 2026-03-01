"""Drive tools registration."""

from fastmcp import FastMCP

from . import drives, files, permissions


def register_tools(server: FastMCP) -> None:
    files.register(server)
    permissions.register(server)
    drives.register(server)
