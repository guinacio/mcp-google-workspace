"""httplib2-compatible fake Google transport. TEST ONLY (see ``hook``).

It answers the few Google endpoints the fleet suite drives with canned JSON,
records every request (replica hostname, API, method, path, phase) in the
shared Redis list :data:`CALLS_KEY` so the suite can count mutations across
processes, and supports two in-band markers:

* ``fleet-slow-<seconds>`` anywhere in the URL or body: sleep before
  answering (in-flight calls for drain, restart and cancellation tests);
* ``fleet-fail-<status>``: answer with that HTTP status and a Google error.

Unknown hosts (for example an OAuth token refresh) get a 599 so an unexpected
path fails loudly instead of pretending to succeed.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httplib2
import redis

from . import CALLS_KEY

_SLOW = re.compile(r"fleet-slow-(\d{1,3})")
_FAIL = re.compile(r"fleet-fail-([45]\d\d)")
_CLIENT_LOCK = threading.Lock()
_CLIENT: Any = None


def _redis() -> Any:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = redis.Redis.from_url(os.environ["MCP_REDIS_URL"])
        return _CLIENT


def record(api: str, method: str, path: str, phase: str) -> None:
    entry = {
        "host": socket.gethostname(),
        "api": api,
        "method": method,
        "path": path,
        "phase": phase,
        "at": round(time.time(), 3),
    }
    _redis().rpush(CALLS_KEY, json.dumps(entry, separators=(",", ":")))


def _answer(api: str, method: str, path: str) -> dict[str, Any]:
    if api == "calendar" and path.endswith("/settings/timezone"):
        return {"kind": "calendar#setting", "id": "timezone", "value": "UTC"}
    if api == "calendar" and path.endswith("/events") and method == "GET":
        return {"kind": "calendar#events", "items": []}
    if api == "gmail" and path.endswith("/messages") and method == "GET":
        return {"messages": [], "resultSizeEstimate": 0}
    if api == "sheets" and path.endswith(":batchUpdate"):
        spreadsheet = path.split("/spreadsheets/", 1)[-1].split(":", 1)[0]
        return {"spreadsheetId": spreadsheet, "replies": [{}]}
    if api == "sheets" and method == "GET" and "/spreadsheets/" in path:
        spreadsheet = path.split("/spreadsheets/", 1)[-1].split("/", 1)[0]
        return {
            "spreadsheetId": spreadsheet,
            "properties": {"title": f"Fleet {spreadsheet}"},
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}],
        }
    return {}


class FakeGoogleHttp:
    """The subset of ``httplib2.Http`` that google-auth-httplib2 uses."""

    def __init__(self, api_name: str) -> None:
        self.api_name = api_name
        self.timeout = 30
        self.redirect_codes = frozenset({300, 301, 302, 303, 307, 308})
        self.connections: dict[str, Any] = {}
        self.follow_redirects = True

    def request(
        self,
        uri: str,
        method: str = "GET",
        body: Any = None,
        headers: Any = None,
        redirections: int = 5,
        connection_type: Any = None,
        **_: Any,
    ) -> tuple[httplib2.Response, bytes]:
        parsed = urlsplit(uri)
        host = (parsed.hostname or "").lower()
        path = unquote(parsed.path)
        if not host.endswith("googleapis.com"):
            return httplib2.Response({"status": "599"}), b'{"error":"fleet fake: unexpected host"}'
        text = f"{uri} {body.decode('utf-8', 'replace') if isinstance(body, bytes) else body or ''}"
        record(self.api_name, method, path, "start")
        slow = _SLOW.search(text)
        if slow:
            time.sleep(min(int(slow.group(1)), 300))
        failed = _FAIL.search(text)
        if failed:
            status = int(failed.group(1))
            payload: dict[str, Any] = {"error": {"code": status, "message": "fleet fake failure", "status": "FAILED_PRECONDITION"}}
        else:
            status, payload = 200, _answer(self.api_name, method, path)
        record(self.api_name, method, path, "done")
        response = httplib2.Response({"status": str(status), "content-type": "application/json; charset=UTF-8"})
        return response, json.dumps(payload).encode()

    def close(self) -> None:
        return None

    def add_credentials(self, *_: Any, **__: Any) -> None:
        return None

    def add_certificate(self, *_: Any, **__: Any) -> None:
        return None
