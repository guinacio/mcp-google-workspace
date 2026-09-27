"""Client side of the fleet qualification suite (``test_fleet_qualification``).

Talks to the stack in ``deploy/fleet-test`` exclusively through nginx over
TLS (``https://localhost:<FLEET_HTTPS_PORT>``), trusting only the throwaway
CA generated for the run, and mints RS256 bearer tokens with the test-time
signing key whose JWKS the stack's issuer serves (the W5 ``http_harness``
approach, with the key material on disk instead of in-process).

Replica identity is read from the TEST-ONLY ``X-Fleet-Upstream`` response
header nginx adds (``$upstream_addr``) and mapped to compose services through
``docker inspect``. Google calls are read from the fake transport's Redis
list. Every wait is a poll with a deadline; nothing sleeps to synchronize.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import importlib.util
from itertools import count
import json
from pathlib import Path
import ssl
import subprocess
import time
from types import ModuleType
from typing import Any, TypeVar

import httpx
from joserfc import jwt
from joserfc.jwk import RSAKey

REPO = Path(__file__).resolve().parent.parent
FLEET_DIR = REPO / "deploy" / "fleet-test"
RUNTIME = FLEET_DIR / ".runtime"
MODERN = "2026-07-28"
LEGACY = "2025-11-25"
TASKS_EXTENSION = {"extensions": {"io.modelcontextprotocol/tasks": {}}}
ELICITATION = {"elicitation": {"form": {}}}
REPLICAS = ("replica-1", "replica-2")
T = TypeVar("T")


def load_orchestrator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_orchestrator", FLEET_DIR / "fleet.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def poll(check: Callable[[], T | None], *, timeout: float, interval: float = 0.25, what: str = "condition") -> T:
    """Return the first truthy ``check()`` result, or fail after *timeout* seconds."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        try:
            last = check()
        except (httpx.HTTPError, OSError) as exc:
            last = exc
        else:
            if last:
                return last
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}; last={last!r}")
        time.sleep(interval)


@dataclass
class Call:
    response: httpx.Response
    replica: str | None

    @property
    def body(self) -> dict[str, Any]:
        return self.response.json()

    @property
    def result(self) -> dict[str, Any]:
        payload = self.body
        assert "result" in payload, payload
        return payload["result"]


