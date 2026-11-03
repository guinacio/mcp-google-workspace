"""Real-socket HTTP harness for W5 tests.

Runs one or more ASGI apps under uvicorn on ephemeral loopback ports in a
single background thread (one event loop), plus helpers for an in-process
OIDC-style signing key server (JWKS) and bearer-token minting.

Synchronization is event-based: :class:`LiveServers` returns only after every
uvicorn server finished its startup (lifespan included) and shuts them down
with bounded joins. Ports come from sockets bound to ``127.0.0.1:0`` before
the apps are built, so an app can know its own public base URL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
import socket
import threading
import time
from typing import Any

import uvicorn
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

STARTUP_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 30.0


def reserve_sockets(count: int) -> list[socket.socket]:
    sockets = []
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.setblocking(False)
        sockets.append(sock)
    return sockets


def socket_url(sock: socket.socket) -> str:
    host, port = sock.getsockname()[:2]
    return f"http://{host}:{port}"


class _Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, ready: threading.Event) -> None:
        super().__init__(config)
        self._ready = ready

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            await super().startup(sockets=sockets)
        finally:
            self._ready.set()


class LiveServers:
    """Serve ``apps[i]`` on ``sockets[i]`` until the context exits."""

    def __init__(self, sockets: Sequence[socket.socket], apps: Sequence[Any]) -> None:
        if len(sockets) != len(apps):
            raise ValueError("one socket per app")
        self.sockets = list(sockets)
        self.urls = [socket_url(sock) for sock in self.sockets]
        self._servers: list[_Server] = []
        self._ready = [threading.Event() for _ in apps]
        for app, ready in zip(apps, self._ready, strict=True):
            config = uvicorn.Config(
                app,
                lifespan="on",
                log_level="warning",
                timeout_graceful_shutdown=1,
                ws="none",
            )
            self._servers.append(_Server(config, ready))
        self._thread = threading.Thread(target=self._run, name="w5-live-http", daemon=True)
        self._error: BaseException | None = None

    def _run(self) -> None:
        async def main() -> None:
            await asyncio.gather(
                *(server.serve(sockets=[sock]) for server, sock in zip(self._servers, self.sockets, strict=True))
            )

        try:
            asyncio.run(main())
        except BaseException as exc:  # pragma: no cover - surfaced in __enter__/__exit__
            self._error = exc
            for ready in self._ready:
                ready.set()

    def __enter__(self) -> LiveServers:
        self._thread.start()
        for ready in self._ready:
            if not ready.wait(STARTUP_TIMEOUT):
                raise TimeoutError("uvicorn did not start in time")
        if self._error is not None or not all(server.started for server in self._servers):
            self.__exit__(None, None, None)
            raise RuntimeError(f"uvicorn failed to start: {self._error!r}")
        return self

    def __exit__(self, *exc: object) -> None:
        for server in self._servers:
            server.should_exit = True
        self._thread.join(SHUTDOWN_TIMEOUT)
        for sock in self.sockets:
            try:
                sock.close()
            except OSError:  # pragma: no cover
                pass


@dataclass
class SigningKeys:
    """A rotating RS256 key set published as JWKS by a local key server."""

    published: dict[str, RSAKeyPair] = field(default_factory=dict)
    fetches: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, kid: str) -> RSAKeyPair:
        pair = RSAKeyPair.generate()
        with self.lock:
            self.published[kid] = pair
        return pair

    def remove(self, kid: str) -> None:
        with self.lock:
            self.published.pop(kid, None)

    def jwks(self) -> dict[str, Any]:
        with self.lock:
            keys = []
            for kid, pair in self.published.items():
                jwk = RSAKey.import_key(pair.public_key).as_dict()
                jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
                keys.append(jwk)
            self.fetches += 1
        return {"keys": keys}

    def app(self) -> Starlette:
        async def jwks(_: Request) -> JSONResponse:
            return JSONResponse(self.jwks())

        return Starlette(routes=[Route("/jwks", jwks)])


def mint(
    pair: RSAKeyPair,
    *,
    kid: str,
    subject: str,
    issuer: str,
    audience: str,
    expires_in: int = 600,
    client_id: str = "w5-client",
    extra: dict[str, Any] | None = None,
    drop: Sequence[str] = (),
) -> str:
    """An RS256 bearer token; ``drop`` removes standard claims (e.g. ``exp``)."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "client_id": client_id,
        **(extra or {}),
    }
    for name in drop:
        claims.pop(name, None)
    from joserfc import jwt

    key = RSAKey.import_key(pair.private_key.get_secret_value())
    return jwt.encode({"alg": "RS256", "kid": kid, "typ": "JWT"}, claims, key, algorithms=["RS256"])


def mcp_headers(
    method: str,
    *,
    token: str | None = None,
    name: str | None = None,
    version: str = "2026-07-28",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if name is not None:
        headers["Mcp-Name"] = name
    headers.update(extra or {})
    return headers


def mcp_body(
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    version: str = "2026-07-28",
    capabilities: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body_params = dict(params or {})
    body_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "w5-test", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": capabilities or {},
        **(meta or {}),
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
