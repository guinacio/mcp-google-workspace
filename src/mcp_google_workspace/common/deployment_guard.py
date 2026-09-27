"""Refuse test-only switches in a production process.

The fleet qualification stack (``deploy/fleet-test``) replaces Google with a
fake transport that exists only in a derived, never-published test image and
activates through ``MCP_FLEET_FAKE_GOOGLE``. The production image contains no
such fake, so the variable would do nothing there -- which is exactly why it
must not be accepted silently: a configuration copied from the test stack
must fail at startup rather than run against real Google while its operator
believes otherwise (or the reverse).
"""

from __future__ import annotations

from collections.abc import Mapping
import os
import sys
from typing import Final

TEST_ONLY_FAKE_GOOGLE_ENV: Final[str] = "MCP_FLEET_FAKE_GOOGLE"
# Module the fleet-test image loads at interpreter start after validating its
# own guard (sentinel value, .test issuer, loopback base URL).
_FAKE_HOOK_MODULE: Final[str] = "fleet_fake_google.hook"


def reject_test_only_settings(
    environ: Mapping[str, str] | None = None,
    modules: Mapping[str, object] | None = None,
) -> None:
    """Raise ``SystemExit`` when a test-only switch is set outside the test image."""
    env = os.environ if environ is None else environ
    loaded = sys.modules if modules is None else modules
    if TEST_ONLY_FAKE_GOOGLE_ENV not in env:
        return
    if _FAKE_HOOK_MODULE in loaded:
        return
    raise SystemExit(
        f"{TEST_ONLY_FAKE_GOOGLE_ENV} is a test-only setting of the fleet qualification "
        "image; this image has no fake Google provider. Remove it from the environment."
    )


__all__ = ["TEST_ONLY_FAKE_GOOGLE_ENV", "reject_test_only_settings"]
