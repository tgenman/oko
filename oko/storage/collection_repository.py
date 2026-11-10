"""Collection facts and writes under the shared storage lock."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from oko.collection.adapters import ExternalKey, ExternalReference, Platform, PublicationContent
from oko.collection.contracts import SourceError
from oko.collection.sources import SourceSettings, TelegramAccess
from oko.domain.collection import CollectionEffects
from oko.services.access import repository_access
from oko.storage import publication_links

if TYPE_CHECKING:
    from oko.storage.store import Store


@dataclass(frozen=True)
class SourceConfiguration:
    revision: int
    settings: list[SourceSettings]
    dependencies: list[str]
    invalid_history: list[str] = field(default_factory=list)


class CollectionRepository:
    def __init__(self, storage: Callable[[], 'Store']) -> None:
        self._storage = storage
        self._access: Callable[[], AbstractContextManager[Any]] = lambda: storage().db.access_lock

    @property
    def store(self) -> 'Store':
        return self._storage()

    @repository_access
    def collected_content(self, item: str, value: PublicationContent) -> tuple[SourceError | None, CollectionEffects]:
        return self.store.collected_publication.accept_content(item,value)

    @repository_access
    def current(self, item: str, revision: str) -> bool:
        return self.unviewed(item) and self.revision(item) == revision

    def _require_transaction(self) -> None:
        if not self.store.db.in_transaction:
            raise RuntimeError('Collection source changes require an owning transaction')

    @repository_access
    def feed_status(self, /, feed: str, **state: Any) -> None:
        self.store.feed_status(feed, **state)

    @repository_access
    def telegram_http_delay(self) -> float:
        return float(self.store.subscriptions.telegram_http_settings()['value'])

    @repository_access
    def source_revision(self) -> int:
        return self.store.subscriptions.source_revision()

    @repository_access
    def history_revision(self) -> list[Any]:
        return self.store.read_versions.history_versions()

    @repository_access
    def configuration(self) -> SourceConfiguration:
        store=self.store
        feeds=store.subscriptions.feeds_with_history(include_deleted=True)
        bindings=store.subscriptions.telegram.bindings()
        settings=[]
        dependencies=[]
        store.publications.sync_feeds([feed['id'] for feed in feeds])
        for feed in feeds:
            binding=bindings.get(feed['id'])
            access=None
            if binding and binding['kind']=='web':
                access=TelegramAccess('web',binding.get('username',feed.get('telegram_username')))
            if feed['connection']!='deleting' and (feed['transport'] is not None or binding and binding['kind']=='web'):
                dependencies.append(feed['id'])
            settings.append(SourceSettings(feed['id'],feed['transport'],(feed['original_urls'] or [''])[0],feed['initial_history'],
                feed['connection']=='deleting',access,store.subscriptions.youtube_theses(feed['id']) if feed['transport']=='youtube' else False))
        return SourceConfiguration(store.subscriptions.source_revision(),settings,dependencies,
                                   [feed['id'] for feed in feeds if feed.get('initial_history_invalid')])

    @repository_access
    def binding(self, feed: str) -> dict[str,Any] | None:
        return self.store.subscriptions.telegram.binding(feed)

    @repository_access
    def refresh_identity(self, feed: str, info: dict[str,Any]) -> None:
        self._require_transaction()
        self.store.subscriptions.telegram.refresh(feed,info)

    @repository_access
    def cursor(self, feed: str) -> str | None:
        return self.store.cursor(feed)

    @repository_access
    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None:
        self._require_transaction()
        self.store.subscriptions.history.discovery_state(feed,dict(history_waiting=True,history_error=label,history_retry_at=retry_at))

    @repository_access
    def caption_ready(self) -> float:
        return self.store.youtube.ready_at()

    @repository_access
    def caption_config(self) -> dict[str,str]:
        return self.store.youtube.config()

    @repository_access
    def caption_started(self, wall: float) -> None:
        self._require_transaction()
        self.store.youtube.start(wall)

    @repository_access
    def caption_blocked(self, wall: float, delay: float) -> None:
        self._require_transaction()
        self.store.youtube.blocked(wall,delay)

    @repository_access
    def caption_completed(self) -> None:
        self._require_transaction()
        self.store.youtube.completed()

    @repository_access
    def startup(self, injected: bool, source_feed: str) -> float:
        store=self.store
        if injected:store.subscriptions.check_owner('feed',source_feed)
        return store.subscriptions.feed_refresh_minutes()*60

    @repository_access
    def revision(self, item: str) -> str:
        """Capture relevant input at admission without storing a duplicate hash."""
        from oko.storage.processing_input import digest
        from oko.storage.reading import saved_content
        row=self.store.db.execute(f"""SELECT i.platform,i.external_id,i.url,
            COALESCE(i.content #> '{{extra,telegram,members}}','[]'::jsonb)::text AS members,
            i.feed_id AS feed_id,f.adapter,i.title,i.content FROM publications i
            LEFT JOIN feeds f ON f.id=i.feed_id WHERE i.id=%s""",(item,)).fetchone()
        if row is None:return digest(None)
        content=saved_content(row['content'])
        blocks=[]
        for block in content.get('blocks',[]):
            if block.get('type')=='media':
                blocks.append({key:value for key,value in block.items() if key not in ('source_url','publication_url')})
            else:blocks.append(block)
        return digest([row['platform'],row['external_id'],row['url'],row['members'],row['feed_id'],row['adapter'],row['title'],blocks])

    @repository_access
    def unviewed(self, item: str) -> bool:
        return self.store._has_unviewed(item)

    @repository_access
    def saved_reference(self, reference: ExternalReference) -> bool:
        row=self.store.db.execute('SELECT content FROM publications WHERE platform=%s AND external_id=%s AND url=%s',(reference.key.platform.value,reference.key.external_id,reference.url)).fetchone()
        return bool(row and 'blocks' in row['content'])

    @repository_access
    def feedless(self, item: str) -> bool:
        return publication_links.feedless(self.store, item)

    @repository_access
    def reference(self, item: str) -> ExternalReference | None:
        row=self.store.db.execute('SELECT platform,external_id,url FROM publications WHERE id=%s',(item,)).fetchone()
        return ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),row['url']) if row else None

    @repository_access
    def feed(self, item: str) -> str:
        return self.store.item_feed(item)

    @repository_access
    def viewed(self, item: str) -> None:
        if not self.store.db.in_transaction:
            raise RuntimeError('Collection changes require an owning transaction')
        self.store.viewed(item)
