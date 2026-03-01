import pytest

from mcp_google_workspace.chat.client import (
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
