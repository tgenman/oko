"""Persist reader completion inside the application action's transaction."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.store import Store


class ReadingMutations:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def viewed(self, identity: str) -> bool:
        if not self._store.db.in_transaction:
            raise RuntimeError('Reader completion requires an owning transaction')
        return self._store.viewed(identity)

    def viewed_set(self, identities: list[str]) -> bool:
        if not self._store.db.in_transaction:
            raise RuntimeError('Reader completion requires an owning transaction')
        return self._store.viewed_set(identities)
