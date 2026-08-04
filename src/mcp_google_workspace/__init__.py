"""mcp_google_workspace package."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("mcp-google-workspace")
except PackageNotFoundError:  # pragma: no cover - source tree without installed metadata
    __version__ = "development"
