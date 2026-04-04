from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

from mcp_google_workspace.chat.client import (
    _describe_http_error,
    normalize_message_name,
    normalize_space_name,
    normalize_user_name,
)


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
