"""Byte-exact contract for the neutral-Spanish UI copy (TX-01/02/03).

Pins the strings components will render and proves the workdir failure map is
keyed by the enum with full coverage and a generic fallback.
"""

from __future__ import annotations

from typing import cast

from flet_app import ui_strings
from locales.es import CONFIG_WORKDIR_FAILURE_COPY
from services.workdir_failures import WorkdirFailureKind
from tests.seam_support import probe_import


def test_pinned_strings_are_byte_exact() -> None:
    assert ui_strings.BUCKET_TODAY == "Hoy"
    assert ui_strings.BUCKET_YESTERDAY == "Ayer"
    assert ui_strings.BUCKET_LAST_7_DAYS == "Últimos 7 días"
    assert ui_strings.BUCKET_LAST_30_DAYS == "Últimos 30 días"
    assert ui_strings.BUCKET_OLDER == "Más antiguas"

    assert ui_strings.OPTION_NEW_CHAT == "Nuevo chat"
    assert ui_strings.OPTION_EXPLORER == "Explorador de directorios"
    assert ui_strings.OPTION_SETTINGS == "Ajustes"

    assert ui_strings.TOAST_COMING_SOON == "Próximamente"
    assert ui_strings.MODELS_EMPTY == "No hay modelos disponibles."


def test_explorer_and_edit_mode_copy_is_byte_exact() -> None:
    assert ui_strings.NON_GIT_TITLE == "Seleccionar carpeta"
    assert (
        ui_strings.NON_GIT_PROMPT
        == "Esta carpeta no es un repositorio Git. ¿Desea trabajar de todas formas?"
    )
    assert ui_strings.NON_GIT_CONTINUE == "Continuar"
    assert ui_strings.NON_GIT_CANCEL == "Cancelar"
    assert ui_strings.GENERIC_WORKDIR_ERROR == "No se pudo acceder a esta carpeta."
    assert ui_strings.RETRY == "Reintentar"
    assert ui_strings.RENAME_FAILED == "No se pudo renombrar la conversación."


def test_workdir_failure_copy_covers_every_kind() -> None:
    missing = [kind for kind in WorkdirFailureKind if kind not in ui_strings.WORKDIR_FAILURE_COPY]
    assert missing == [], f"missing workdir copy for kinds: {missing}"


def test_workdir_failure_copy_is_keyed_by_enum_members() -> None:
    non_enum = [
        key for key in ui_strings.WORKDIR_FAILURE_COPY if not isinstance(key, WorkdirFailureKind)
    ]
    assert non_enum == [], f"copy keys must be WorkdirFailureKind members: {non_enum}"


def test_workdir_failure_copy_re_exports_the_locale_map() -> None:
    assert ui_strings.WORKDIR_FAILURE_COPY is CONFIG_WORKDIR_FAILURE_COPY


def test_workdir_failure_copy_lookup_uses_kind_and_falls_back() -> None:
    assert (
        ui_strings.workdir_failure_copy(WorkdirFailureKind.NOT_FOUND)
        == CONFIG_WORKDIR_FAILURE_COPY[WorkdirFailureKind.NOT_FOUND]
    )
    missing = cast(WorkdirFailureKind, "not_a_real_kind")
    assert ui_strings.workdir_failure_copy(missing) == ui_strings.GENERIC_WORKDIR_ERROR


def test_strings_module_is_flet_free() -> None:
    returncode, loaded = probe_import("flet_app.ui_strings")

    assert returncode == 0
    assert loaded == set()
