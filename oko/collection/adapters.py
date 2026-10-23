"""External adapter API. No persistence, scheduler or application IDs."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, BinaryIO, Protocol


class Platform(str, Enum):
    telegram = 'telegram'
    youtube = 'youtube'
    telegraph = 'telegraph'
    instagram = 'instagram'
    twitter = 'twitter'
    other_web = 'other_web'
    rss = 'rss'


@dataclass(frozen=True)
class ExternalKey:
    platform: Platform
    external_id: str


@dataclass(frozen=True)
class ExternalReference:
    key: ExternalKey
    url: str


@dataclass(frozen=True)
class FeedMetadata:
    reference: ExternalReference
    title: str
    description: str
    latest_cursor: str


@dataclass(frozen=True)
class PublicationMetadata:
    reference: ExternalReference
    title: str
    published_at: datetime | None


@dataclass(frozen=True)
class MediaReference:
    key: str
    locator: str
    name: str
    mime_type: str | None = None
    size_bytes: int | None = None


class AdapterErrorKind(str, Enum):
    notice = 'notice'
    temporary = 'temporary'
    source_wait = 'source_wait'
    unavailable = 'unavailable'
    permanent = 'permanent'
    media_changed = 'media_changed'


@dataclass(frozen=True)
class AdapterIssue:
    code: str
    message: str
    kind: AdapterErrorKind
    publication: ExternalReference | None = None
    media_key: str | None = None
    scope: str | None = None
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind == AdapterErrorKind.source_wait and (not self.scope or self.retry_at is None):
            raise ValueError('source_wait requires scope and retry_at')


class AdapterException(Exception):
    def __init__(self, issue: AdapterIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


@dataclass(frozen=True)
class PublicationContent:
    metadata: PublicationMetadata
    content: dict[str, Any] = field(default_factory=dict)
    media: tuple[MediaReference, ...] = ()
    linked_publications: tuple[ExternalReference, ...] = ()
    needs_fetch: bool = False
    issues: tuple[AdapterIssue, ...] = ()


@dataclass(frozen=True)
class DiscoveryPage:
    publications: tuple[PublicationContent, ...]
    next_cursor: str
    has_more: bool
    issues: tuple[AdapterIssue, ...] = ()


class PublicationAdapter(Protocol):
    async def fetch(self, publication: ExternalReference) -> PublicationContent: ...
    async def download_media(self, publication: ExternalReference, media: MediaReference,
                             output: BinaryIO) -> MediaReference: ...


class FeedAdapter(PublicationAdapter, Protocol):
    async def resolve_feed(self, url: str) -> FeedMetadata: ...
    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str: ...
    async def discover(self, feed: ExternalReference, cursor: str, limit: int) -> DiscoveryPage: ...
