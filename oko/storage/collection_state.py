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

    def revision(self) -> int:
        return self.store.subscriptions.source_revision()

    def configuration(self) -> SourceConfiguration:
        store=self.store
        feeds=store.subscriptions.feeds()
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
        self.store.subscriptions.telegram.refresh(feed,info)

    def cursor(self, feed: str) -> str | None:
        return self.store.cursor(feed)

    def notify_parents(self, item: str) -> None:
        from oko.storage.publication_links import notify_parents
        notify_parents(self.store,item)

    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None:
        self.store.subscriptions.history.discovery_state(feed,dict(history_waiting=True,history_error=label,history_retry_at=retry_at))

    def caption_ready(self) -> float:
        return self.store.youtube.ready_at()

    def caption_config(self) -> dict[str,str]:
        return self.store.youtube.config()

    def caption_started(self, wall: float) -> None:
        self.store.youtube.start(wall)

    def caption_blocked(self, wall: float, delay: float) -> None:
        self.store.youtube.blocked(wall,delay)

    def caption_completed(self) -> None:
        self.store.youtube.completed()

    def startup(self, injected: bool, source_feed: str) -> float:
        store=self.store
        if injected and source_feed=='demo' or store.db.execute("SELECT 1 FROM feed_publications m JOIN publications p ON p.id=m.publication_id WHERE m.feed_id = 'demo' AND m.is_primary AND m.is_unviewed AND p.viewed = 0 LIMIT 1").fetchone():
            store.ensure_demo()
        if injected:store.subscriptions.check_owner('feed',source_feed)
        return store.subscriptions.feed_refresh_minutes()*60

    def publication_revision(self, item: str) -> str:
        from oko.storage.processing_input import digest
        row=self.store.db.execute("SELECT i . platform , i . external_id , i . url , i . members , i . feed_id , f . adapter , coalesce ( i.content::jsonb#>>'{extra,_collection_revision}' , i.content::jsonb#>>'{extra,_input_hash}' ) FROM publications i LEFT JOIN feeds f ON f . id = i . feed_id WHERE i . id = %s AND i . viewed = 0".replace('i . feed_id', owner_expr('i')),(item,)).fetchone()
        return digest(list(row) if row else None)
