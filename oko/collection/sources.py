"""Configured source adapters; no Store, execution locks or worker ownership."""
from copy import copy, deepcopy
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from oko.collection.contracts import Source
from oko.collection.adapters import PublicationAdapter
from oko.collection.rss import RSSSource
from oko.collection.rss_source import RSSAdapterSource
from oko.collection.web_adapter import WebAdapter
from oko.collection.web_source import WebArticleSource
from oko.collection.telegram.source import TelegramSource
from oko.collection.telegram.web import WebTelegram
from oko.collection.telegram.channel_source import TelegramWebSource
from oko.collection.youtube.source import YouTubeSource
from oko.collection.youtube.publication_source import YouTubeAdapterSource
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
    initial_days: int
    initial_history: dict[str, Any] | None
    deleting: bool = False
    telegram: TelegramAccess | None = None
    want_captions: bool = False


@dataclass(frozen=True)
class SourceUpdates:
    created: tuple[str, ...]
    captions: tuple[tuple[str, bool], ...]


class Sources:
    def __init__(self, source: Source | None, source_feed: str, rss_enabled: bool,
                 transport: httpx.AsyncBaseTransport | None,
                 captions: CaptionsClient, history: HistoryClient | None,
                 telegram_transport: httpx.AsyncBaseTransport | None = None,
                 publication_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.sources: dict[str, Source] = {source_feed: source} if source else {}
        self._injected = source_feed if source else None
        self._rss_enabled, self._transport = rss_enabled, transport
        self._captions, self._history = captions, history
        self._telegram_transport = telegram_transport or transport
        self._publication_transport = publication_transport or transport
        self._settings: dict[str, SourceSettings] = {}

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
            source: RSSSource | TelegramSource
            if config.transport == 'youtube' and self._rss_enabled:
                youtube_type=YouTubeAdapterSource
                source = previous if type(previous) is youtube_type else youtube_type(
                    config.url, config.initial_days, self._captions, self._publication_transport, self._history)
                captions.append((config.feed, config.want_captions))
            elif access and access.kind == 'web':
                assert access.username is not None
                web_type=TelegramWebSource
                source = previous if type(previous) is web_type else web_type(
                    WebTelegram(access.username, self._telegram_transport), config.initial_days, access.username)
            elif config.transport == 'rss' and self._rss_enabled:
                source_type = RSSAdapterSource
                source = previous if type(previous) is source_type else source_type(config.url, config.initial_days, self._publication_transport)
            else:
                continue
            active.add(config.feed)
            if source is not previous:
                created.append(config.feed)
            elif self._settings.get(config.feed) == config:
                continue
            else:
                # An in-flight call keeps its configuration; stable polling keeps
                # the adapter and its caches. ReadSync keeps its own binding.
                source = copy(source)
            source.initial_days = config.initial_days
            source.initial_history = deepcopy(config.initial_history)
            if isinstance(source, YouTubeSource):
                source.want_captions = config.want_captions
            self.sources[config.feed] = source
            self._settings[config.feed] = config
        for identity in set(self.sources) - active:
            self.sources.pop(identity)
            self._settings.pop(identity, None)
        return SourceUpdates(tuple(created), tuple(captions))

    def description(self, feed: str) -> str | None:
        source = self.sources.get(feed)
        return source.changes if source else None

    def telegram_access(self, feed: str) -> Literal['web'] | None:
        source = self.sources.get(feed)
        return 'web' if isinstance(source, TelegramSource) else None

    def caption_feeds(self) -> list[str]:
        return [feed for feed,source in self.sources.items() if isinstance(source,YouTubeAdapterSource)]

    def article_source(self) -> WebArticleSource:
        return WebArticleSource(WebAdapter(self._publication_transport))

    def publication_adapter(self, feed: str, *, captions: CaptionsClient | None = None) -> PublicationAdapter | None:
        source = self.sources.get(feed)
        if isinstance(source,YouTubeAdapterSource):
            adapter=source.publication_adapter()
            if captions is not None:
                adapter=copy(adapter)
                adapter.captions=captions
            return adapter
        if isinstance(source,RSSAdapterSource):
            return source.publication_adapter()
        return source.publication_adapter() if source is not None else None
