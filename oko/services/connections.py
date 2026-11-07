"""Persisted connection identity and configuration, without external waits."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from oko.processing.execution import CodexSettings
from oko.processing.config import ProcessorConfig
from oko.services.access import repository_access
from oko.subscriptions.history import InitialHistory


class TelegramConnectionRepository(Protocol):
    def owners(self, claims: list[tuple[str, str]], *, active_only: bool = False) -> list[str]: ...
    def duplicates(self, feeds: list[str]) -> dict[str, Any]: ...
    def supports_access_change(self, feed: str) -> bool: ...
    def change_access(self, feed: str, binding: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]: ...
    def connect(self, binding: dict[str, Any], info: dict[str, Any], source: str, folder: str,
                history: InitialHistory | None) -> dict[str, Any]: ...


class ProcessorConnectionRepository(Protocol):
    def configure(self, config: ProcessorConfig) -> bool: ...
    def configure_codex(self, config: CodexSettings) -> None: ...
    def codex_config(self) -> CodexSettings | None: ...


class YouTubeConnectionRepository(Protocol):
    def configure(self, value: Any) -> None: ...
    def status(self, now: float) -> dict[str, Any]: ...


class ConnectionService:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]],
                 telegram: Callable[[], TelegramConnectionRepository],
                 processor: Callable[[], ProcessorConnectionRepository],
                 youtube: Callable[[], YouTubeConnectionRepository]) -> None:
        self._access, self._telegram, self._processor, self._youtube = access, telegram, processor, youtube

    @repository_access
    def owners(self, claims: list[tuple[str, str]], *, active_only: bool = False) -> list[str]:
        return self._telegram().owners(claims, active_only=active_only)

    @repository_access
    def duplicates(self, feeds: list[str]) -> dict[str, Any]:
        return self._telegram().duplicates(feeds)

    @repository_access
    def supports_access_change(self, feed: str) -> bool:
        return self._telegram().supports_access_change(feed)

    @repository_access
    def change_access(self, feed: str, binding: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
        return self._telegram().change_access(feed, binding, info)

    @repository_access
    def connect(self, binding: dict[str, Any], info: dict[str, Any], source: str, folder: str, history: InitialHistory | None) -> dict[str, Any]:
        return self._telegram().connect(binding, info, source, folder, history)

    @repository_access
    def configure_processor(self, config: ProcessorConfig) -> bool:
        return self._processor().configure(config)

    @repository_access
    def configure_codex(self, config: CodexSettings) -> None:
        self._processor().configure_codex(config)

    @repository_access
    def codex_config(self) -> CodexSettings | None:
        return self._processor().codex_config()

    @repository_access
    def configure_youtube(self, value: Any) -> None:
        self._youtube().configure(value)

    @repository_access
    def youtube_status(self, now: float) -> dict[str, Any]:
        return self._youtube().status(now)
