"""Tests for the direct (SQLite) OpenCode history reader.

Covers :mod:`opencode.db` (the reader itself) and the ``direct=True`` path of
:class:`opencode.sessions.OpenCodeSessions`, including the CLI fallback when the
database cannot be opened.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from opencode.client import OpenCodeDBError
from opencode.db import default_db_path, query_opencode_db_direct, read_sql_sync
from opencode.sessions import (
    OpenCodeSessions,
    _build_get_session_sql,
    _build_list_sql,
    _build_messages_sql,
)

_SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_id TEXT,
    directory TEXT,
    title TEXT,
    agent TEXT,
    model TEXT,
    time_created INTEGER,
    time_updated INTEGER
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT
);
"""


def _make_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO session (id, parent_id, directory, title, agent, "
            "time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ses_abc", None, "C:/project", "Mi sesión", "build", 1000, 2000),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?)",
            ("msg_1", "ses_abc", 100, 100, '{"role":"user"}'),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("part_1", "msg_1", "ses_abc", 100, 100, '{"type":"text","text":"Hola"}'),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_read_sql_sync_returns_rows(tmp_path: Path) -> None:
    db_file = _make_db(tmp_path / "opencode.db")

    rows = read_sql_sync(_build_list_sql("C:/project"), db_file)

    assert len(rows) == 1
    assert rows[0]["real_id"] == "ses_abc"
    assert rows[0]["title"] == "Mi sesión"


def test_read_sql_sync_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(OpenCodeDBError):
        read_sql_sync(_build_list_sql("C:/project"), tmp_path / "nope.db")


def test_read_sql_sync_does_not_modify_the_db(tmp_path: Path) -> None:
    db_file = _make_db(tmp_path / "opencode.db")

    with pytest.raises(OpenCodeDBError):
        read_sql_sync("DELETE FROM session", db_file)


async def test_query_opencode_db_direct_runs_off_the_loop(tmp_path: Path) -> None:
    db_file = _make_db(tmp_path / "opencode.db")

    rows = await query_opencode_db_direct(_build_messages_sql("ses_abc"), db_path=db_file)

    assert [row["text"] for row in rows] == ["Hola"]


def test_default_db_path_honors_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_DB_PATH", "C:/custom/opencode.db")

    assert default_db_path() == Path("C:/custom/opencode.db")


async def test_open_sessions_direct_reads_dtos(tmp_path: Path) -> None:
    db_file = _make_db(tmp_path / "opencode.db")
    sessions = OpenCodeSessions("C:/project", direct=True, db_path=db_file)

    summaries = await sessions.list_sessions()
    messages = await sessions.get_messages("ses_abc")
    count = await sessions.count_sessions()
    summary = await sessions.get_session("ses_abc")

    assert [item.real_id for item in summaries] == ["ses_abc"]
    assert [message.text for message in messages] == ["Hola"]
    assert count == 1
    assert summary is not None and summary.title == "Mi sesión"

    assert _build_get_session_sql("ses_abc")  # sanity: builder import is used


async def test_open_sessions_direct_falls_back_to_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    async def fake_query(sql: str, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append(sql)
        return [
            {
                "real_id": "ses_abc",
                "title": "Desde CLI",
                "created_at": 1,
                "updated_at": 2,
                "message_count": 0,
                "directory": "C:/project",
                "agent": "build",
            }
        ]

    monkeypatch.setattr("opencode.sessions.query_opencode_db", fake_query)
    sessions = OpenCodeSessions("C:/project", direct=True, db_path=tmp_path / "missing.db")

    summaries = await sessions.list_sessions()

    assert calls, "the CLI fallback must run when the DB is missing"
    assert summaries[0].title == "Desde CLI"
