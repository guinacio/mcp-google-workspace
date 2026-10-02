"""W7a: fleet qualification against a real multi-process deployment.

Skipped unless ``MCP_FLEET_TEST=1`` (never part of the default ``pytest -q``)::

    MCP_FLEET_TEST=1 uv run pytest -m fleet tests/test_fleet_qualification.py

The module brings ``deploy/fleet-test`` up cold (fresh secrets, fresh volumes)
and tears it down afterwards. ``MCP_FLEET_EXTERNAL=1`` uses a stack that is
already up (``deploy/fleet-test/fleet.py up``); ``MCP_FLEET_KEEP=1`` leaves it
running. Every assertion goes through nginx over TLS: two replicas built from
the repository ``Dockerfile`` (separate containers and OS processes), one
task-worker container, Redis 8 (TLS, ACL, AOF) and an S3 API, with Google
answered by the test-only fake transport. Tests are ordered: the disruptive
ones (restarts, Redis restart, drain) run last and leave the fleet ready.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
import json
import os
import socket
import ssl
import threading
import time
from typing import Any
from uuid import uuid4

import anyio
import httpx
import pytest

from fleet_harness import (
    ELICITATION,
    LEGACY,
    REPLICAS,
    RUNTIME,
    TASKS_EXTENSION,
    Call,
    Fleet,
    load_orchestrator,
    poll,
)

pytestmark = [
    pytest.mark.fleet,
    pytest.mark.skipif(
        os.getenv("MCP_FLEET_TEST") != "1",
        reason="fleet qualification needs Docker; set MCP_FLEET_TEST=1",
    ),
]

EXPECTED_READINESS = {
    "encryption",
    "token_storage",
    "redis",
    "upload_object_storage",
    "app_state",
    "task_queue",
    "operation_records",
    "operation_lease",
    "continuation_keys",
    "fleet_storage",
    "legacy_session_affinity",
}
APP_MAX_BYTES = 1024 * 1024  # MCP_MAX_REQUEST_BYTES in compose.yaml
NGINX_MAX_BYTES = 2 * 1024 * 1024  # client_max_body_size in nginx.conf
RATE_LIMIT = 120  # MCP_RATE_LIMIT_PER_MINUTE in compose.yaml


@pytest.fixture(scope="module")
def fleet() -> Iterator[Fleet]:
    orchestrator = load_orchestrator()
    external = os.getenv("MCP_FLEET_EXTERNAL") == "1"
    keep = os.getenv("MCP_FLEET_KEEP") == "1"
    if not external:
        orchestrator.up()
    client = Fleet.connect(orchestrator)
    try:
        for service in REPLICAS:
            client.wait_ready(service)
        client.wait_healthy("worker")
        yield client
    finally:
        client.http.close()
        logs = subprocess_logs(orchestrator)
        (RUNTIME / "compose.log").write_text(logs, encoding="utf-8")
        if not external and not keep:
            orchestrator.down()


def subprocess_logs(orchestrator: Any) -> str:
    import subprocess

    result = subprocess.run(
        orchestrator.compose_command("logs", "--no-color", "--timestamps"),
        text=True, capture_output=True, timeout=120, check=False,
    )
    return result.stdout + result.stderr


def assert_alternates(calls: list[Call]) -> None:
    replicas = [call.replica for call in calls]
    assert all(replica in REPLICAS for replica in replicas), replicas
    for previous, current in zip(replicas, replicas[1:]):
        assert current != previous, f"consecutive requests hit the same replica: {replicas}"


def structured(call: Call) -> dict[str, Any]:
    result = call.result
    return result.get("structuredContent") or {}


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def test_every_replica_is_ready_with_the_fleet_checks(fleet: Fleet) -> None:
    for service in REPLICAS:
        response = fleet.ready(service)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == "ready" and report["fleet"] is True
        assert EXPECTED_READINESS <= set(report["checks"]), sorted(report["checks"])
        for name in EXPECTED_READINESS - {"legacy_session_affinity"}:
            assert report["checks"][name]["ok"] is True, (service, name, report["checks"][name])
        assert report["checks"]["app_state"]["backend"] == "redis"
        assert report["checks"]["operation_records"]["backend"] == "redis"
        assert report["checks"]["token_storage"]["backend"] == "redis"
        assert report["checks"]["task_queue"]["backend"] == "redis"
        assert report["checks"]["task_queue"]["snapshot_encryption"] is True
        assert report["checks"]["fleet_storage"]["replicas"] == 2
        # Advisory only: legacy affinity is a load-balancer property.
        assert report["checks"]["legacy_session_affinity"]["required"] is False
        assert report["warnings"] == ["legacy_session_affinity"]
        assert report["admission_scope"] == "fleet"

        version = fleet.http.get(f"/_fleet/{service}/version").json()
        assert version["mcp_protocol_versions"]["tested"] == ["2025-11-25", "2026-07-28"]
        assert version["test_only_fake_google"] is True  # the fake is active, visibly
    # The load-balanced path answers too.
    assert fleet.http.get("/gw/health/ready").status_code == 200
    assert fleet.http.get("/gw/health/live").json()["status"] == "ok"


def test_replica_without_continuation_keys_is_not_ready(fleet: Fleet) -> None:
    response = poll(lambda: (lambda r: r if r.status_code == 503 else None)(fleet.ready("replica-nokeys")),
                    timeout=60, what="replica-nokeys readiness")
    report = response.json()
    assert report["status"] == "not_ready"
    assert report["checks"]["continuation_keys"]["ok"] is False
    assert report["checks"]["task_queue"]["ok"] is True


def test_replica_with_an_empty_task_snapshot_key_is_not_ready(fleet: Fleet) -> None:
    # Regression (W7a): an empty FASTMCP_TASKS_ENCRYPTION_KEY used to report
    # snapshot_encryption: true and ready, while fastmcp-tasks refuses the
    # empty key on the first task submission.
    response = poll(lambda: (lambda r: r if r.status_code == 503 else None)(fleet.ready("replica-notaskkey")),
                    timeout=60, what="replica-notaskkey readiness")
    report = response.json()
    assert report["status"] == "not_ready"
    assert report["checks"]["task_queue"]["ok"] is False
    assert report["checks"]["task_queue"]["snapshot_encryption"] is False
    assert report["checks"]["continuation_keys"]["ok"] is True


# ---------------------------------------------------------------------------
# HTTP boundaries through the proxy
# ---------------------------------------------------------------------------


def test_protected_resource_metadata_is_reachable_under_the_prefix(fleet: Fleet) -> None:
    unauthenticated = fleet.post("tools/list", subject=None)
    assert unauthenticated.response.status_code == 401
    challenge = unauthenticated.response.headers["www-authenticate"]
    metadata_url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
    assert metadata_url == f"{fleet.base}/.well-known/oauth-protected-resource/gw/mcp"
    document = fleet.http.get(metadata_url.removeprefix(fleet.base)).json()
    assert document["resource"] == f"{fleet.base}/gw/mcp"
    assert [server.rstrip("/") for server in document["authorization_servers"]] == [fleet.issuer]

    wrong_audience = fleet.http.post(
        "/gw/mcp",
        headers={**fleet.headers("tools/list", subject=None),
                 "Authorization": f"Bearer {fleet.token('heidi', extra={'aud': 'another-resource'})}"},
        json=fleet.body("tools/list"),
    )
    assert wrong_audience.status_code == 401


def test_routing_header_mismatch_and_bad_origin_are_rejected_through_the_proxy(fleet: Fleet) -> None:
    before = len(fleet.google_calls())
    mismatch = fleet.http.post(
        "/gw/mcp",
        headers=fleet.headers("tools/call", subject="heidi", name="files_list_files"),
        json=fleet.body("tools/call", {"name": "sheets_get_spreadsheet", "arguments": {"spreadsheet_id": "hdr"}}),
    )
    assert mismatch.status_code == 400, mismatch.text
    assert mismatch.json()["error"]["code"] == -32020

    evil = fleet.post("tools/list", subject="heidi", headers={"Origin": "https://evil.example"})
    assert evil.response.status_code == 403
    same_origin = fleet.post("tools/list", subject="heidi", headers={"Origin": fleet.base})
    assert same_origin.response.status_code == 200
    wrong_host = fleet.http.post(
        "/gw/mcp", headers={**fleet.headers("tools/list", subject="heidi"), "Host": "evil.example"},
        json=fleet.body("tools/list"),
    )
    assert wrong_host.status_code == 421
    assert len(fleet.google_calls()) == before


def _raw_tls(fleet: Fleet) -> ssl.SSLSocket:
    port = int(fleet.values["FLEET_HTTPS_PORT"])
    raw = socket.create_connection(("127.0.0.1", port), timeout=30)
    sock = fleet.ssl_context.wrap_socket(raw, server_hostname="localhost")
    sock.settimeout(30)
    return sock


def _head(fleet: Fleet, extra: dict[str, str]) -> bytes:
    headers = {
        "Host": f"localhost:{fleet.values['FLEET_HTTPS_PORT']}",
        **fleet.headers("tools/call", subject="heidi", name="files_list_files"),
        **extra,
    }
    return ("POST /gw/mcp HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode()


def _response(sock: ssl.SSLSocket) -> tuple[int, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    length = next(
        (int(line.split(b":", 1)[1]) for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")),
        None,
    )
    while length is not None and len(rest) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    return status, rest


def test_chunked_oversized_body_gets_413_while_streaming_through_nginx(fleet: Fleet) -> None:
    sock = _raw_tls(fleet)
    try:
        sock.sendall(_head(fleet, {"Transfer-Encoding": "chunked"}))
        chunk = b"x" * (64 * 1024)
        sent = 0
        try:
            # Never send the terminating chunk. With request buffering nginx
            # would wait for the whole body (and time out); streamed, the
            # app's limiter answers as soon as the limit is crossed.
            while sent <= APP_MAX_BYTES + 4 * len(chunk):
                sock.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                sent += len(chunk)
        except OSError:
            pass
        status, body = _response(sock)
    finally:
        sock.close()
    assert status == 413
    assert json.loads(body) == {"error": "request_too_large", "max_bytes": APP_MAX_BYTES}

    # A declared length above nginx's own bound is refused by nginx itself.
    sock = _raw_tls(fleet)
    try:
        sock.sendall(_head(fleet, {"Content-Length": str(NGINX_MAX_BYTES + 1)}))
        status, _ = _response(sock)
    finally:
        sock.close()
    assert status == 413

    # A small chunked body is served normally.
    body = json.dumps(fleet.body("tools/call", {"name": "files_list_files", "arguments": {}})).encode()
    sock = _raw_tls(fleet)
    try:
        sock.sendall(_head(fleet, {"Transfer-Encoding": "chunked", "Connection": "close"}))
        sock.sendall(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
        status, _ = _response(sock)
    finally:
        sock.close()
    assert status == 200


def test_progress_streams_as_sse_through_nginx_without_buffering(fleet: Fleet) -> None:
    opened = fleet.tool("heidi", "apps_get_dashboard")
    handle = structured(opened)["view"]["handle"]
    # Each Gmail list in the dashboard load now takes 2 s at the fake.
    fleet.tool("heidi", "apps_patch_state", {"view_handle": handle, "inbox_query": "fleet-slow-2"})
    arrivals: list[tuple[float, dict[str, Any]]] = []
    started = time.monotonic()
    with fleet.http.stream(
        "POST", "/gw/mcp",
        headers=fleet.headers("tools/call", subject="heidi", name="apps_get_dashboard"),
        json=fleet.body("tools/call", {"name": "apps_get_dashboard", "arguments": {"view_handle": handle}},
                        meta={"progressToken": "fleet-progress"}),
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                arrivals.append((time.monotonic() - started, json.loads(line[5:].strip())))
    progress = [(at, message) for at, message in arrivals if message.get("method") == "notifications/progress"]
    final_at, final = arrivals[-1]
    assert final["result"]["resultType"] == "complete"
    assert len(progress) >= 3
    # The first progress notifications reached the client while the tool was
    # still waiting on Google: nothing buffered the stream.
    assert final_at - progress[0][0] >= 3.0, arrivals


# ---------------------------------------------------------------------------
# Modern protocol, round robin without affinity
# ---------------------------------------------------------------------------


def test_dashboard_state_compare_and_set_across_replicas(fleet: Fleet) -> None:
    opened = fleet.tool("alice", "apps_get_dashboard")
    view = structured(opened)["view"]
    assert view["revision"] == 1
    patched = fleet.tool(
        "alice", "apps_patch_state",
        {"view_handle": view["handle"], "expected_revision": 1, "view": "day", "anchor_date": "2026-03-05"},
    )
    assert structured(patched)["view"]["revision"] == 2
    stale = fleet.tool(
        "alice", "apps_patch_state",
        {"view_handle": view["handle"], "expected_revision": 1, "view": "week"},
    )
    assert stale.result["isError"] is True
    assert structured(stale)["code"] == "view_state_conflict"
    current = fleet.tool("alice", "apps_get_state", {"view_handle": view["handle"]})
    assert structured(current)["view"]["revision"] == 2
    assert structured(current)["state"]["view"] == "day"
    foreign = fleet.tool("bob", "apps_get_state", {"view_handle": view["handle"]})
    assert structured(foreign)["code"] == "view_handle_invalid"
    assert_alternates([opened, patched, stale, current, foreign])


def test_upload_stored_on_one_replica_is_listed_read_and_deleted_on_the_others(fleet: Fleet) -> None:
    diagnostics = fleet.tool("bob", "get_mcp_apps_diagnostics")
    store_tool = structured(diagnostics)["hidden_callbacks"]["store_files"]
    content = f"fleet upload {uuid4().hex}\n".encode()
    stored = fleet.tool("bob", store_tool, {"files": [{
        "name": "fleet.txt", "size": len(content), "type": "text/plain",
        "data": base64.b64encode(content).decode(),
    }]})
    uploads = structured(stored)["result"]
    upload_id = uploads[0]["upload_id"]
    listed = fleet.tool("bob", "files_list_files")
    assert upload_id in [item["upload_id"] for item in structured(listed)["result"]]
    other = fleet.tool("alice", "files_list_files")  # isolation: another principal
    assert upload_id not in [item.get("upload_id") for item in structured(other)["result"]]
    read = fleet.tool("bob", "files_read_file", {"name": upload_id})
    assert structured(read)["result"]["content"] == content.decode()
    deleted = fleet.tool("bob", "files_delete_file", {"name": upload_id})
    assert structured(deleted) == {"status": "deleted", "name": upload_id}
    gone = fleet.tool("bob", "files_list_files")
    assert upload_id not in [item["upload_id"] for item in structured(gone)["result"]]
    calls = [diagnostics, stored, listed, other, read, deleted, gone]
    assert_alternates(calls)
    assert stored.replica == deleted.replica != read.replica == listed.replica


def _delete_contact(contact: str) -> tuple[str, dict[str, Any]]:
    return "people_delete_contact", {"person_name": f"people/{contact}"}


def _accept(state: str) -> dict[str, Any]:
    return {"inputResponses": {"confirm": {"action": "accept", "content": {"value": True}}}, "requestState": state}


def test_mrtr_confirmation_asked_on_one_replica_answered_on_the_other(fleet: Fleet) -> None:
    contact = unique("c-fleet")
    name, arguments = _delete_contact(contact)
    asked = fleet.tool("carol", name, arguments, capabilities=ELICITATION)
    assert asked.result["resultType"] == "input_required"
    state = asked.result["requestState"]
    assert fleet.calls_matching(contact) == []

    tampered_state = state[:-6] + ("A" if state[-6] != "A" else "B") + state[-5:]
    tampered = fleet.post("tools/call", {"name": name, "arguments": arguments, **_accept(tampered_state)},
                          subject="carol", name=name, capabilities=ELICITATION)
    assert tampered.response.status_code == 400
    assert tampered.body["error"]["data"] == {"reason": "invalid_request_state"}
    foreign = fleet.post("tools/call", {"name": name, "arguments": arguments, **_accept(state)},
                         subject="dave", name=name, capabilities=ELICITATION)
    assert foreign.response.status_code == 400
    assert fleet.calls_matching(contact) == []

    answered = fleet.tool("carol", name, arguments, capabilities=ELICITATION, extra=_accept(state))
    assert answered.result["resultType"] == "complete"
    assert answered.result.get("isError") in (None, False), answered.result
    assert structured(answered)["status"] == "deleted"
    mutations = fleet.calls_matching(contact)
    assert len(mutations) == 1 and mutations[0]["host"] == answered.replica
    assert answered.replica != asked.replica

    replayed = fleet.tool("carol", name, arguments, capabilities=ELICITATION, extra=_accept(state))
    assert replayed.result["_meta"]["mcp-google-workspace/operation"]["replayed"] is True
    assert structured(replayed)["status"] == "deleted"
    assert len(fleet.calls_matching(contact)) == 1
    assert_alternates([asked, tampered, foreign, answered, replayed])


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _submit_task(fleet: Fleet, subject: str, spreadsheet_id: str, *, path: str = "/gw/mcp") -> Call:
    call = fleet.tool(
        subject, "sheets_batch_update_spreadsheet",
        {"spreadsheet_id": spreadsheet_id, "requests": [{"addSheet": {"properties": {"title": "fleet"}}}]},
        capabilities=TASKS_EXTENSION, path=path,
    )
    assert call.result["resultType"] == "task", call.result
    return call


def _task_get(fleet: Fleet, subject: str, task_id: str, *, path: str = "/gw/mcp") -> Call:
    return fleet.post("tasks/get", {"taskId": task_id}, subject=subject, capabilities=TASKS_EXTENSION, path=path)


def _wait_task(fleet: Fleet, subject: str, task_id: str, *, timeout: float = 60.0,
               polls: list[Call] | None = None) -> dict[str, Any]:
    def finished() -> dict[str, Any] | None:
        call = _task_get(fleet, subject, task_id)
        if polls is not None:
            polls.append(call)
        result = call.result
        return result if result["status"] not in {"working", "input_required"} else None

    return poll(finished, timeout=timeout, interval=0.5, what=f"task {task_id}")


def test_task_submitted_on_one_replica_runs_on_the_worker_and_is_polled_on_the_other(fleet: Fleet) -> None:
    sheet = unique("fleet-task")
    submitted = _submit_task(fleet, "dave", sheet)
    polls: list[Call] = []
    done = _wait_task(fleet, "dave", submitted.result["taskId"], polls=polls)
    assert done["status"] == "completed"
    assert done["result"]["structuredContent"]["spreadsheetId"] == sheet
    assert polls[0].replica != submitted.replica
    assert_alternates([submitted, *polls])
    executions = fleet.calls_matching(sheet)
    assert [call["host"] for call in executions] == ["worker"]
    # The finished task reads the same on both replicas.
    for service in REPLICAS:
        again = _task_get(fleet, "dave", submitted.result["taskId"], path=f"/_fleet/{service}/mcp")
        assert again.result["status"] == "completed"


def test_task_cancellation_and_foreign_task_handles(fleet: Fleet) -> None:
    sheet = unique("fleet-cancel") + "-fleet-slow-30"
    submitted = _submit_task(fleet, "dave", sheet)
    task_id = submitted.result["taskId"]
    poll(lambda: fleet.calls_matching(sheet, "start"), timeout=30, what="worker picked up the task")

    for method in ("tasks/get", "tasks/cancel"):
        foreign = fleet.post(method, {"taskId": task_id}, subject="bob", capabilities=TASKS_EXTENSION)
        assert foreign.body["error"]["code"] == -32602, foreign.body
        assert foreign.body["error"]["message"] == f"Task {task_id} not found"

    cancelled = fleet.post("tasks/cancel", {"taskId": task_id}, subject="dave", capabilities=TASKS_EXTENSION)
    assert cancelled.response.status_code == 200, cancelled.response.text
    final = _wait_task(fleet, "dave", task_id, timeout=30)
    assert final["status"] == "cancelled", final
    for service in REPLICAS:
        seen = _task_get(fleet, "dave", task_id, path=f"/_fleet/{service}/mcp")
        assert seen.result["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Fleet admission
# ---------------------------------------------------------------------------


def test_per_principal_rate_limit_is_enforced_across_replicas(fleet: Fleet) -> None:
    subject = unique("rate")
    served: list[Call] = []
    for _ in range(RATE_LIMIT):
        call = fleet.tool(subject, "files_list_files")
        assert "error" not in call.body, call.body
        served.append(call)
    counts = {replica: sum(1 for call in served if call.replica == replica) for replica in REPLICAS}
    # Each replica alone saw half of the budget ...
    assert counts == {"replica-1": RATE_LIMIT // 2, "replica-2": RATE_LIMIT // 2}
    # ... yet the next call is refused on either one: the limit is fleet-wide.
    for service in REPLICAS:
        refused = fleet.post("tools/call", {"name": "files_list_files", "arguments": {}}, subject=subject,
                             name="files_list_files", path=f"/_fleet/{service}/mcp")
        error = refused.body["error"]
        assert error["code"] == -32005
        assert error["data"]["code"] == "rate_limited" and error["data"]["retryable"] is True
    assert "error" not in fleet.tool("alice", "files_list_files").body


# ---------------------------------------------------------------------------
# Legacy (2025-11-25) clients
# ---------------------------------------------------------------------------


def _legacy_headers(fleet: Fleet, session: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {fleet.token('grace')}",
    }
    if session is not None:
        headers.update({"Mcp-Session-Id": session, "MCP-Protocol-Version": LEGACY})
    return headers


def _legacy_session(fleet: Fleet, path: str) -> tuple[list[httpx.Response], str]:
    initialize = fleet.http.post(path, headers=_legacy_headers(fleet), json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "legacy", "version": "0"}},
    })
    assert initialize.status_code == 200, initialize.text
    session = initialize.headers["mcp-session-id"]
    responses = [initialize]
    responses.append(fleet.http.post(path, headers=_legacy_headers(fleet, session),
                                     json={"jsonrpc": "2.0", "method": "notifications/initialized"}))
    for index in range(4):
        responses.append(fleet.http.post(path, headers=_legacy_headers(fleet, session),
                                         json={"jsonrpc": "2.0", "id": 10 + index, "method": "tools/list"}))
    return responses, session


def test_legacy_session_breaks_under_plain_round_robin(fleet: Fleet) -> None:
    """Why the proxy needs legacy affinity (TEST-ONLY /rr route, no affinity)."""
    responses, _ = _legacy_session(fleet, "/rr/mcp")
    replicas = [fleet.replica_of(response) for response in responses]
    statuses = [response.status_code for response in responses]
    assert_alternates([Call(response, replica) for response, replica in zip(responses, replicas)])
    home = replicas[0]
    for replica, response in zip(replicas[1:], responses[1:]):
        if replica == home:
            assert response.status_code in (200, 202), response.text
        else:
            assert response.status_code == 404, response.text
            assert response.json()["error"]["message"] == "Session not found"
    assert 404 in statuses


def test_legacy_session_is_pinned_by_the_proxy_affinity_rule(fleet: Fleet) -> None:
    responses, _ = _legacy_session(fleet, "/gw/mcp")
    assert [response.status_code for response in responses] == [200, 202, 200, 200, 200, 200]
    assert len({fleet.replica_of(response) for response in responses}) == 1
    # Modern traffic interleaved with it still round-robins.
    modern = [fleet.post("tools/list", subject="grace") for _ in range(4)]
    assert_alternates(modern)


def test_legacy_client_lists_calls_and_confirms_through_the_proxy(fleet: Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    # httpx2 (the FastMCP client) trusts exactly this CA file.
    monkeypatch.setenv("SSL_CERT_FILE", str(RUNTIME / "ca.pem"))
    contact = unique("c-legacy")
    prompts: list[str] = []

    async def accept(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        return {name: True for name in params.requested_schema.get("properties", {})}

    async def scenario() -> tuple[str | None, list[str], Any]:
        transport = StreamableHttpTransport(f"{fleet.base}/gw/mcp", auth=fleet.token("grace"))
        async with Client(transport, mode="legacy", elicitation_handler=accept) as client:
            tools = [tool.name for tool in await client.list_tools()]
            deleted = await client.call_tool("people_delete_contact", {"person_name": f"people/{contact}"})
            return client.protocol_version, tools, deleted

    version, tools, deleted = anyio.run(scenario)
    assert version == LEGACY
    assert "files_list_files" in tools
    assert deleted.structured_content["status"] == "deleted"
    assert prompts == [f"Permanently delete contact people/{contact}?"]
    assert len(fleet.calls_matching(contact)) == 1


# ---------------------------------------------------------------------------
# Restart recovery, Redis restart and drain (disruptive; last)
# ---------------------------------------------------------------------------


@pytest.fixture()
def durable_flow(fleet: Fleet) -> dict[str, Any]:
    """State, an upload, a finished and a pending confirmation for one principal."""
    subject = "erin"
    view = structured(fleet.tool(subject, "apps_get_dashboard"))["view"]
    patched = fleet.tool(subject, "apps_patch_state",
                         {"view_handle": view["handle"], "expected_revision": 1, "view": "week"})
    assert structured(patched)["view"]["revision"] == 2
    store_tool = structured(fleet.tool(subject, "get_mcp_apps_diagnostics"))["hidden_callbacks"]["store_files"]
    content = f"durable {uuid4().hex}".encode()
    stored = fleet.tool(subject, store_tool, {"files": [{
        "name": "durable.txt", "size": len(content), "type": "text/plain",
        "data": base64.b64encode(content).decode(),
    }]})
    done_contact, pending_contact = unique("c-done"), unique("c-pending")
    name, done_args = _delete_contact(done_contact)
    asked = fleet.tool(subject, name, done_args, capabilities=ELICITATION)
    done_answer = _accept(asked.result["requestState"])
    assert structured(fleet.tool(subject, name, done_args, capabilities=ELICITATION, extra=done_answer))["status"] == "deleted"
    _, pending_args = _delete_contact(pending_contact)
    pending = fleet.tool(subject, name, pending_args, capabilities=ELICITATION)
    assert pending.result["resultType"] == "input_required"
    return {
        "subject": subject,
        "handle": view["handle"],
        "upload_id": structured(stored)["result"][0]["upload_id"],
        "content": content.decode(),
        "done": (done_contact, done_args, done_answer),
        "pending": (pending_contact, pending_args, _accept(pending.result["requestState"])),
    }


def _assert_flow_survived(fleet: Fleet, flow: dict[str, Any], *, revision: int) -> int:
    subject = flow["subject"]
    for service in REPLICAS:
        path = f"/_fleet/{service}/mcp"
        state = fleet.tool(subject, "apps_get_state", {"view_handle": flow["handle"]}, path=path)
        assert structured(state)["view"]["revision"] == revision, structured(state)
        read = fleet.tool(subject, "files_read_file", {"name": flow["upload_id"]}, path=path)
        assert structured(read)["result"]["content"] == flow["content"]
        contact, arguments, answer = flow["done"]
        replay = fleet.tool(subject, "people_delete_contact", arguments, capabilities=ELICITATION,
                            extra=answer, path=path)
        assert replay.result["_meta"]["mcp-google-workspace/operation"]["replayed"] is True
        assert len(fleet.calls_matching(contact)) == 1
    bumped = fleet.tool(subject, "apps_patch_state",
                        {"view_handle": flow["handle"], "expected_revision": revision, "view": "month"})
    assert structured(bumped)["view"]["revision"] == revision + 1
    return revision + 1


def test_restart_of_a_replica_and_the_worker_mid_flow(fleet: Fleet, durable_flow: dict[str, Any]) -> None:
    # A task queued while no worker runs (replicas are submit-only).
    fleet.docker("stop", "--time", "45", fleet.container("worker"))
    queued_sheet = unique("fleet-queued")
    queued = _submit_task(fleet, "erin", queued_sheet)
    assert _task_get(fleet, "erin", queued.result["taskId"]).result["status"] == "working"

    fleet.docker("restart", "--time", "45", fleet.container("replica-1"))
    fleet.docker("start", fleet.container("worker"))
    fleet.wait_ready("replica-1")
    fleet.wait_healthy("worker")

    done = _wait_task(fleet, "erin", queued.result["taskId"], timeout=90)
    assert done["status"] == "completed"
    assert [call["host"] for call in fleet.calls_matching(queued_sheet)] == ["worker"]
    _assert_flow_survived(fleet, durable_flow, revision=2)

    # The confirmation asked before the restart is answered afterwards, on
    # the restarted replica: it executes exactly once.
    contact, arguments, answer = durable_flow["pending"]
    answered = fleet.tool("erin", "people_delete_contact", arguments, capabilities=ELICITATION,
                          extra=answer, path="/_fleet/replica-1/mcp")
    assert structured(answered)["status"] == "deleted"
    assert len(fleet.calls_matching(contact)) == 1

    # A task running on the worker when it receives SIGTERM finishes, once,
    # and the worker stops promptly (regression: it used to ignore SIGTERM as
    # PID 1 and be SIGKILLed after the grace period, mid-task).
    running_sheet = unique("fleet-running") + "-fleet-slow-6"
    running = _submit_task(fleet, "erin", running_sheet)
    poll(lambda: fleet.calls_matching(running_sheet, "start"), timeout=30, what="worker started the task")
    started = time.monotonic()
    fleet.docker("restart", "--time", "45", fleet.container("worker"))
    assert time.monotonic() - started < 30, "the worker did not stop gracefully on SIGTERM"
    fleet.wait_healthy("worker")
    finished = _wait_task(fleet, "erin", running.result["taskId"], timeout=60)
    assert finished["status"] == "completed"
    assert len(fleet.calls_matching(running_sheet, "start")) == 1


def test_a_task_whose_worker_is_killed_mid_call_is_not_executed_again(fleet: Fleet) -> None:
    # Regression (W7a): Docket redelivers a task whose worker died (after
    # FASTMCP_DOCKET_REDELIVERY_TIMEOUT, 20 s here) and the body used to run
    # again, repeating a non-idempotent batchUpdate.
    sheet = unique("fleet-killed") + "-fleet-slow-6"
    submitted = _submit_task(fleet, "erin", sheet)
    poll(lambda: fleet.calls_matching(sheet, "start"), timeout=30, what="worker started the task")
    fleet.docker("kill", "--signal", "SIGKILL", fleet.container("worker"))
    fleet.docker("start", fleet.container("worker"))
    fleet.wait_healthy("worker")
    final = _wait_task(fleet, "erin", submitted.result["taskId"], timeout=120)
    result = final.get("result") or {}
    assert result.get("isError") is True, final
    assert result["structuredContent"]["code"] == "outcome_unknown"
    assert result["structuredContent"]["required_action"]["action"] == "verify_before_retry"
    assert len(fleet.calls_matching(sheet, "start")) == 1


def test_redis_restart_with_aof_keeps_state_uploads_operations_and_queue(
    fleet: Fleet, durable_flow: dict[str, Any]
) -> None:
    fleet.docker("restart", "--time", "20", fleet.container("redis"))
    fleet.wait_healthy("redis")
    for service in REPLICAS:
        fleet.wait_ready(service)
    fleet.wait_healthy("worker")
    # The first calls after the restart must succeed: every client reconnects.
    _assert_flow_survived(fleet, durable_flow, revision=2)
    sheet = unique("fleet-after-redis")
    submitted = _submit_task(fleet, "erin", sheet)
    assert _wait_task(fleet, "erin", submitted.result["taskId"], timeout=90)["status"] == "completed"
    assert [call["host"] for call in fleet.calls_matching(sheet)] == ["worker"]


def test_sigterm_drains_a_replica_while_the_proxy_routes_around_it(fleet: Fleet) -> None:
    sheet = unique("fleet-drain") + "-fleet-slow-8"
    outcome: dict[str, Any] = {}

    def slow_call() -> None:
        started = time.monotonic()
        try:
            call = fleet.tool("frank", "sheets_get_spreadsheet", {"spreadsheet_id": sheet},
                              path="/_fleet/replica-1/mcp", timeout=90)
            outcome["call"] = call
        except BaseException as exc:  # noqa: BLE001 - reported below
            outcome["error"] = exc
        outcome["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=slow_call, name="fleet-slow-call")
    worker.start()
    poll(lambda: fleet.calls_matching(sheet, "start"), timeout=30, what="slow call in flight on replica-1")
    fleet.compose("kill", "--signal", "SIGTERM", "replica-1")
    signalled = time.monotonic()

    # Readiness is red (the listener is closed) while the call is still running ...
    poll(lambda: fleet.ready("replica-1").status_code != 200, timeout=5, interval=0.1,
         what="replica-1 readiness red")
    assert "call" not in outcome and "error" not in outcome
    # ... and the proxy routes new work to the other replica.
    rerouted = [fleet.post("tools/list", subject="frank") for _ in range(4)]
    assert [call.response.status_code for call in rerouted] == [200] * 4
    assert {call.replica for call in rerouted} == {"replica-2"}

    worker.join(90)
    assert "error" not in outcome, outcome.get("error")
    call: Call = outcome["call"]
    assert call.result["resultType"] == "complete" and call.result.get("isError") in (None, False), call.result
    assert structured(call)["spreadsheetId"] == sheet
    assert time.monotonic() - signalled > 2.5  # longer than FastMCP's former 2 s cut-off
    assert fleet.calls_matching(sheet, "done")

    poll(lambda: fleet.health("replica-1").startswith("exited"), timeout=60, what="replica-1 exited")
    fleet.docker("start", fleet.container("replica-1"))
    fleet.wait_ready("replica-1")
    back = [fleet.post("tools/list", subject="frank") for _ in range(4)]
    assert {call.replica for call in back} == set(REPLICAS)


def test_logs_contain_no_secrets_or_unhandled_exceptions(fleet: Fleet) -> None:
    logs = subprocess_logs(fleet.orchestrator)
    # Regression: the 413 path used to log an ASGI traceback per oversized body.
    assert "Exception in ASGI application" not in logs
    values = fleet.values
    for name in ("FLEET_REDIS_PASSWORD", "FLEET_REDIS_ADMIN_PASSWORD", "FLEET_S3_SECRET_KEY",
                 "FLEET_REQUEST_STATE_KEYS", "FLEET_TASKS_ENCRYPTION_KEY"):
        assert values[name] not in logs, f"{name} appears in container logs"
    token_key = json.loads((RUNTIME / "secrets" / "mcp-secret.json").read_text())["token_encryption_keys"]
    for key in token_key.values():
        assert key not in logs
    assert "backend=rediss://mcp:***@redis:6379/0" in logs  # the worker's redacted banner
