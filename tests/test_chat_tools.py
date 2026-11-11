from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import anyio
import pytest
from fastmcp import Client
from google.auth.credentials import AnonymousCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcp_google_workspace.chat.client import (
    _describe_http_error,
    normalize_message_name,
    normalize_space_name,
    normalize_user_name,
)
from mcp_google_workspace.chat import tools as chat_tools
from mcp_google_workspace.chat.server import chat_mcp
from mcp_google_workspace.chat.presentation import message_envelope, space_envelope
from mcp_google_workspace.chat.schemas import ListMessagesRequest, ListSpacesRequest


def test_normalize_space_name():
    assert normalize_space_name("spaces/AAA") == "spaces/AAA"
    assert normalize_space_name("AAA") == "spaces/AAA"


def test_normalize_message_name():
    assert normalize_message_name("spaces/AAA/messages/BBB") == "spaces/AAA/messages/BBB"


def test_normalize_message_name_invalid():
    with pytest.raises(ValueError):
        normalize_message_name("BBB")


def test_normalize_user_name():
    assert normalize_user_name("users/123") == "users/123"
    assert normalize_user_name("123") == "users/123"


def test_describe_http_error():
    exc = HttpError(
        SimpleNamespace(status=403, reason="Forbidden"),
        b'{"error":{"status":"PERMISSION_DENIED","message":"nope"}}',
    )
    details = _describe_http_error(exc)
    assert "status=403" in details
    assert "reason=Forbidden" in details
    assert "PERMISSION_DENIED" in details


def test_message_envelope_exposes_a_human_author_and_compact_content():
    message = {
        "name": "spaces/AAA/messages/BBB",
        "text": "Hello\nthere",
        "sender": {"name": "users/123", "type": "HUMAN"},
        "createTime": "2026-07-09T12:00:00Z",
        "attachment": [{"name": "spaces/AAA/attachments/1"}],
    }

    result = message_envelope(
        message,
        {"displayName": "Ada Lovelace", "email": "ada@example.com"},
        account_timezone="America/Sao_Paulo",
    )

    assert result["author"] == {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "user_id": "users/123",
        "type": "HUMAN",
    }
    assert result["text"] == "Hello there"
    assert result["attachment_count"] == 1


def test_space_envelope_exposes_dm_peer():
    result = space_envelope(
        {"name": "spaces/AAA", "spaceType": "DIRECT_MESSAGE"},
        {"name": "users/123", "displayName": "Ada Lovelace", "email": "ada@example.com"},
    )

    assert result["is_direct_message"] is True
    assert result["peer"]["name"] == "Ada Lovelace"


def test_chat_listings_default_to_enriched_people():
    assert ListSpacesRequest().enrich_dms is True
    assert ListMessagesRequest(space_name="spaces/AAA").enrich_authors is True


def test_list_messages_by_thread_name_filters_through_the_real_client(monkeypatch):
    """The Chat API has no thread.name parameter; the thread goes into filter."""

    real = build("chat", "v1", credentials=AnonymousCredentials(), static_discovery=True, cache_discovery=False)
    sent: list[str] = []

    async def execute(request):
        sent.append(request.uri)
        return {"messages": []}

    async def timezone():
        return "UTC"

    monkeypatch.setattr(chat_tools, "chat_service", lambda: real)
    monkeypatch.setattr(chat_tools, "execute_google_request", execute)
    monkeypatch.setattr(chat_tools, "resolve_user_timezone", timezone)

    async def scenario():
        async with Client(chat_mcp) as client:
            return await client.call_tool(
                "list_messages",
                {
                    "request": {
                        "space_name": "spaces/AAAA",
                        "thread_name": "spaces/AAAA/threads/BBBB",
                        "filter": 'create_time > "2026-01-01T00:00:00Z"',
                    }
                },
                raise_on_error=False,
            )

    result = anyio.run(scenario)
    assert result.is_error is False, result.content
    (uri,) = sent
    assert parse_qs(urlparse(uri).query)["filter"] == [
        'create_time > "2026-01-01T00:00:00Z" AND thread.name = spaces/AAAA/threads/BBBB'
    ]
