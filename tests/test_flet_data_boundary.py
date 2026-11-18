"""Boundary proof for the Flet data layer (``flet-data-layer`` P6 / FD-01).

Two guarantees:
  1. static: ``flet_app/data/**`` imports no ``flet``/``telegram``/``frontends``;
  2. headless: every data module imports in a fresh interpreter without loading
     those packages, so the controllers are testable with no UI runtime.

It also pins FD-09: ``flet_app/main.py`` no longer writes the workdir at startup
and builds the four data controllers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.seam_support import probe_import

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "flet_app" / "data"
_MAIN = _ROOT / "flet_app" / "main.py"

# Matches ``import flet``/``from flet ...``/``import telegram``/``import frontends``
# but NOT ``flet_app`` (the package itself must stay importable).
_FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:flet|telegram|frontends)(?:\.|\s|$)",
    re.MULTILINE,
)

_DATA_MODULES = (
    "flet_app.data",
    "flet_app.data.state",
    "flet_app.data.sidebar",
    "flet_app.data.status_strip",
    "flet_app.data.model_selector",
    "flet_app.data.explorer",
)


def test_data_package_exists() -> None:
    assert (_DATA_DIR / "__init__.py").is_file()
    assert sorted(path.name for path in _DATA_DIR.glob("*.py")) == [
        "__init__.py",
        "explorer.py",
        "model_selector.py",
        "sidebar.py",
        "state.py",
        "status_strip.py",
    ]


def test_data_package_has_zero_frontend_imports() -> None:
    offenders: list[str] = []
    for path in sorted(_DATA_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _FORBIDDEN_IMPORT.finditer(source):
            lineno = source[: match.start()].count("\n") + 1
            offenders.append(f"{path}:{lineno}: {match.group(0).strip()}")

    assert offenders == [], "flet_app/data must stay UI-free:\n" + "\n".join(offenders)


@pytest.mark.parametrize("module", _DATA_MODULES)
def test_data_modules_import_headless(module: str) -> None:
    returncode, loaded = probe_import(module)

    assert returncode == 0, f"{module} failed to import headless"
    assert loaded == set(), f"{module} imported {sorted(loaded)}"


def test_main_does_not_write_the_workdir_at_startup() -> None:
    source = _MAIN.read_text(encoding="utf-8")

    assert not re.search(r"settings_service\.set\s*\(", source)
    assert not re.search(r"set_chat_setting\s*\(", source)
    assert "effective_workdir.resolve" in source


def test_main_builds_the_data_controllers() -> None:
    source = _MAIN.read_text(encoding="utf-8")

    for name in (
        "SidebarController",
        "StatusStripController",
        "ModelSelectorController",
        "ExplorerController",
    ):
        assert name in source, name
