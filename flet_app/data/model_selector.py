"""Model selector data: provider grouping, empty/degraded state, selection (FD-07).

``load()`` returns the ``ModelCatalogSnapshot`` (already grouped by provider and
with ``is_default`` recomputed per conversation by the core catalog). The
empty/degraded case is a valid snapshot, not an error. Selecting a model
persists it through the model accessor and reloads, so ``is_default`` moves.

No copy (e.g. "No hay modelos disponibles") lives here — ``flet-ui`` owns it.
"""

from __future__ import annotations

from typing import Protocol

from flet_app.data.state import Loadable, LoadEmpty, LoadReady, LoadState
from services.conversation import ConversationKey
from services.model_catalog import ModelCatalogSnapshot


class ModelCatalogPort(Protocol):
    """The subset of ``OpenCodeModelCatalog`` the selector reads."""

    async def list(self, key: ConversationKey) -> ModelCatalogSnapshot: ...


class ModelPreferencePort(Protocol):
    """The model accessor the selector persists through."""

    async def set_model(self, key: ConversationKey, model: str) -> None: ...


class ModelSelectorController(Loadable[ModelCatalogSnapshot]):
    """Load and persist the model selector's data."""

    def __init__(
        self,
        catalog: ModelCatalogPort,
        preferences: ModelPreferencePort,
        key: ConversationKey,
    ) -> None:
        super().__init__()
        self._catalog = catalog
        self._preferences = preferences
        self._key = key

    async def _fetch(
        self, target: ConversationKey
    ) -> tuple[ModelCatalogSnapshot, LoadState[ModelCatalogSnapshot]]:
        snapshot = await self._catalog.list(target)
        state: LoadState[ModelCatalogSnapshot] = (
            LoadEmpty[ModelCatalogSnapshot]()
            if not snapshot.options
            else LoadReady[ModelCatalogSnapshot](snapshot)
        )
        return snapshot, state

    async def load(self, key: ConversationKey | None = None) -> ModelCatalogSnapshot | None:
        """Return the grouped catalog for ``key`` (defaults to the held key)."""
        target = self._key if key is None else key
        snapshot, _ = await self._load(lambda: self._fetch(target))
        return snapshot

    async def select(
        self, model_id: str, key: ConversationKey | None = None
    ) -> ModelCatalogSnapshot | None:
        """Persist ``model_id`` and reload so ``is_default`` reflects it."""
        target = self._key if key is None else key
        try:
            await self._preferences.set_model(target, model_id)
        except Exception as exc:
            self._fail(exc)
            return None
        return await self.load(target)
