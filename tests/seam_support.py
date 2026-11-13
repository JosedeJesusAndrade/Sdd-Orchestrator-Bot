"""Shared helper for the subprocess ``sys.modules`` seam tests.

Later core-isolation phases assert that importing a core module never drags
``telegram``/``flet``/``frontends`` into ``sys.modules``. This helper owns the
subprocess plumbing so every phase uses the exact same probe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Modules the core must never import.
FRONTEND_MODULES: tuple[str, ...] = ("telegram", "flet", "frontends")


def run_python(
    script: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter rooted at the project.

    ``ALLOWED_CHAT_IDS`` defaults to a valid value so importing ``config`` in
    the child does not fail-fast; callers can override it (for example to
    assert the fail-fast itself).
    """
    merged = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "ALLOWED_CHAT_IDS": "111",
    }
    if env is not None:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=merged,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def probe_import(module: str) -> tuple[int, set[str]]:
    """Import ``module`` in a subprocess; report frontend modules loaded.

    Returns ``(returncode, loaded)`` where ``loaded`` is the intersection of
    ``FRONTEND_MODULES`` with the child's ``sys.modules`` after the import.
    """
    script = (
        "import sys\n"
        f"import {module}\n"
        f"hits = [m for m in {FRONTEND_MODULES!r} if m in sys.modules]\n"
        "print(','.join(sorted(hits)))\n"
    )
    result = run_python(script)
    loaded = {name for name in result.stdout.strip().split(",") if name}
    return result.returncode, loaded
