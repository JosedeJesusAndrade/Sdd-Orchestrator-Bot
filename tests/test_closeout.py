"""P7 closeout: shell wiring guards and the ``<100ms`` structural invariants.

These pin the entry-point wiring that is otherwise only reachable through a live
Flet session: the resize hook that keeps the 100%-wide drawer in step, the status
refresh icon that also refreshes the sessions, and the hard "never block the loop"
rule (``page.run_thread`` is never called). The behavioral drawer guards live in
``test_drawer.py``; the controller guards in ``test_flet_data_controllers.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FLET_DIR = _ROOT / "flet_app"
_MAIN = _FLET_DIR / "main.py"


def _main_source() -> str:
    return _MAIN.read_text(encoding="utf-8")


def test_main_tracks_window_resize() -> None:
    """The page resize hook keeps the drawer width/height on the page size."""
    source = _main_source()

    assert "page.on_resize = on_resize" in source
    assert "drawer.set_width" in source
    assert "drawer.set_height" in source


def test_status_refresh_icon_refreshes_sessions_and_status() -> None:
    """The refresh icon fans out to ``refresh_all`` (sessions + strip)."""
    source = _main_source()

    block = re.search(
        r"def on_status_refresh\(\) -> None:.*?(?=\n    async def refresh_all)",
        source,
        re.DOTALL,
    )
    assert block is not None, "on_status_refresh must sit before refresh_all"
    assert "page.run_task(refresh_all)" in block.group(0)


def test_flet_app_never_calls_run_thread() -> None:
    """FD-05/SH-05: the app is single-loop; ``page.run_thread`` is never called."""
    offenders: list[str] = []
    for path in sorted(_FLET_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"\.run_thread\s*\(", line):
                offenders.append(f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}")

    assert offenders == [], "no page.run_thread in flet_app:\n" + "\n".join(offenders)
