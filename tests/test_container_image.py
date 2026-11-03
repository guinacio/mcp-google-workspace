"""W7a: container qualification of the production image (repository Dockerfile).

Skipped unless ``MCP_FLEET_TEST=1`` (needs Docker). Uses ``FLEET_BASE_IMAGE``
when set (CI builds the image once), otherwise builds
``mcp-google-workspace:fleet-test`` through ``deploy/fleet-test/fleet.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import os
import platform
import subprocess
from typing import Any

import httpx
import pytest

from fleet_harness import REPO, load_orchestrator, poll

pytestmark = [
    pytest.mark.fleet,
    pytest.mark.skipif(
        os.getenv("MCP_FLEET_TEST") != "1",
        reason="container qualification needs Docker; set MCP_FLEET_TEST=1",
    ),
]

# The image was 753 MB before W7a (a recursive chown duplicated the venv layer).
MAX_IMAGE_BYTES = 550 * 1024 * 1024

INSPECT_SCRIPT = r"""
import json, os, pathlib, sys
site = pathlib.Path("/app/.venv/lib/python3.12/site-packages")
dists = sorted(p.name for p in site.glob("*.dist-info"))
ui = pathlib.Path("/app/src/mcp_google_workspace/apps/ui")
print(json.dumps({
    "uid": os.getuid(),
    "dists": dists,
    "pth": sorted(p.name for p in site.glob("*.pth")),
    "fake_google": (site / "fleet_fake_google").exists(),
    "ui": sorted(p.name for p in ui.iterdir()),
    "ui_dist": sorted(p.name for p in (ui / "dist").iterdir()),
    "test_dirs": sorted(str(p) for p in pathlib.Path("/app/src").rglob("tests") if p.is_dir()),
    "node_modules": any(pathlib.Path("/app").rglob("node_modules")),
    "app_writable": os.access("/app/src/mcp_google_workspace", os.W_OK) or os.access("/app/.venv", os.W_OK),
    "data_writable": os.access("/data/tokens", os.W_OK) and os.access("/data/gemini", os.W_OK),
    "playwright": any(n.lower().startswith("playwright") for n in dists),
}))
"""


def _docker(*args: str, timeout: float = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout, check=check)


@pytest.fixture(scope="module")
def image() -> str:
    tag = os.getenv("FLEET_BASE_IMAGE", "").strip()
    if tag:
        return tag
    orchestrator = load_orchestrator()
    return str(orchestrator.build())


def _normalized(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _locked(*flags: str) -> set[str]:
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-hashes", "--no-emit-project", "--format", "requirements-txt", *flags],
        cwd=REPO, text=True, capture_output=True, check=True, timeout=120,
    ).stdout
    names = set()
    for line in exported.splitlines():
        if "==" not in line or line.startswith(("#", " ")):
            continue
        requirement, _, marker = line.partition(";")
        if any(platform in marker for platform in ("win32", "Windows", "emscripten", "darwin")):
            continue  # not installed in a Linux image
        names.add(_normalized(requirement.split("==", 1)[0].strip()))
    return names


def test_image_is_non_root_with_oci_labels(image: str) -> None:
    config = json.loads(_docker("image", "inspect", image).stdout)[0]["Config"]
    assert config["User"] == "mcp"
    labels = config["Labels"]
    assert labels["io.modelcontextprotocol.server.name"] == "io.github.guinacio/mcp-google-workspace"
    for name in ("title", "description", "source", "licenses", "revision"):
        assert labels.get(f"org.opencontainers.image.{name}"), name
    assert labels["org.opencontainers.image.source"] == "https://github.com/guinacio/mcp-google-workspace"
    assert config["Cmd"] == ["mcp-google-workspace-http"]


def test_image_has_no_dev_dependencies_tests_or_test_hooks(image: str) -> None:
    result = _docker("run", "--rm", "--entrypoint", "python", image, "-c", INSPECT_SCRIPT)
    facts: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    assert facts["uid"] != 0
    installed = {_normalized(name.removesuffix(".dist-info").rsplit("-", 1)[0]) for name in facts["dists"]}
    dev_only = _locked("--all-groups") - _locked("--no-dev")
    assert dev_only, "the lock has no dev-only packages to check"
    assert installed & dev_only == set(), sorted(installed & dev_only)
    assert installed >= _locked("--no-dev"), sorted(_locked("--no-dev") - installed)
    assert not facts["playwright"]
    assert facts["fake_google"] is False
    assert facts["pth"] == ["_editable_impl_mcp_google_workspace.pth", "_virtualenv.pth"]
    assert facts["ui"] == ["dist"] and facts["ui_dist"] == ["index.html"]
    assert facts["test_dirs"] == [] and facts["node_modules"] is False
    assert facts["app_writable"] is False  # code and venv are root-owned
    assert facts["data_writable"] is True


def test_image_size_is_bounded(image: str, record_property: Any) -> None:
    size = int(_docker("image", "inspect", "--format", "{{.Size}}", image).stdout.strip())
    architecture = _docker("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image).stdout.strip()
    record_property("image_bytes", size)
    print(f"image {image} {architecture}: {size} bytes ({size / 1024 / 1024:.1f} MiB)")
    assert size < MAX_IMAGE_BYTES


@pytest.fixture()
def standalone(image: str) -> Iterator[str]:
    """One container with the release smoke configuration (no Redis: one process)."""
    from cryptography.fernet import Fernet

    name = f"mcp-w7a-standalone-{os.getpid()}"
    _docker("rm", "--force", name, check=False)
    _docker(
        "run", "--detach", "--name", name, "--publish", "127.0.0.1::8000",
        "--env", "MCP_HTTP_BASE_URL=http://127.0.0.1:8000",
        "--env", "MCP_HTTP_JWT_ISSUER=https://issuer.example.com",
        "--env", "MCP_HTTP_JWT_AUDIENCE=google-workspace-mcp",
        "--env", "MCP_HTTP_JWKS_URI=https://issuer.example.com/.well-known/jwks.json",
        "--env", "MCP_GOOGLE_OAUTH_REDIRECT_URL=http://127.0.0.1:8000/google/oauth/callback",
        "--env", f"MCP_TOKEN_ENCRYPTION_KEY={Fernet.generate_key().decode()}",
        image,
    )
    try:
        port = _docker("port", name, "8000/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        _docker("rm", "--force", name, check=False)


def test_image_serves_health_version_and_the_auth_challenge(standalone: str, image: str) -> None:
    # The published port is loopback-mapped; the app's Host allow-list is the
    # base URL's authority, so send that Host explicitly.
    headers = {"Host": "127.0.0.1:8000"}
    architecture = _docker("image", "inspect", "--format", "{{.Architecture}}", image).stdout.strip()
    native = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    # Under QEMU emulation (e.g. linux/arm64 on an amd64 host) startup is ~10x slower.
    startup_deadline = 60 if architecture == native else 300
    live = poll(lambda: (lambda r: r if r.status_code == 200 else None)(
        httpx.get(f"{standalone}/health/live", headers=headers, timeout=5)),
        timeout=startup_deadline, what="health/live")
    assert live.json()["status"] == "ok"
    ready = httpx.get(f"{standalone}/health/ready", headers=headers, timeout=10)
    assert ready.status_code == 200, ready.text
    assert ready.json()["fleet"] is False
    version = httpx.get(f"{standalone}/version", headers=headers, timeout=5).json()
    revision = json.loads(_docker("image", "inspect", image).stdout)[0]["Config"]["Labels"][
        "org.opencontainers.image.revision"
    ]
    assert version["commit"] == revision
    assert version["mcp_protocol_versions"]["preferred"] == "2026-07-28"
    assert "test_only_fake_google" not in version
    unauthenticated = httpx.post(
        f"{standalone}/mcp",
        headers={**headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        timeout=10,
    )
    assert unauthenticated.status_code == 401
    assert ('resource_metadata="http://127.0.0.1:8000/.well-known/oauth-protected-resource/mcp"'
            in unauthenticated.headers["www-authenticate"])


def test_image_refuses_the_test_only_fake_google_switch(image: str) -> None:
    result = _docker(
        "run", "--rm", "--env", "MCP_FLEET_FAKE_GOOGLE=fleet-qualification-only", image, check=False, timeout=120,
    )
    assert result.returncode != 0
    assert "test-only setting" in (result.stdout + result.stderr)
