"""Boundary tests for the core/Telegram config split (core-isolation item 3a).

These prove the frontend-agnostic core is importable with ZERO Telegram
environment, that ``telegram_config`` has no import-time side effects, and that
every symbol now lives in exactly one half of the split.
"""

from __future__ import annotations

from pathlib import Path

from tests.seam_support import FRONTEND_MODULES, run_python


def _assert_no_frontends_loaded(script: str) -> None:
    probe = (
        f"{script}\n"
        "import sys\n"
        f"hits = [m for m in {FRONTEND_MODULES!r} if m in sys.modules]\n"
        "print(','.join(sorted(hits)))\n"
    )
    result = run_python(probe, env={"ALLOWED_CHAT_IDS": ""})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_import_core_config_without_allow_list() -> None:
    result = run_python("import core_config", env={"ALLOWED_CHAT_IDS": ""})
    assert result.returncode == 0, result.stderr


def test_import_services_composition_without_allow_list() -> None:
    result = run_python("import services.composition", env={"ALLOWED_CHAT_IDS": ""})
    assert result.returncode == 0, result.stderr


def test_services_composition_does_not_load_telegram() -> None:
    _assert_no_frontends_loaded("import services.composition")


def test_import_telegram_config_without_allow_list() -> None:
    """The view module imports cleanly; the fail-fast is explicit, not import-time."""
    result = run_python("import telegram_config", env={"ALLOWED_CHAT_IDS": ""})
    assert result.returncode == 0, result.stderr


def test_core_modules_do_not_load_telegram() -> None:
    for module in (
        "core_config",
        "services.chat_view",
        "services.prompt_service",
        "services.session_service",
        "services.settings_service",
        "formatting.markdown",
    ):
        _assert_no_frontends_loaded(f"import {module}")


def test_load_telegram_config_returns_typed_config() -> None:
    script = (
        "from telegram_config import load_telegram_config\n"
        "cfg = load_telegram_config()\n"
        "print(cfg.allowed_chat_ids)\n"
        "print(cfg.max_message_length)\n"
    )
    result = run_python(script, env={"ALLOWED_CHAT_IDS": "111,222"})
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "(111, 222)"
    assert lines[1] == "4000"


def test_symbols_live_in_the_right_half() -> None:
    script = (
        "import core_config, telegram_config\n"
        "core = ['BASE_DIR', 'OPENCODE_BACKEND', 'OPENCODE_WORKDIR',\n"
        "        'OPENCODE_TIMEOUT', 'OPENCODE_CMD', 'OPENCODE_SERVER_HOST',\n"
        "        'OPENCODE_SERVER_PORT', 'OPENCODE_SERVER_PASSWORD',\n"
        "        'OPENCODE_SERVER_BINARY', 'OPENCODE_SERVER_ALLOW_REMOTE',\n"
        "        'OPENCODE_PERMISSION_TIMEOUT', 'OPENAI_API_KEY', 'DEFAULT_MODEL',\n"
        "        'SESSIONS_PATH', 'DEFAULT_SESSION_NAME', 'PROGRESS_UPDATE_INTERVAL',\n"
        "        'INTERNAL_SUBPROCESS_TIMEOUT', 'EXPLORE_DB', 'setup_logger',\n"
        "        'resolve_model']\n"
        "view = ['TELEGRAM_BOT_TOKEN', 'ALLOWED_CHAT_IDS',\n"
        "        'TELEGRAM_MAX_MESSAGE_LENGTH', 'CONTAINER_KEY',\n"
        "        'CONNECTIVITY_CHECK_INTERVAL', 'CONNECTIVITY_FIRST_CHECK_DELAY',\n"
        "        'load_telegram_config']\n"
        "missing_core = [n for n in core if not hasattr(core_config, n)]\n"
        "missing_view = [n for n in view if not hasattr(telegram_config, n)]\n"
        "leaked_view = [n for n in view if hasattr(core_config, n)]\n"
        "print(missing_core, missing_view, leaked_view)\n"
    )
    result = run_python(script, env={"ALLOWED_CHAT_IDS": "111"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[] [] []"


def test_dangling_minimax_aliases_are_gone() -> None:
    """MD-06: the absent MiniMax M2.7 aliases no longer resolve."""
    from core_config import DEFAULT_MODEL, MODEL_ALIASES, resolve_model

    minimax = MODEL_ALIASES["minimax"]
    for dangling in ("m27", "m27-fast", "minimax-m27", "minimax-m27-highspeed"):
        assert dangling not in minimax
        assert resolve_model(dangling) == dangling

    assert resolve_model("flash") == "deepseek/deepseek-flash"
    assert resolve_model("m3") == "minimax/MiniMax-M3"
    assert DEFAULT_MODEL == "deepseek/deepseek-flash"


def test_flet_main_imports_without_telegram_vars(tmp_path: Path) -> None:
    """Prove the Flet entry point does not depend on any Telegram config."""
    env = {
        "ALLOWED_CHAT_IDS": "",
        "BOT_LOG_FILE": str(tmp_path / "flet-import.log"),
    }
    result = run_python("import flet_app.main", env=env)
    assert result.returncode == 0, result.stderr
