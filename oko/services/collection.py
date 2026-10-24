"""Publication collection and source state, independent of attempts and adapters."""
from collections.abc import Callable
from oko.storage.read_sync import ReadPlan
from typing import Any
from oko.domain.collection import CollectionEffects
from oko.storage.store import Store
from oko.storage.collection_state import CollectionState, SourceConfiguration
from oko.storage.access import repository_access
from oko.storage import publication_links
from oko.collection.contracts import Material, Publication, SourceError
from oko.collection.adapters import ExternalKey


class CollectionService:
    def __init__(self, storage: Callable[[], Store]) -> None:
        self._storage=storage

    @repository_access
    def discovered(self, publications: list[Publication], cursor: str, feed: str, namespace: str, *, description: str | None,
                   successful: bool, source_order: tuple[ExternalKey,...]) -> CollectionEffects:
        return self._storage().collect_discovery(publications,cursor,feed,namespace,description=description,successful=successful,source_order=source_order)

    @repository_access
    def collected(self, item: str, material: Material, *, manual: bool) -> tuple[SourceError | None,CollectionEffects]:
        return self._storage().collect_material(item,material,manual=manual)

    @repository_access
    def revision(self, item: str) -> str:
        return CollectionState(self._storage()).publication_revision(item)

    @repository_access
    def current(self, item: str, revision: str) -> bool:
        return self._storage()._has_unviewed(item) and self.revision(item)==revision

    @repository_access
    def feedless(self, item: str) -> bool:
        return publication_links.feedless(self._storage(),item)

    @repository_access
    def publication(self, item: str) -> Publication | None:
        return self._storage().publication(item)

    @repository_access
    def feed(self, item: str) -> str:
        return self._storage().item_feed(item)

    @repository_access
    def source_removed(self, item: str) -> None:
        repository=self._storage()
        repository.mark_source_removed(item)

    @repository_access
    def feed_status(self, /, feed: str, **state: Any) -> None:
        self._storage().feed_status(feed,**state)

    @repository_access
    def viewed(self, item: str) -> None:
        self._storage().viewed(item)

    @repository_access
    def source_revision(self) -> int:
        return CollectionState(self._storage()).revision()

    @repository_access
    def configuration(self) -> SourceConfiguration:
        return CollectionState(self._storage()).configuration()

    @repository_access
    def binding(self, feed: str) -> dict[str,Any] | None:
        return CollectionState(self._storage()).binding(feed)

    @repository_access
    def profile(self, role: str) -> dict[str,Any] | None:
        return CollectionState(self._storage()).profile(role)

    @repository_access
    def refresh_identity(self, feed: str, info: dict[str,Any]) -> None:
        CollectionState(self._storage()).refresh_identity(feed,info)

    @repository_access
    def cursor(self, feed: str) -> str | None:
        return CollectionState(self._storage()).cursor(feed)

    @repository_access
    def notify_parents(self, item: str) -> None:
        CollectionState(self._storage()).notify_parents(item)

    @repository_access
    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None:
        CollectionState(self._storage()).discovery_wait(feed,label,retry_at)

    @repository_access
    def read_request(self, feed: str) -> dict[str,Any] | None:
        return CollectionState(self._storage()).read_request(feed)

    @repository_access
    def read_confirmed(self, feed: str) -> int:
        return CollectionState(self._storage()).read_confirmed(feed)

    @repository_access
    def read_signature(self, request: dict[str,Any]) -> str:
        return CollectionState(self._storage()).read_signature(request)

    @repository_access
    def read_plan(self, request: dict[str,Any], state: dict[str,Any], window: dict[str,Any] | None = None) -> ReadPlan:
        return CollectionState(self._storage()).read_plan(request,state,window)

    @repository_access
    def read_completed(self, feed: str, plan: ReadPlan, confirmed: int) -> None:
        CollectionState(self._storage()).read_completed(feed,plan,confirmed)

    @repository_access
    def read_failed(self, feed: str, label: str) -> None:
        CollectionState(self._storage()).read_failed(feed,label)

    @repository_access
    def caption_ready(self) -> float:
        return CollectionState(self._storage()).caption_ready()

    @repository_access
    def caption_config(self) -> dict[str,str]:
        return CollectionState(self._storage()).caption_config()

    @repository_access
    def caption_started(self, wall: float) -> None:
        CollectionState(self._storage()).caption_started(wall)

    @repository_access
    def caption_blocked(self, wall: float, delay: float) -> None:
        CollectionState(self._storage()).caption_blocked(wall,delay)

    @repository_access
    def caption_completed(self) -> None:
        CollectionState(self._storage()).caption_completed()

    @repository_access
    def telegram_http_delay(self) -> float:
        return float(self._storage().subscriptions.telegram_http_settings()['value'])

    @repository_access
    def startup(self, injected: bool, source_feed: str) -> float:
        return CollectionState(self._storage()).startup(injected,source_feed)
