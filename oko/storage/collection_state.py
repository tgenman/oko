"""Saved feed/source facts used by collection services."""
from dataclasses import dataclass
from typing import Any
from oko.storage.publication_owners import owner_expr
from oko.storage.store import Store
from oko.collection.sources import SourceSettings, TelegramAccess


@dataclass(frozen=True)
class SourceConfiguration:
    revision: int
    settings: list[SourceSettings]
    dependencies: list[str]
    bindings: dict[str,dict[str,Any]]


class CollectionState:
    def __init__(self,store: Store) -> None:
        self.store=store

    def _require_transaction(self) -> None:
        if not self.store.db.in_transaction:
            raise RuntimeError('Collection source changes require an owning transaction')

    def feed_status(self, feed: str, **state: Any) -> None:
        self.store.feed_status(feed, **state)

    def telegram_http_delay(self) -> float:
        return float(self.store.subscriptions.telegram_http_settings()['value'])

    def revision(self) -> int:
        return self.store.subscriptions.source_revision()

    def configuration(self) -> SourceConfiguration:
        store=self.store
        feeds=store.subscriptions.feeds(include_deleted=True)
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
            settings.append(SourceSettings(feed['id'],feed['transport'],(feed['original_urls'] or [''])[0],feed['initial_days'],feed['initial_history'],
                feed['connection']=='deleting',access,store.subscriptions.youtube_theses(feed['id']) if feed['transport']=='youtube' else False))
        return SourceConfiguration(self.revision(),settings,dependencies,bindings)

    def binding(self, feed: str) -> dict[str,Any] | None:
        return self.store.subscriptions.telegram.binding(feed)


    def refresh_identity(self, feed: str, info: dict[str,Any]) -> None:
        self._require_transaction()
        self.store.subscriptions.telegram.refresh(feed,info)

    def cursor(self, feed: str) -> str | None:
        return self.store.cursor(feed)

    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None:
        self._require_transaction()
        self.store.subscriptions.history.discovery_state(feed,dict(history_waiting=True,history_error=label,history_retry_at=retry_at))

    def caption_ready(self) -> float:
        return self.store.youtube.ready_at()

    def caption_config(self) -> dict[str,str]:
        return self.store.youtube.config()

    def caption_started(self, wall: float) -> None:
        self._require_transaction()
        self.store.youtube.start(wall)

    def caption_blocked(self, wall: float, delay: float) -> None:
        self._require_transaction()
        self.store.youtube.blocked(wall,delay)

    def caption_completed(self) -> None:
        self._require_transaction()
        self.store.youtube.completed()

    def startup(self, injected: bool, source_feed: str) -> float:
        store=self.store
        if injected:store.subscriptions.check_owner('feed',source_feed)
        return store.subscriptions.feed_refresh_minutes()*60

    def publication_revision(self, item: str) -> str:
        """Capture relevant input at admission without storing a duplicate hash."""
        from oko.storage.processing_input import digest
        from oko.storage.reading import saved_content
        row=self.store.db.execute(f"""SELECT i.platform,i.external_id,i.url,
            COALESCE(i.content #> '{{extra,telegram,members}}','[]'::jsonb)::text AS members,
            {owner_expr('i')} AS feed_id,f.adapter,i.title,i.content FROM publications i
            LEFT JOIN feeds f ON f.id={owner_expr('i')} WHERE i.id=%s""",(item,)).fetchone()
        if row is None:return digest(None)
        content=saved_content(row['content'])
        blocks=[]
        for block in content.get('blocks',[]):
            if block.get('type')=='media':
                blocks.append({key:value for key,value in block.items() if key not in ('source_url','publication_url')})
            else:blocks.append(block)
        return digest([row['platform'],row['external_id'],row['url'],row['members'],row['feed_id'],row['adapter'],row['title'],blocks])
