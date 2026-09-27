"""TEST-ONLY fake Google transport for the fleet qualification stack.

This package is never part of the production image. It is copied only into
the derived ``mcp-google-workspace-fleet-fake:local`` image built by
``deploy/fleet-test/fleet.py`` (``fake_google/Dockerfile``), which is never
published. See :mod:`fleet_fake_google.hook` for the activation guard.
"""

SENTINEL = "fleet-qualification-only"
ENV = "MCP_FLEET_FAKE_GOOGLE"
CALLS_KEY = "fleet:google:calls"
