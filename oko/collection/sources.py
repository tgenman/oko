"""Configured source adapters; no Store, execution locks or worker ownership."""
from copy import copy, deepcopy
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from oko.collection.adapters import FeedAdapter, PublicationAdapter
from oko.collection.rss_adapter import RSSAdapter
from oko.collection.web_adapter import WebAdapter
from oko.collection.telegram.adapter import TelegramWebAdapter
from oko.collection.youtube.adapter import YouTubeAdapter
from oko.collection.contracts import CaptionsClient
from oko.collection.youtube.history import HistoryClient


@dataclass(frozen=True)
class TelegramAccess:
    kind: Literal['web']
    username: str | None = None


@dataclass(frozen=True)
class SourceSettings:
    feed: str
    transport: str
    url: str
    initial_history: dict[str, Any] | None
    deleting: bool = False
    telegram: TelegramAccess | None = None
    want_captions: bool = False


@dataclass(frozen=True)
class SourceUpdates:
    created: tuple[str, ...]
    captions: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class FeedRuntime:
    adapter: FeedAdapter
    settings: SourceSettings
    kind: Literal['rss', 'telegram', 'youtube', 'injected']


class Sources:
    def __init__(self, source: FeedAdapter | None, source_feed: str, rss_enabled: bool,
                 transport: httpx.AsyncBaseTransport | None,
                 captions: CaptionsClient, history: HistoryClient | None,
                 telegram_transport: httpx.AsyncBaseTransport | None = None,
                 publication_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.sources: dict[str, FeedRuntime] = {source_feed: FeedRuntime(source,SourceSettings(source_feed,'injected','https://fixture.invalid/feed',None),'injected')} if source else {}
        self._injected = source_feed if source else None
        self._rss_enabled, self._transport = rss_enabled, transport
        self._captions, self._history = captions, history
        self._telegram_transport = telegram_transport or transport
        self._publication_transport = publication_transport or transport

    def configure(self, settings: list[SourceSettings]) -> SourceUpdates:
        created: list[str] = []
        captions: list[tuple[str, bool]] = []
        active = {self._injected} if self._injected else set()
        for supplied in settings:
            if supplied.feed == self._injected:
                continue
            config = deepcopy(supplied)
            access = config.telegram
            if config.deleting:
                continue
            previous = self.sources.get(config.feed)
            if config.transport == 'youtube' and self._rss_enabled:
                kind: Literal['rss','telegram','youtube']='youtube'
                captions.append((config.feed, config.want_captions))
            elif access and access.kind == 'web':
                assert access.username is not None
                kind='telegram'
            elif config.transport == 'rss' and self._rss_enabled:
                kind='rss'
            else:
                continue
            active.add(config.feed)
            reuse=(isinstance(previous,FeedRuntime) and previous.kind==kind
                   and previous.settings.url==config.url and previous.settings.telegram==config.telegram)
            if reuse and isinstance(previous,FeedRuntime):
                adapter=previous.adapter
            else:
                if kind=='youtube':adapter=YouTubeAdapter(self._captions,self._publication_transport,self._history)
                elif kind=='telegram':adapter=TelegramWebAdapter(self._telegram_transport,linked_descriptions=True)
                else:adapter=RSSAdapter(self._publication_transport)
                created.append(config.feed)
            self.sources[config.feed]=FeedRuntime(adapter,config,kind)
        for identity in set(self.sources) - active:
            self.sources.pop(identity)
        return SourceUpdates(tuple(created), tuple(captions))

    def description(self, feed: str) -> str | None:
        source = self.sources.get(feed)
        if isinstance(source,FeedRuntime):
            return {'rss':'RSS: new entries are discovered in the available feed window.',
                    'telegram':'Telegram web: public history and new messages are discovered.',
                    'youtube':'YouTube: new videos are discovered in the available Atom window.',
                    'injected':None}[source.kind]
        return None

    def telegram_access(self, feed: str) -> Literal['web'] | None:
        source = self.sources.get(feed)
        return 'web' if isinstance(source,FeedRuntime) and source.kind=='telegram' else None

    def caption_feeds(self) -> list[str]:
        return [feed for feed,source in self.sources.items() if isinstance(source,FeedRuntime) and source.kind=='youtube']

    def article_adapter(self) -> WebAdapter:
        return WebAdapter(self._publication_transport)

    def publication_adapter(self, feed: str, *, captions: CaptionsClient | None = None) -> PublicationAdapter | None:
        source = self.sources.get(feed)
        if isinstance(source,FeedRuntime) and isinstance(source.adapter,YouTubeAdapter):
            adapter=copy(source.adapter)
            if captions is not None:adapter.captions=captions
            return adapter
        if isinstance(source,FeedRuntime):return source.adapter
        return None
