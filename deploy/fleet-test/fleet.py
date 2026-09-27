"""Bring the fleet qualification stack up and down (test only).

The stack (``compose.yaml``) is a real multi-process deployment of the
production image: Redis (TLS + ACL + AOF), an S3-compatible object store, two
HTTP replicas, one out-of-process task worker, and nginx terminating TLS in
front of them. Google is replaced by a test-only transport that exists only in
a derived image (``fake_google/``); see ``docs/DEPLOYMENT_FLEET.md``.

Everything secret is generated here, at test time, into ``.runtime/``
(gitignored, excluded from the Docker build context): a throwaway CA and
server certificate, the JWT signing key and its JWKS, the token key ring,
``MCP_REQUEST_STATE_KEYS``, ``FASTMCP_TASKS_ENCRYPTION_KEY``, Redis ACL
passwords and S3 credentials. Nothing generated here is ever committed.

Usage (from the repository root)::

    uv run python deploy/fleet-test/fleet.py up      # prepare + build + up --wait
    uv run python deploy/fleet-test/fleet.py down    # remove containers, networks, volumes
    uv run python deploy/fleet-test/fleet.py logs

Environment:

* ``FLEET_BASE_IMAGE`` -- production image under test. When unset, it is built
  from the repository ``Dockerfile`` as ``mcp-google-workspace:fleet-test``;
  when set (CI builds it once), it is used as is.
* ``FLEET_HTTPS_PORT`` -- loopback port nginx publishes (default 18443).
* ``FLEET_PROJECT`` -- compose project name (default ``mcp-fleet-test``).
"""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RUNTIME = HERE / ".runtime"
COMPOSE_FILE = HERE / "compose.yaml"
FAKE_CONTEXT = HERE / "fake_google"

DEFAULT_BASE_IMAGE = "mcp-google-workspace:fleet-test"
FAKE_IMAGE = "mcp-google-workspace-fleet-fake:local"
ISSUER = "https://issuer.fleet.test"
AUDIENCE = "fleet-mcp"
SIGNING_KID = "fleet-k1"
FAKE_GOOGLE_SENTINEL = "fleet-qualification-only"
UP_TIMEOUT_SECONDS = 240


def project() -> str:
    return os.getenv("FLEET_PROJECT", "mcp-fleet-test")


def https_port() -> int:
    return int(os.getenv("FLEET_HTTPS_PORT", "18443"))


def base_url() -> str:
    return f"https://localhost:{https_port()}/gw"


def compose_command(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project(),
        "--file",
        str(COMPOSE_FILE),
        "--env-file",
        str(RUNTIME / "fleet.env"),
        *args,
    ]


def run(command: list[str], *, check: bool = True, timeout: float | None = None, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    if not quiet:
        print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        check=check,
        text=True,
        timeout=timeout,
        capture_output=quiet,
    )


# ---------------------------------------------------------------------------
# Test-time secrets
# ---------------------------------------------------------------------------


