"""Prefect Horizon / FastMCP Cloud entrypoint: set Entrypoint to ``horizon_server.py:mcp``.

The package uses relative imports, so its modules cannot be the entrypoint
file themselves (``src/mcp_google_workspace/server.py`` fails with "attempted
relative import with no known parent package"). This shim imports the package
by name instead. Configuration: see ``mcp_google_workspace.hosted``.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import mcp_google_workspace  # noqa: F401  (installed project)
except ImportError:  # dependencies only: load the package from the checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mcp_google_workspace.hosted import build_gateway_hosted_server

mcp = build_gateway_hosted_server()
