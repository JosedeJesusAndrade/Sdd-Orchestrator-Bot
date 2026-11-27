"""Neutral-Spanish copy owned by the Flet UI (TX-01 / TX-02 / TX-03).

This module is pure: it holds only constants and one lookup helper, imports no
``flet`` and performs no I/O. Components read the copy from here so no literal
lives in ``flet_app/data/**`` and the strings can be asserted byte-for-byte.
"""

from __future__ import annotations

from locales.es import CONFIG_WORKDIR_FAILURE_COPY
from services.workdir_failures import WorkdirFailureKind

# ---- Drawer date buckets (SH-04 / TX-02) ------------------------------------
BUCKET_TODAY = "Hoy"
BUCKET_YESTERDAY = "Ayer"
BUCKET_LAST_7_DAYS = "Últimos 7 días"
BUCKET_LAST_30_DAYS = "Últimos 30 días"
BUCKET_OLDER = "Más antiguas"

# ---- Drawer options (SH-03 / TX-02) ----------------------------------------
OPTION_NEW_CHAT = "Nuevo chat"
OPTION_EXPLORER = "Explorador de directorios"
OPTION_SETTINGS = "Ajustes"

# ---- Toasts (SH-03 / TX-02) -------------------------------------------------
TOAST_COMING_SOON = "Próximamente"

# ---- Sessions list empty/error states --------------------------------------
SESSIONS_EMPTY = "No hay sesiones todavía."
SESSIONS_ERROR = "No se pudieron cargar las sesiones."

# ---- Chat empty state (welcome greeting) -----------------------------------
CHAT_EMPTY = "¿Qué vamos a construir hoy?"

# ---- Model picker (MP-03 / TX-02) ------------------------------------------
MODELS_EMPTY = "No hay modelos disponibles."

# ---- Explorer: non-git confirmation (EX-03) --------------------------------
NON_GIT_TITLE = "Seleccionar carpeta"
NON_GIT_PROMPT = "Esta carpeta no es un repositorio Git. ¿Desea trabajar de todas formas?"
NON_GIT_CONTINUE = "Continuar"
NON_GIT_CANCEL = "Cancelar"

# ---- Explorer: chrome, CRUD dialogs and CTA (EX-01 / EX-02) -----------------
EXPLORER_TITLE = "Explorador de directorios"
EXPLORER_BACK = "Volver al chat"
EXPLORER_USE_FOLDER = "Usar esta carpeta"
EXPLORER_NEW_FOLDER = "Nueva carpeta"
EXPLORER_RENAME = "Renombrar"
EXPLORER_DELETE = "Eliminar"
EXPLORER_RENAME_TITLE = "Renombrar carpeta"
EXPLORER_DELETE_TITLE = "Eliminar carpeta"
EXPLORER_DELETE_PROMPT = '¿Eliminar la carpeta "{name}"? Solo se elimina si está vacía.'
EXPLORER_FOLDER_NAME = "Nombre de la carpeta"
EXPLORER_INIT_GIT = "Inicializar repositorio Git"
EXPLORER_EMPTY = "Esta carpeta está vacía."
EXPLORER_GIT_BADGE = "git"
EXPLORER_RECENT = "Recientes"
EXPLORER_CREATE = "Crear"
EXPLORER_SAVE = "Guardar"
CANCEL = NON_GIT_CANCEL

# ---- Explorer: generic failure (EX-04 / TX-03) -----------------------------
GENERIC_WORKDIR_ERROR = "No se pudo acceder a esta carpeta."
RETRY = "Reintentar"

# ---- Edit mode: rename failure (EM-01) -------------------------------------
RENAME_FAILED = "No se pudo renombrar la conversación."

# ---- Workdir failure copy keyed by the enum (re-exported, TX-03) -----------
WORKDIR_FAILURE_COPY: dict[WorkdirFailureKind, str] = CONFIG_WORKDIR_FAILURE_COPY


def workdir_failure_copy(kind: WorkdirFailureKind) -> str:
    """Return the typed copy for ``kind`` or the generic workdir fallback."""
    return WORKDIR_FAILURE_COPY.get(kind, GENERIC_WORKDIR_ERROR)
