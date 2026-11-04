"""Persistence effects of feed changes within the application transaction."""
from typing import TYPE_CHECKING, Any

from oko.storage import memberships
from oko.subscriptions.history import InitialHistory

if TYPE_CHECKING:
    from oko.storage.store import Store


class SubscriptionEffects:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def require_transaction(self) -> None:
        if not self._store.db.in_transaction:
            raise RuntimeError('Subscription changes require an owning transaction')

    def lock_feed_graph(self) -> None:
        self.require_transaction()
        memberships.lock_graph(self._store.db)

    def feed_added(self, identity: str) -> None:
        self.require_transaction()
        self._store.problems.sync_codex_feed_added(identity, self._store.wall_clock())

    def settings_changed(self, scope: str, owner: str) -> None:
        self.require_transaction()
        self._store.settings_changed(scope, owner)

    def remove_feed(self, identity: str) -> None:
        self.require_transaction()
        self._store.remove_feed(identity)

    def pause_history(self, identity: str, paused: bool) -> dict[str, Any]:
        self.require_transaction()
        return self._store.pause_history(identity, paused)

    def change_history(self, identity: str, selection: InitialHistory | None) -> dict[str, Any]:
        self.require_transaction()
        return self._store.change_history(identity, selection)

    def checkpoint(self, name: str) -> None:
        self.require_transaction()
        self._store.fault(name)
