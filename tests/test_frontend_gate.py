"""Fase C: tests for the FrontendGate seam (``services/frontend_gate.py``).

Covers:
  - ``AllowAllGate`` allows every conversation id shape (int, str, empty str).
  - ``AllowAllGate`` structurally satisfies the ``FrontendGate`` Protocol —
    exercised through a function annotated with the Protocol, so mypy proves
    the conformance and the test runs the same call path.
  - The module is PTB-free (subprocess check).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from frontends.telegram.gate import TelegramGate
from services.conversation import ConversationKey
from services.frontend_gate import (
    AllowAllGate,
    FrontendGate,
    authorize_request,
    finish_request,
)
from utils.logging import request_id_var

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── v1 policy ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "conversation_id",
    [
        ConversationKey("telegram:123"),
        ConversationKey("flet:local"),
        ConversationKey(""),
    ],
)
def test_allow_all_gate_allows_any_conversation_key(
    conversation_id: ConversationKey,
) -> None:
    assert AllowAllGate().is_allowed(conversation_id) is True


# ── Protocol conformance ──────────────────────────────────────────────


def _accepts_gate(gate: FrontendGate, conversation_id: ConversationKey) -> bool:
    """Typing-level conformance: mypy requires the Protocol shape here."""
    return gate.is_allowed(conversation_id)


def test_allow_all_gate_satisfies_frontend_gate_protocol() -> None:
    gate = AllowAllGate()

    assert _accepts_gate(gate, ConversationKey("flet:local")) is True
    assert isinstance(gate, FrontendGate)


# ── TelegramGate ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("telegram:123", True),
        ("telegram:-456", True),
        ("telegram:999", False),
        ("flet:local", False),
        ("flet:123", False),
        ("123", False),
        ("telegram:not-a-number", False),
        ("telegram:", False),
    ],
)
def test_telegram_gate_allows_only_listed_telegram_keys(key: str, expected: bool) -> None:
    gate = TelegramGate([123, -456])

    assert gate.is_allowed(ConversationKey(key)) is expected


def test_telegram_gate_satisfies_frontend_gate_protocol() -> None:
    gate = TelegramGate([123])

    assert _accepts_gate(gate, ConversationKey("telegram:123")) is True
    assert isinstance(gate, FrontendGate)


# ── Shared authorize/finish helper ────────────────────────────────────


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [str(record.__dict__.get("event")) for record in caplog.records]


def test_authorize_request_allows_logs_entry_and_mints_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="opencode_bot.services.frontend_gate")
    request_id_var.set("-")

    allowed = authorize_request(AllowAllGate(), ConversationKey("flet:local"), "demo")

    assert allowed is True
    assert "handler_entry" in _events(caplog)
    assert request_id_var.get() != "-"


def test_authorize_request_denies_and_logs_unauthorized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="opencode_bot.services.frontend_gate")

    allowed = authorize_request(
        TelegramGate([123]),
        ConversationKey("telegram:999"),
        "demo",
    )

    assert allowed is False
    events = _events(caplog)
    assert "handler_entry" in events
    assert "unauthorized" in events

    record = next(r for r in caplog.records if r.__dict__.get("event") == "unauthorized")
    assert record.levelno == logging.WARNING
    assert record.__dict__["handler"] == "demo"
    assert str(record.__dict__["chat_id_masked"]).startswith("telegram:")


def test_finish_request_logs_handler_exit_with_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="opencode_bot.services.frontend_gate")

    finish_request("demo", ConversationKey("telegram:123"), time.monotonic() - 0.01)

    record = next(r for r in caplog.records if r.__dict__.get("event") == "handler_exit")
    assert record.__dict__["handler"] == "demo"
    assert isinstance(record.__dict__["duration_ms"], float)
    assert record.__dict__["duration_ms"] >= 0


# ── PTB-free guarantee ────────────────────────────────────────────────


def test_frontend_gate_module_is_ptb_free() -> None:
    """Importing the gate in a clean interpreter must not pull in ``telegram``.

    Uses a subprocess because this test session already imports PTB (other
    test modules), which would otherwise pollute ``sys.modules``.
    """
    script = (
        "import services.frontend_gate, sys; "
        "print('PTB_FREE=' + str('telegram' not in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=_PROJECT_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert "PTB_FREE=True" in result.stdout
