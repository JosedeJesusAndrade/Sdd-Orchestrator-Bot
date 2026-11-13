"""Boundary proof for core-isolation item 1 (Phase 2).

``services/`` is the frontend-agnostic core: importing any of its modules must
leave ``telegram``/``flet``/``frontends`` out of ``sys.modules``, and the
package source must contain no ``telegram`` or ``frontends`` import at all.
``frontends.telegram`` is the adapter layer and is expected to load Telegram.
"""

from __future__ import annotations

from pathlib import Path

from tests.seam_support import probe_import

_SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"


def test_frontends_telegram_loads_telegram() -> None:
    returncode, loaded = probe_import("frontends.telegram")

    assert returncode == 0
    assert "telegram" in loaded


def test_core_modules_are_ptb_free() -> None:
    for module in (
        "services.composition",
        "services.chat_view",
        "services.prompt_service",
        "services.session_service",
        "services.settings_service",
        "services.workdir_service",
        "services.session_commands",
        "formatting.markdown",
    ):
        returncode, loaded = probe_import(module)
        assert returncode == 0, module
        assert loaded == set(), f"{module} imported {sorted(loaded)}"


def test_services_package_imports_no_telegram_or_frontends() -> None:
    offenders: list[str] = []
    for path in sorted(_SERVICES_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import telegram", "from telegram")):
                offenders.append(f"{path}:{lineno}: {stripped}")
            if stripped.startswith(("import frontends", "from frontends")):
                offenders.append(f"{path}:{lineno}: {stripped}")

    assert offenders == [], "services/ must stay frontend-agnostic:\n" + "\n".join(offenders)
