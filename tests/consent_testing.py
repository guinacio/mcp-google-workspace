"""Unit-test helpers: run captured tool functions behind the real consent gate.

Tool modules no longer ask for consent themselves; the confirmation guard
(``common/confirmation.py``) classifies each call with the consent table and
asks before the body runs. Tests that capture a tool's raw function wrap it
with :func:`gated` and answer the question with :func:`answer_consent`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_google_workspace.common import confirmation
from mcp_google_workspace.common.confirmation import install_confirmation_guard
from mcp_google_workspace.common.consent import CONSENT_TABLE, ToolPolicy, Trigger


def gated(function: Callable[..., Any], published_name: str) -> Callable[..., Any]:
    """``function`` behind the guard every registered tool gets."""
    component = SimpleNamespace(fn=function)
    install_confirmation_guard(component, published_name, published_name=published_name)
    return component.fn  # type: ignore[no-any-return]


def gated_tools(tools: dict[str, Callable[..., Any]], namespace: str) -> dict[str, Callable[..., Any]]:
    """Captured ``{local name: function}`` with each function gated under its published name."""
    return {name: gated(function, f"{namespace}_{name}") for name, function in tools.items()}


class ConsentAnswers:
    """Stand-in for the elicitation adapter: records each question, answers ``answer``."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    async def __call__(self, ctx: Any, action: str, message: str, **_kwargs: Any) -> bool:
        self.asked.append((action, message))
        return self.answer


def answer_consent(monkeypatch: pytest.MonkeyPatch, answer: bool) -> ConsentAnswers:
    """Answer every consent question of the test with ``answer``."""
    answers = ConsentAnswers(answer)
    monkeypatch.setattr(confirmation, "confirm_destructive_action", answers)
    return answers


# Policies for synthetic tools that test servers register: every tool the
# annotations reach must be classified (common/consent.py CONSENT_TABLE).
WRITE = ToolPolicy("write")


def asks(action: str, preview: Callable[[Mapping[str, Any]], str], *, explicit: bool = False) -> ToolPolicy:
    """A synthetic tool every call of which needs consent, previewed by ``preview``."""

    async def render(arguments: Mapping[str, Any], _action: str | None) -> str:
        return preview(arguments)

    return ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger(action, lambda _arguments: True, "always", explicit_confirm=explicit),),
        preview=render,
    )


def classify_tools(monkeypatch: pytest.MonkeyPatch, policies: Mapping[str, ToolPolicy]) -> None:
    """Add ``policies`` to the consent table for the test (restored afterwards)."""
    for name, policy in policies.items():
        monkeypatch.setitem(CONSENT_TABLE, name, policy)
