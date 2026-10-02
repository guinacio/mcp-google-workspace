"""Interpreter-startup hook (loaded by ``fleet_fake_google.pth``). TEST ONLY.

Safety design -- the fake can never answer for Google in a real deployment:

1. **Not in the production image.** This code exists only in the derived
   fleet-test image; the production image has no ``fleet_fake_google`` module
   and no ``.pth`` hook (asserted by the container qualification).
2. **Explicit opt-in with a sentinel.** Nothing happens unless
   ``MCP_FLEET_FAKE_GOOGLE`` equals :data:`SENTINEL`. Any other value
   terminates the process: a typo never silently falls back to real Google.
3. **Refuses non-test identities.** Activation also requires the JWT issuer
   and JWKS hosts under the reserved ``.test`` TLD (RFC 6761; it never
   resolves publicly) and a loopback public base URL. A real deployment's
   issuer and base URL cannot satisfy this, so the process exits (code 78).
4. **Production rejects the variable.** The production entrypoints refuse to
   start when ``MCP_FLEET_FAKE_GOOGLE`` is set but no fake is installed
   (``common.deployment_guard``), so the variable cannot be carried into a
   production configuration unnoticed.
5. **Visible.** ``/version`` reports ``test_only_fake_google: true`` and the
   process logs a warning at startup.
6. **No egress.** The fleet stack runs every Python container on an
   ``internal`` Docker network with only the throwaway CA trusted.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys
from types import ModuleType
from typing import Any, Callable
from urllib.parse import urlsplit

from . import ENV, SENTINEL

EXIT_CONFIG = 78  # EX_CONFIG


def guard_failures(env: Any) -> list[str]:
    """Reasons the fake must not activate in this environment (empty = allowed)."""
    failures = []
    for name in ("MCP_HTTP_JWT_ISSUER", "MCP_HTTP_JWKS_URI"):
        host = (urlsplit(env.get(name, "")).hostname or "").lower()
        if not host.endswith(".test"):
            failures.append(f"{name} host {host!r} is not under the reserved .test TLD")
    base_host = (urlsplit(env.get("MCP_HTTP_BASE_URL", "")).hostname or "").lower()
    if base_host not in {"localhost", "127.0.0.1"}:
        failures.append(f"MCP_HTTP_BASE_URL host {base_host!r} is not loopback")
    return failures


def _die(message: str) -> None:
    sys.stderr.write(f"fleet_fake_google: refusing to start: {message}\n")
    sys.stderr.flush()
    os._exit(EXIT_CONFIG)


def _patch_google_auth(module: ModuleType) -> None:
    from .transport import FakeGoogleHttp

    instrumented = module.InstrumentedAuthorizedHttp

    def fake_authorized_http(credentials: Any, settings: Any, api_name: str) -> Any:
        # Real credential loading (Redis token store, key ring, scopes) and the
        # real googleapiclient request path run unchanged; only the socket
        # layer is replaced.
        return instrumented(credentials, api_name=api_name, http=FakeGoogleHttp(api_name))

    module._build_authorized_http = fake_authorized_http


def _patch_production(module: ModuleType) -> None:
    original: Callable[[], dict[str, Any]] = module.build_version_payload

    def build_version_payload() -> dict[str, Any]:
        return {**original(), "test_only_fake_google": True}

    module.build_version_payload = build_version_payload


_TARGETS: dict[str, Callable[[ModuleType], None]] = {
    "mcp_google_workspace.auth.google_auth": _patch_google_auth,
    "mcp_google_workspace.common.production": _patch_production,
}


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, inner: Any, patch: Callable[[ModuleType], None]) -> None:
        self._inner = inner
        self._patch = patch

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return self._inner.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._inner.exec_module(module)
        self._patch(module)


class _PatchingFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        patch = _TARGETS.get(fullname)
        if patch is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchingLoader(spec.loader, patch)
                return spec
        return None


def activate() -> bool:
    value = os.environ.get(ENV)
    if value is None:
        return False
    if value != SENTINEL:
        _die(f"{ENV} must be exactly {SENTINEL!r} (test-only switch)")
    failures = guard_failures(os.environ)
    if failures:
        _die("; ".join(failures))
    if any(name in sys.modules for name in _TARGETS):
        _die("the application was imported before the fake could be installed")
    sys.meta_path.insert(0, _PatchingFinder())
    sys.stderr.write("WARNING fleet_fake_google: Google APIs are served by a TEST-ONLY fake transport\n")
    return True


activate()
