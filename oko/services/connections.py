"""Persisted connection identity and configuration, without external waits."""

from oko.storage.access import repository_access
from collections.abc import Callable
from typing import Any
from oko.processing.execution import CodexSettings
from oko.processing.llamacpp import ProcessorConfig
from oko.storage.store import Store
from oko.subscriptions.history import InitialHistory

class ConnectionService:
    def __init__(self, repository: Callable[[], Store]) -> None:
        self._storage = repository

    @repository_access
    def owners(self, claims: list[tuple[str, str]], *, active_only: bool = False) -> list[str]:
        return self._storage().subscriptions.telegram.owners(claims, active_only=active_only)

    @repository_access
    def duplicates(self, feeds: list[str]) -> dict[str, Any]:
        return self._storage().subscriptions.telegram.duplicates(feeds)

    @repository_access
    def supports_access_change(self, feed: str) -> bool:
        return self._storage().publications.feed(feed)

    @repository_access
    def change_access(self, feed: str, binding: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
        return self._storage().change_telegram_access(feed, binding, info)

    @repository_access
    def connect(self, binding: dict[str, Any], info: dict[str, Any], source: str, folder: str, history: InitialHistory | None) -> dict[str, Any]:
        return self._storage().connect_telegram(binding,info,source,folder,history)

    @repository_access
    def configure_processor(self, config: ProcessorConfig) -> bool:
        return self._storage().configure_processor(config)

    @repository_access
    def configure_codex(self, config: CodexSettings) -> None:
        self._storage().configure_codex(config)

    @repository_access
    def codex_config(self) -> CodexSettings | None:
        return self._storage().codex_config()

    @repository_access
    def configure_youtube(self, value: Any) -> None:
        self._storage().youtube.configure(value)

    @repository_access
    def youtube_status(self, now: float) -> dict[str, Any]:
        return self._storage().youtube.status(now)
