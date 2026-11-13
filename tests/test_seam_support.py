"""Self-tests for the shared subprocess seam helper (``tests/seam_support.py``).

Later core-isolation phases use ``probe_import`` to assert that the core never
drags ``telegram``/``flet``/``frontends`` into ``sys.modules``. These tests prove
the helper itself reports correctly on today's code.

After the config split (item 3a) the probe targets ``core_config`` — the
frontend-agnostic half — and the fail-fast is triggered explicitly via
``load_telegram_config`` rather than by importing a module.
"""

from __future__ import annotations

from tests.seam_support import probe_import, run_python


def test_probe_reports_core_config_as_frontend_free() -> None:
    returncode, loaded = probe_import("core_config")

    assert returncode == 0
    assert loaded == set()


def test_probe_detects_telegram_when_it_is_imported() -> None:
    returncode, loaded = probe_import("telegram")

    assert returncode == 0
    assert "telegram" in loaded


def test_run_python_injects_a_valid_allow_list_by_default() -> None:
    result = run_python("import telegram_config; print(len(telegram_config.ALLOWED_CHAT_IDS))")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_run_python_can_override_the_allow_list_to_trigger_failfast() -> None:
    script = "from telegram_config import load_telegram_config; load_telegram_config()"
    result = run_python(script, env={"ALLOWED_CHAT_IDS": ""})

    assert result.returncode == 1
    assert "ALLOWED_CHAT_IDS inválido" in result.stderr