def _write(path: Path, content: str | bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(content)
    # Readable by the non-root users inside the containers (redis, nginx, mcp).
    # These are throwaway test secrets in a gitignored directory.
    path.chmod(mode)


def _certificates() -> dict[str, bytes]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mcp fleet-test throwaway CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName("issuer.fleet.test"),
        x509.DNSName("redis"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    return {
        "ca.pem": ca_cert.public_bytes(pem),
        "tls/ca.pem": ca_cert.public_bytes(pem),
        "tls/server.crt": server_cert.public_bytes(pem) + ca_cert.public_bytes(pem),
        "tls/server.key": server_key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    }


def _signing_key() -> tuple[bytes, dict[str, Any]]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwk = {"kty": "RSA", "kid": SIGNING_KID, "alg": "RS256", "use": "sig", "n": b64(numbers.n), "e": b64(numbers.e)}
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return private, {"keys": [jwk]}


def prepare() -> dict[str, str]:
    """Generate every secret into ``.runtime/`` (fresh for each cold start)."""
    from cryptography.fernet import Fernet

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    for name, content in _certificates().items():
        _write(RUNTIME / name, content)
    private, jwks = _signing_key()
    _write(RUNTIME / "jwt-signing-key.pem", private, 0o600)
    _write(RUNTIME / "issuer" / "jwks.json", json.dumps(jwks))
    (RUNTIME / "issuer").chmod(0o755)
    (RUNTIME / "tls").chmod(0o755)

    # Token/grant/app-state/upload key ring, in the versioned MCP_SECRET_FILE form.
    _write(
        RUNTIME / "secrets" / "mcp-secret.json",
        json.dumps(
            {
                "token_encryption_keys": {"fleet-2026a": Fernet.generate_key().decode()},
                "active_token_encryption_key_id": "fleet-2026a",
            }
        ),
    )
    # A syntactically valid, fake Google OAuth client (the server-owned
    # credentials.json production mounts from its secret store). It is only
    # read, never used: the fake transport answers every Google request.
    _write(
        RUNTIME / "secrets" / "credentials.json",
        json.dumps(
            {
                "web": {
                    "client_id": "fleet-fake-client.apps.googleusercontent.com",
                    "client_secret": secrets.token_urlsafe(24),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [f"{base_url()}/google/oauth/callback"],
                }
            }
        ),
    )
    (RUNTIME / "secrets").chmod(0o755)

    redis_password = secrets.token_urlsafe(24)
    admin_password = secrets.token_urlsafe(24)
    # Least privilege that the application, Docket and the fake still need:
    # everything except administrative and dangerous commands.
    _write(
        RUNTIME / "redis" / "users.acl",
        "user default off\n"
        f"user mcp on >{redis_password} ~* &* +@all -@admin -@dangerous\n"
        f"user fleetadmin on >{admin_password} ~* &* +@all\n",
    )
    (RUNTIME / "redis").chmod(0o755)

    values = {
        "FLEET_IMAGE": FAKE_IMAGE,
        "FLEET_HTTPS_PORT": str(https_port()),
        "FLEET_BASE_URL": base_url(),
        "FLEET_ISSUER": ISSUER,
        "FLEET_AUDIENCE": AUDIENCE,
        "FLEET_FAKE_GOOGLE": FAKE_GOOGLE_SENTINEL,
        "FLEET_REDIS_PASSWORD": redis_password,
        "FLEET_REDIS_ADMIN_PASSWORD": admin_password,
        "FLEET_S3_ACCESS_KEY": "fleet" + secrets.token_hex(8),
        "FLEET_S3_SECRET_KEY": secrets.token_urlsafe(30),
        "FLEET_REQUEST_STATE_KEYS": secrets.token_hex(32),
        "FLEET_TASKS_ENCRYPTION_KEY": secrets.token_urlsafe(40),
    }
    _write(RUNTIME / "fleet.env", "".join(f"{key}={value}\n" for key, value in values.items()), 0o600)
    return values


def runtime_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (RUNTIME / "fleet.env").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key:
            values[key] = value
    return values


# ---------------------------------------------------------------------------
# Images and lifecycle
# ---------------------------------------------------------------------------


def build() -> str:
    base = os.getenv("FLEET_BASE_IMAGE", "").strip()
    if not base:
        base = DEFAULT_BASE_IMAGE
        run(["docker", "build", "--tag", base, "--build-arg", f"MCP_BUILD_COMMIT={_commit()}", str(REPO)])
    run(
        [
            "docker",
            "build",
            "--tag",
            FAKE_IMAGE,
            "--build-arg",
            f"BASE_IMAGE={base}",
            str(FAKE_CONTEXT),
        ]
    )
    return base


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True, capture_output=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def up() -> None:
    down(quiet=True)
    prepare()
    build()
    started = time.monotonic()
    try:
        run(compose_command("up", "--detach", "--wait", "--wait-timeout", str(UP_TIMEOUT_SECONDS)))
    except subprocess.CalledProcessError:
        logs()
        raise
    print(f"fleet ready in {time.monotonic() - started:.1f}s at {base_url()}", flush=True)


def down(*, quiet: bool = False) -> None:
    if not (RUNTIME / "fleet.env").exists():
        _write(RUNTIME / "fleet.env", f"FLEET_IMAGE={FAKE_IMAGE}\n", 0o600)
    run(compose_command("down", "--volumes", "--remove-orphans", "--timeout", "5"), check=False, quiet=quiet)


def logs() -> None:
    run(compose_command("logs", "--no-color", "--timestamps"), check=False)


def main(argv: list[str]) -> int:
    commands = {"prepare": prepare, "build": build, "up": up, "down": down, "logs": logs}
    if len(argv) != 1 or argv[0] not in commands:
        print(f"usage: fleet.py {{{'|'.join(commands)}}}", file=sys.stderr)
        return 2
    commands[argv[0]]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
