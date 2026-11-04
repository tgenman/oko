"""Focused persistence capabilities for source and processor configuration."""
from typing import TYPE_CHECKING, Any

from oko.processing.execution import CodexSettings
from oko.processing.llamacpp import ProcessorConfig
from oko.subscriptions.history import InitialHistory

if TYPE_CHECKING:
    from oko.storage.store import Store


class TelegramConnections:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def owners(self, claims: list[tuple[str, str]]) -> list[str]:
        return self._store.subscriptions.telegram.owners(claims)

    def duplicates(self, feeds: list[str]) -> dict[str, Any]:
        return self._store.subscriptions.telegram.duplicates(feeds)

    def supports_access_change(self, feed: str) -> bool:
        return self._store.publications.feed(feed)

    def change_access(self, feed: str, binding: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
        if not self._store.db.in_transaction:
            raise RuntimeError('Connection changes require an owning transaction')
        return self._store.change_telegram_access(feed, binding, info)

    def connect(self, binding: dict[str, Any], info: dict[str, Any], source: str, folder: str,
                history: InitialHistory | None) -> dict[str, Any]:
        if not self._store.db.in_transaction:
            raise RuntimeError('Connection changes require an owning transaction')
        return self._store.connect_telegram(binding, info, source, folder, history)


class ProcessorConnections:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def configure(self, config: ProcessorConfig) -> bool:
        if not self._store.db.in_transaction:
            raise RuntimeError('Processor configuration requires an owning transaction')
        return self._store.configure_processor(config)

    def configure_codex(self, config: CodexSettings) -> None:
        if not self._store.db.in_transaction:
            raise RuntimeError('Processor configuration requires an owning transaction')
        self._store.configure_codex(config)

    def codex_config(self) -> CodexSettings | None:
        return self._store.codex_config()