@dataclass
class Fleet:
    orchestrator: ModuleType
    values: dict[str, str]
    http: httpx.Client
    signing_key: RSAKey
    ssl_context: ssl.SSLContext
    ids: Iterator[int] = field(default_factory=lambda: count(1))
    _addresses: dict[str, str] = field(default_factory=dict)

    @classmethod
    def connect(cls, orchestrator: ModuleType) -> Fleet:
        values = orchestrator.runtime_values()
        context = ssl.create_default_context(cafile=str(RUNTIME / "ca.pem"))
        base = f"https://localhost:{values['FLEET_HTTPS_PORT']}"
        return cls(
            orchestrator=orchestrator,
            values=values,
            http=httpx.Client(base_url=base, verify=context, timeout=60.0),
            signing_key=RSAKey.import_key((RUNTIME / "jwt-signing-key.pem").read_bytes()),
            ssl_context=context,
        )

    # -- identity -------------------------------------------------------------

    @property
    def base(self) -> str:
        return str(self.http.base_url).rstrip("/")

    @property
    def issuer(self) -> str:
        return self.values["FLEET_ISSUER"]

    def token(self, subject: str, *, expires_in: int = 600, extra: dict[str, Any] | None = None) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": subject,
            "aud": self.values["FLEET_AUDIENCE"],
            "iat": now,
            "exp": now + expires_in,
            "client_id": f"fleet-host-{subject}",
            **(extra or {}),
        }
        header = {"alg": "RS256", "kid": self.orchestrator.SIGNING_KID, "typ": "JWT"}
        return jwt.encode(header, claims, self.signing_key, algorithms=["RS256"])

    # -- MCP requests ---------------------------------------------------------

    def headers(self, method: str, *, subject: str | None = "alice", name: str | None = None,
                version: str = MODERN, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": version,
            "Mcp-Method": method,
        }
        if subject is not None:
            headers["Authorization"] = f"Bearer {self.token(subject)}"
        if name is not None:
            headers["Mcp-Name"] = name
        headers.update(extra or {})
        return headers

    def body(self, method: str, params: dict[str, Any] | None = None, *, version: str = MODERN,
             capabilities: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        body_params = dict(params or {})
        body_params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientInfo": {"name": "fleet-qualification", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": capabilities or {},
            **(meta or {}),
        }
        return {"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": body_params}

    def post(self, method: str, params: dict[str, Any] | None = None, *, subject: str | None = "alice",
             name: str | None = None, capabilities: dict[str, Any] | None = None, path: str = "/gw/mcp",
             headers: dict[str, str] | None = None, meta: dict[str, Any] | None = None,
             timeout: float | None = None) -> Call:
        response = self.http.post(
            path,
            headers=self.headers(method, subject=subject, name=name, extra=headers),
            json=self.body(method, params, capabilities=capabilities, meta=meta),
            timeout=timeout or 60.0,
        )
        assert "mcp-session-id" not in response.headers
        return Call(response, self.replica_of(response))

    def tool(self, subject: str, name: str, arguments: dict[str, Any] | None = None, *,
             capabilities: dict[str, Any] | None = None, extra: dict[str, Any] | None = None,
             path: str = "/gw/mcp", meta: dict[str, Any] | None = None, timeout: float | None = None) -> Call:
        params = {"name": name, "arguments": arguments or {}, **(extra or {})}
        call = self.post("tools/call", params, subject=subject, name=name, capabilities=capabilities,
                         path=path, meta=meta, timeout=timeout)
        assert call.response.status_code == 200, call.response.text
        return call

    # -- topology -------------------------------------------------------------

    def compose(self, *args: str, check: bool = True, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.orchestrator.compose_command(*args),
            check=check, text=True, capture_output=True, timeout=timeout,
        )

    def _refresh_addresses(self) -> None:
        ids = self.compose("ps", "--all", "--quiet").stdout.split()
        if not ids:
            return
        inspected = subprocess.run(
            ["docker", "inspect", *ids], check=True, text=True, capture_output=True, timeout=60
        )
        self._addresses.clear()
        for container in json.loads(inspected.stdout):
            service = container["Config"]["Labels"].get("com.docker.compose.service", "")
            for network in (container.get("NetworkSettings", {}).get("Networks") or {}).values():
                if network.get("IPAddress"):
                    self._addresses[network["IPAddress"]] = service

    def replica_of(self, response: httpx.Response) -> str | None:
        upstream = response.headers.get("x-fleet-upstream", "")
        # "$upstream_addr" lists every attempted server; the last one answered.
        address = upstream.split(",")[-1].strip().rsplit(":", 1)[0]
        if not address:
            return None
        if address not in self._addresses:
            self._refresh_addresses()
        return self._addresses.get(address, address)

    def container(self, service: str) -> str:
        ids = self.compose("ps", "--all", "--quiet", service).stdout.split()
        assert ids, f"no container for {service}"
        return ids[0]

    def docker(self, *args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        # Plain docker (not compose) so starting a service never re-runs its
        # compose dependencies (fleet-init).
        return subprocess.run(["docker", *args], check=True, text=True, capture_output=True, timeout=timeout)

    def health(self, service: str) -> str:
        return self.docker(
            "inspect", "--format", "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            self.container(service),
        ).stdout.strip()

    def wait_healthy(self, service: str, timeout: float = 120.0) -> None:
        poll(lambda: self.health(service) == "running/healthy", timeout=timeout, interval=0.5,
             what=f"{service} healthy")

    def ready(self, service: str) -> httpx.Response:
        return self.http.get(f"/_fleet/{service}/health/ready", timeout=10.0)

    def wait_ready(self, service: str, timeout: float = 120.0) -> dict[str, Any]:
        response = poll(
            lambda: (lambda r: r if r.status_code == 200 else None)(self.ready(service)),
            timeout=timeout, what=f"{service} readiness",
        )
        return response.json()

    def google_calls(self) -> list[dict[str, Any]]:
        result = self.compose(
            "exec", "-T", "redis", "redis-cli", "--tls", "--cacert", "/tls/ca.pem",
            "--user", "fleetadmin", "--no-auth-warning", "--raw", "LRANGE", "fleet:google:calls", "0", "-1",
            timeout=60,
        )
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")]

    def calls_matching(self, marker: str, phase: str = "done") -> list[dict[str, Any]]:
        return [call for call in self.google_calls() if marker in call["path"] and call["phase"] == phase]

    def redis(self, *args: str) -> str:
        return self.compose(
            "exec", "-T", "redis", "redis-cli", "--tls", "--cacert", "/tls/ca.pem",
            "--user", "fleetadmin", "--no-auth-warning", "--raw", *args, timeout=60,
        ).stdout
