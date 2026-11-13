"""Tests for the explicit ALLOWED_CHAT_IDS fail-fast (core-isolation item 3a).

The fail-fast moved OUT of import time. Merely importing ``telegram_config``
must succeed even with an empty/invalid allow-list; only the explicit
``load_telegram_config()`` call (owned by the entry points) exits with code 1.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_config import parse_allowed_chat_ids

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_parse_single_id() -> None:
    assert parse_allowed_chat_ids("123") == [123]


def test_parse_csv_with_whitespace() -> None:
    assert parse_allowed_chat_ids(" 1 , 2 ,3 ") == [1, 2, 3]


def test_parse_empty_returns_empty_list() -> None:
    assert parse_allowed_chat_ids("") == []
    assert parse_allowed_chat_ids("   ,  ") == []


def test_parse_non_numeric_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_allowed_chat_ids("abc")


def _run_with(raw: str, script: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ALLOWED_CHAT_IDS": raw,
        # Force the child to write UTF-8 regardless of the console code page
        # (cp1252 in some Windows shells). Without this, the accent in
        # "inválido" was encoded as cp1252 (0xE1) and the strict UTF-8 decode
        # below raised UnicodeDecodeError.
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


_IMPORT = "import telegram_config"
_LOAD = "from telegram_config import load_telegram_config; load_telegram_config()"


def test_import_telegram_config_with_invalid_value_is_deferred() -> None:
    """Importing the view must NEVER fail fast (no import-time SystemExit)."""
    result = _run_with("not-a-number", _IMPORT)
    assert result.returncode == 0, result.stderr


def test_import_telegram_config_with_empty_value_is_deferred() -> None:
    result = _run_with("", _IMPORT)
    assert result.returncode == 0, result.stderr


def test_non_numeric_env_value_exits_with_code_1() -> None:
    result = _run_with("not-a-number", _LOAD)
    assert result.returncode == 1
    # Fail-fast must stay visible even though setup_logger() was never called
    # (import has no logging side effects): the message goes to stderr.
    assert "ALLOWED_CHAT_IDS inválido" in result.stderr


def test_empty_env_value_exits_with_code_1() -> None:
    result = _run_with("", _LOAD)
    assert result.returncode == 1
    assert "ALLOWED_CHAT_IDS inválido" in result.stderr


def test_valid_env_value_loads_cleanly() -> None:
    result = _run_with("111,222", _LOAD)
    assert result.returncode == 0, result.stderr
