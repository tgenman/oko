"""Saved feed/source facts used by collection services."""
from dataclasses import dataclass
from oko.storage.read_sync import ReadPlan
from typing import Any
from oko.storage.store import Store
from oko.collection.sources import SourceSettings, TelegramAccess
from oko.collection.telegram.contracts import ClientReadBinding


@dataclass(frozen=True)
class SourceConfiguration:
    revision: int
    settings: list[SourceSettings]
    dependencies: list[str]
    bindings: dict[str,dict[str,Any]]
    profiles: dict[str,dict[str,Any]]
    reads: dict[str,ClientReadBinding]
    waits: dict[int,float]


class CollectionState:
    def __init__(self,store: Store) -> None:
        self.store=store

    def revision(self) -> int:
        return self.store.subscriptions.source_revision()

    def configuration(self) -> SourceConfiguration:
        store=self.store
        feeds=store.subscriptions.feeds()
        bindings=store.subscriptions.telegram.bindings()
        profiles=store.subscriptions.telegram.profiles()
        settings=[]
        dependencies=[]
        reads={}
        waits={}
        store.publications.sync_feeds([feed['id'] for feed in feeds])
        for feed in feeds:
            binding=bindings.get(feed['id'])
            access=None
            if binding:
                profile=profiles.get(binding['role']) if binding['kind']=='client' else None
                access=TelegramAccess(binding['kind'],binding.get('username',feed.get('telegram_username')),
                    profile['account'] if profile else feed.get('telegram_account'),
                    int(binding['peer_id']) if binding['kind']=='client' else feed.get('telegram_peer'))
                if profile:
                    state=store.db.execute('SELECT initial FROM telegram_read_state WHERE feed = %s',(feed['id'],)).fetchone()
                    reads[feed['id']]=ClientReadBinding(profile['account'],profile['user_id'],int(binding['peer_id']),profile['generation'],(state['initial'] or 0) if state else 0)
                    wait=store.db.execute('SELECT value FROM state WHERE key = %s',('telegram-retry:'+str(profile['user_id']),)).fetchone()
                    if wait:waits[profile['user_id']]=float(wait[0])
            if feed['connection']!='deleting' and (feed['transport'] is not None or binding and binding['kind']=='web'):
                dependencies.append(feed['id'])
            settings.append(SourceSettings(feed['id'],feed['transport'],(feed['original_urls'] or [''])[0],feed['initial_days'],feed['initial_history'],
                feed['connection']=='deleting',access,store.subscriptions.youtube_theses(feed['id']) if feed['transport']=='youtube' else False))
        return SourceConfiguration(self.revision(),settings,dependencies,bindings,profiles,reads,waits)

    def binding(self, feed: str) -> dict[str,Any] | None:
        return self.store.subscriptions.telegram.binding(feed)

    def profile(self, role: str) -> dict[str,Any] | None:
        return self.store.subscriptions.telegram.profile(role)

    def refresh_identity(self, feed: str, info: dict[str,Any]) -> None:
        self.store.subscriptions.telegram.refresh(feed,info)

    def cursor(self, feed: str) -> str | None:
        return self.store.cursor(feed)

    def notify_parents(self, item: str) -> None:
        from oko.storage.publication_links import notify_parents
        notify_parents(self.store,item)

    def discovery_wait(self, feed: str, label: str, retry_at: float | None) -> None:
        self.store.subscriptions.history.discovery_state(feed,dict(history_waiting=True,history_error=label,history_retry_at=retry_at))

    def read_request(self, feed: str) -> dict[str,Any] | None:
        return self.store.read_sync.request(feed)

    def read_confirmed(self, feed: str) -> int:
        return self.store.read_sync.confirmed(feed)

    def read_signature(self, request: dict[str,Any]) -> str:
        return self.store.read_sync.signature(request)

    def read_plan(self, request: dict[str,Any], state: dict[str,Any], window: dict[str,Any] | None = None) -> ReadPlan:
        return self.store.read_sync.plan(request,state,window)

    def read_completed(self, feed: str, plan: ReadPlan, confirmed: int) -> None:
        self.store.read_sync.completed(feed,plan,confirmed)

    def read_failed(self, feed: str, label: str) -> None:
        self.store.read_sync.failed(feed,label)

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
        if injected and source_feed=='demo' or store.db.execute("SELECT 1 FROM unviewed_items_by_feed WHERE feed_id = 'demo' AND viewed = 0 LIMIT 1").fetchone():
            store.subscriptions.ensure_demo()
        if injected:store.subscriptions.check_owner('feed',source_feed)
        return store.subscriptions.feed_refresh_minutes()*60

    def publication_revision(self, item: str) -> str:
        from oko.storage.processing_input import digest
        row=self.store.db.execute("SELECT i . platform , i . external_id , i . url , i . members , i . feed_id , f . adapter , coalesce ( i.content::jsonb#>>'{extra,_collection_revision}' , i.content::jsonb#>>'{extra,_input_hash}' ) FROM items_read i LEFT JOIN feeds f ON f . id = i . feed_id WHERE i . id = %s AND i . viewed = 0",(item,)).fetchone()
        return digest(list(row) if row else None)
