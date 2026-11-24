"""Direct read-only access to the OpenCode SQLite database.

The CLI adapter (:func:`opencode.client.query_opencode_db`) is convenient but
boots a Node process per call (~2 s on this machine). The history seam only
READS, so we can talk to the very same SQLite file directly and answer in
milliseconds. Each query opens a short-lived ``mode=ro`` connection and closes
it, so a concurrent writer (the OpenCode server/CLI) is never disturbed and we
always see the last committed snapshot.

If the database cannot be opened (missing file, permissions, a different data
directory) the caller falls back to the CLI; see :class:`opencode.sessions`.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from opencode.client import OpenCodeDBError

#: Override the SQLite file location (tests / custom installs).
DB_PATH_ENV = "OPENCODE_DB_PATH"


def default_db_path() -> Path:
    """Return the OpenCode SQLite file, honoring ``OPENCODE_DB_PATH``.

    Mirrors the CLI's default data directory: ``$XDG_DATA_HOME/opencode`` when
    set, else ``~/.local/share/opencode`` (the layout confirmed on Windows too).
    """
    override = os.getenv(DB_PATH_ENV)
    if override:
        return Path(override)
    xdg_data_home = os.getenv("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "opencode" / "opencode.db"


def read_sql_sync(sql: str, db_path: Path) -> list[dict[str, Any]]:
    """Execute one read-only query against ``db_path`` and return row dicts.

    Raises :class:`OpenCodeDBError` when the database is missing or the query
    fails, so callers can decide to fall back to the CLI.
    """
    if not db_path.exists():
        raise OpenCodeDBError(f"OpenCode database not found: {db_path}")

    uri = f"file:{quote(db_path.as_posix(), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:  # pragma: no cover - depends on the filesystem
        raise OpenCodeDBError(f"cannot open OpenCode database {db_path}: {exc}") from exc

    try:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(sql)
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        raise OpenCodeDBError(f"OpenCode database query failed: {exc}") from exc
    finally:
        connection.close()


async def query_opencode_db_direct(
    sql: str, *, db_path: Path | None = None
) -> list[dict[str, Any]]:
    """Async wrapper around :func:`read_sql_sync` (runs off the event loop)."""
    path = db_path if db_path is not None else default_db_path()
    return await asyncio.to_thread(read_sql_sync, sql, path)
