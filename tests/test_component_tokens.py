"""Orthogonality gates for the P3 components (token-only + no services).

Scans the component modules (including ``app_background``) and asserts they:

1. contain no hardcoded ``#RRGGBB`` colour literals (DS-02 token-only), and
2. import no ``services`` module at runtime — the only service references are
   ``TYPE_CHECKING`` type hints (pure presentational, callbacks only).

This is a source-level gate; the behavioural token assertions live in each
component's own test file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_COMPONENTS = (
    "sidebar_tile.py",
    "section_header.py",
    "search_placeholder.py",
    "footer_profile.py",
    "status_strip.py",
    "model_picker.py",
    "drawer.py",
    "app_background.py",
)

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
_COMPONENTS_DIR = Path(__file__).resolve().parents[1] / "flet_app" / "components"


def _source(name: str) -> str:
    return (_COMPONENTS_DIR / name).read_text(encoding="utf-8")


def _runtime_import_modules(tree: ast.Module) -> list[str]:
    """Top-level import modules, excluding ``if TYPE_CHECKING:`` blocks."""
    modules: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def test_components_have_no_hardcoded_hex_colors() -> None:
    for name in _COMPONENTS:
        assert not _HEX_COLOR.search(_source(name)), name


def test_components_import_no_services_at_runtime() -> None:
    for name in _COMPONENTS:
        modules = _runtime_import_modules(ast.parse(_source(name)))
        offending = [m for m in modules if m == "services" or m.startswith("services.")]
        assert not offending, (name, offending)


def test_components_use_the_design_system() -> None:
    for name in _COMPONENTS:
        modules = _runtime_import_modules(ast.parse(_source(name)))
        assert "flet_app.core.design_system" in modules, name
