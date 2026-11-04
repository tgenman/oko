"""Collection operations using separate, narrow persistence capabilities."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from oko.collection.contracts import Material, Publication, SourceError
from oko.domain.collection import CollectionEffects
from oko.storage.collection_state import SourceConfiguration
from oko.services.access import repository_access


class PublicationFacts(Protocol):
    def unviewed(self, item: str) -> bool: ...
    def feedless(self, item: str) -> bool: ...
    def publication(self, item: str) -> Publication | None: ...
    def feed(self, item: str) -> str: ...
    def source_removed(self, item: str) -> None: ...
    def viewed(self, item: str) -> None: ...


class CollectedPublications(Protocol):
    def accept(self, item: str, material: Material, *, manual: bool = False) -> tuple[SourceError | None, CollectionEffects]: ...


class SourceFacts(Protocol):
    def publication_revision(self, item: str) -> str: ...
    def revision(self) -> int: ...
    def configuration(self) -> SourceConfiguration: ...
    def binding(self, feed: str) -> dict[str, Any] | None: ...
    def refresh_identity(self, feed: str, info: dict[str, Any]) -> None: ...
    def cursor(self, feed: str) -> str | None: ...
    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None: ...
    def feed_status(self, feed: str, **state: Any) -> None: ...
    def caption_ready(self) -> float: ...
    def caption_config(self) -> dict[str, str]: ...
    def caption_started(self, wall: float) -> None: ...
    def caption_blocked(self, wall: float, delay: float) -> None: ...
    def caption_completed(self) -> None: ...
    def telegram_http_delay(self) -> float: ...
    def startup(self, injected: bool, source_feed: str) -> float: ...


class CollectionService:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]],
                 publications: Callable[[], PublicationFacts], sources: Callable[[], SourceFacts],
                 collected: Callable[[], CollectedPublications]) -> None:
        self._access, self._publications, self._sources, self._collected = access, publications, sources, collected

    @repository_access
    def collected(self, item: str, material: Material, *, manual: bool) -> tuple[SourceError | None, CollectionEffects]:
        return self._collected().accept(item, material, manual=manual)

    @repository_access
    def revision(self, item: str) -> str:
        return self._sources().publication_revision(item)

    @repository_access
    def current(self, item: str, revision: str) -> bool:
        return self._publications().unviewed(item) and self._sources().publication_revision(item) == revision

    @repository_access
    def feedless(self, item: str) -> bool:
        return self._publications().feedless(item)

    @repository_access
    def publication(self, item: str) -> Publication | None:
        return self._publications().publication(item)

    @repository_access
    def feed(self, item: str) -> str:
        return self._publications().feed(item)

    @repository_access
    def source_removed(self, item: str) -> None:
        self._publications().source_removed(item)

    @repository_access
    def feed_status(self, /, feed: str, **state: Any) -> None:
        self._sources().feed_status(feed, **state)

    @repository_access
    def viewed(self, item: str) -> None:
        self._publications().viewed(item)

    @repository_access
    def source_revision(self) -> int:
        return self._sources().revision()

    @repository_access
    def configuration(self) -> SourceConfiguration:
        return self._sources().configuration()

    @repository_access
    def binding(self, feed: str) -> dict[str, Any] | None:
        return self._sources().binding(feed)

    @repository_access
    def refresh_identity(self, feed: str, info: dict[str, Any]) -> None:
        self._sources().refresh_identity(feed, info)

    @repository_access
    def cursor(self, feed: str) -> str | None:
        return self._sources().cursor(feed)

    @repository_access
    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None:
        self._sources().discovery_wait(feed, label, retry_at)

    @repository_access
    def caption_ready(self) -> float:
        return self._sources().caption_ready()

    @repository_access
    def caption_config(self) -> dict[str, str]:
        return self._sources().caption_config()

    @repository_access
    def caption_started(self, wall: float) -> None:
        self._sources().caption_started(wall)

    @repository_access
    def caption_blocked(self, wall: float, delay: float) -> None:
        self._sources().caption_blocked(wall, delay)

    @repository_access
    def caption_completed(self) -> None:
        self._sources().caption_completed()

    @repository_access
    def telegram_http_delay(self) -> float:
        return self._sources().telegram_http_delay()

    @repository_access
    def startup(self, injected: bool, source_feed: str) -> float:
        return self._sources().startup(injected, source_feed)
