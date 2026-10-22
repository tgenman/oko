"""Public Telegram adapter. Cursors describe external windows, never database rows."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
import re
from typing import Any, BinaryIO
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

import httpx

from oko.collection.adapters import (
    AdapterErrorKind, AdapterException, AdapterIssue, DiscoveryPage, ExternalKey,
    ExternalReference, FeedMetadata, MediaReference, Platform, PublicationContent,
    PublicationMetadata,
)
from oko.collection.contracts import SourceDeleted, SourceDeferred, SourceError
from oko.collection.telegram.contracts import TelegramPublication
from oko.collection.telegram.media import Downloader
from oko.collection.telegram.web import load


def reference(url: str) -> ExternalReference:
    return ExternalReference(ExternalKey(Platform.telegram, url), url)


def username(feed: ExternalReference) -> str:
    parsed = urlsplit(feed.url)
    parts = parsed.path.strip('/').split('/')
    if parts and parts[0] == 's':
        parts = parts[1:]
    if (feed.key.platform != Platform.telegram or parsed.scheme not in ('http', 'https')
            or parsed.hostname not in ('t.me', 'telegram.me') or parsed.username or parsed.password
            or parsed.port or not parts or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', parts[0])):
        raise AdapterException(AdapterIssue('invalid_reference', 'Неверный адрес Telegram', AdapterErrorKind.permanent))
    return parts[0].lower()


def failure(error: SourceError, publication: ExternalReference | None = None,
            media_key: str | None = None, scope: str = 'telegram:html') -> AdapterException:
    kind = AdapterErrorKind.temporary if error.retryable else AdapterErrorKind.permanent
    issue_scope, retry_at = None, None
    if isinstance(error, SourceDeleted):
        kind = AdapterErrorKind.unavailable
    if isinstance(error, SourceDeferred):
        kind = AdapterErrorKind.source_wait
        issue_scope = getattr(error, 'scope', scope)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=error.delay)
    result = AdapterException(AdapterIssue(error.code or 'source_unavailable', error.label,
        kind, publication, media_key, issue_scope, retry_at))
    return result


def encode(start: int, upper: int, *, done: bool = False) -> str:
    return json.dumps(dict(telegram_web=1, start=start, upper=upper, done=done), separators=(',', ':'))


def decode(cursor: str) -> dict[str, Any]:
    try:
        value = json.loads(cursor)
        if (value.get('telegram_web') != 1 or type(value['start']) is not int
                or type(value['upper']) is not int or type(value['done']) is not bool
                or value['start'] < 0 or value['upper'] < 0):
            raise ValueError('invalid window')
        return dict(value)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AdapterException(AdapterIssue('telegram-cursor-incompatible',
            'Несовместимый курсор Telegram', AdapterErrorKind.permanent)) from None


def content(post: TelegramPublication) -> PublicationContent:
    publication = post.publication
    teaser = publication.teaser
    if teaser is None:
        return PublicationContent(PublicationMetadata(reference(publication.url), '', None), needs_fetch=True)
    assert teaser.source_manifest
    blocks: list[dict[str, Any]] = []
    if post.text:
        blocks.append(dict(type='text', role='body', text=post.text))
    if teaser.reply_preview:
        blocks.append(dict(type='quote', text=teaser.reply_preview.text,
            author=teaser.reply_preview.author, source_url=teaser.reply_preview.source_url))
    media = []
    issues = []
    for item, resource in zip(post.media, teaser.source_manifest.resources):
        if item.url:
            media.append(MediaReference(item.id, item.url, item.name, item.mime, item.size))
            blocks.append(dict(type='media', ref={'$media': item.id}, role='attachment' if resource.kind=='file' else resource.kind))
        elif item.external:
            blocks.append(dict(type='external_media', kind=item.external.kind, url=item.external.source_url))
        else:
            issues.append(AdapterIssue('media_unavailable', item.reason or 'Вложение недоступно',
                AdapterErrorKind.unavailable, reference(publication.url), item.id))
    return PublicationContent(PublicationMetadata(reference(publication.url), teaser.title,
        datetime.fromtimestamp(publication.published, timezone.utc) if publication.published_known else None),
        dict(blocks=blocks, extra=dict(telegram=dict(members=list(publication.members),
            description=teaser.description, manifest=teaser.source_manifest.model_dump(mode='json')))),
        tuple(media), needs_fetch=False, issues=tuple(issues))


def resource_identity(locator: str) -> str:
    url = urlsplit(locator)
    query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
             if key.lower() not in ('signature', 'expires')]
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, urlencode(query), ''))


class TelegramWebAdapter:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def resolve_feed(self, url: str) -> FeedMetadata:
        name = username(reference(url))
        try:
            page, _ = await load(name, 0, self.transport, partial=True)
        except SourceError as error:
            raise failure(error) from error
        upper = max((max(p.publication.members) for p in page.posts), default=0)
        return FeedMetadata(reference('https://t.me/s/'+name), page.title, page.description,
                            encode(upper, upper) if upper else '')

    async def _window(self, feed: ExternalReference, start: int, upper: int,
                      *, count: int | None = None) -> tuple[list[TelegramPublication], tuple[AdapterIssue, ...]]:
        name = username(feed)
        before = upper + 1
        posts: list[TelegramPublication] = []
        issues: list[AdapterIssue] = []
        while True:
            try:
                page, _ = await load(name, before, self.transport, partial=True)
            except SourceError as error:
                raise failure(error) from error
            issues.extend(issue for issue in page.issues if issue not in issues)
            for post in sorted(page.posts, key=lambda p: max(p.publication.members), reverse=True):
                members = set(post.publication.members)
                if max(members) < start or max(members) > upper:
                    continue
                existing = next((p for p in posts if members.intersection(p.publication.members)), None)
                if existing is None:
                    posts.append(post)
                # Overlapping pages must not count one album twice.
            if (count is not None and len(posts) >= count) or not page.next_before or any(
                    min(p.publication.members) <= start for p in page.posts):
                return sorted(posts, key=lambda p: max(p.publication.members), reverse=True), tuple(issues)
            before = page.next_before

    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str:
        if offset < 0:
            raise ValueError('offset must be nonnegative')
        if not cursor or offset == 0:
            return cursor
        state = decode(cursor)
        posts, _ = await self._window(feed, 0, state['start'], count=offset+1)
        return encode(max(posts[min(offset, len(posts)-1)].publication.members), state['upper']) if posts else ''

    async def discover(self, feed: ExternalReference, cursor: str, limit: int) -> DiscoveryPage:
        if limit < 1:
            raise ValueError('limit must be positive')
        if not cursor:
            metadata = await self.resolve_feed(feed.url)
            if not metadata.latest_cursor:
                return DiscoveryPage((), '', False)
            state = decode(metadata.latest_cursor)
            state['start'] = 0
        else:
            state = decode(cursor)
            if state['done']:
                metadata = await self.resolve_feed(feed.url)
                upper = decode(metadata.latest_cursor)['upper'] if metadata.latest_cursor else state['upper']
                state = dict(start=state['upper']+1, upper=upper, done=False)
        if state['start'] > state['upper']:
            return DiscoveryPage((), encode(state['upper']+1, state['upper'], done=True), False)
        window, issues = await self._window(feed, state['start'], state['upper'])
        posts = list(reversed(window))
        selected = posts[:limit]
        more = len(posts) > limit
        start = max(selected[-1].publication.members)+1 if selected else state['upper']+1
        return DiscoveryPage(tuple(content(p) for p in selected), encode(start, state['upper'], done=not more), more, issues)

    async def fetch(self, publication: ExternalReference) -> PublicationContent:
        name = username(publication)
        try:
            identity = int(urlsplit(publication.url).path.rstrip('/').split('/')[-1])
        except ValueError:
            raise AdapterException(AdapterIssue('invalid_reference', 'Нужен адрес публикации', AdapterErrorKind.permanent)) from None
        try:
            page, _ = await load(name, identity+1, self.transport, partial=True)
        except SourceError as error:
            raise failure(error, publication) from error
        post = next((p for p in page.posts if identity in p.publication.members or int(p.publication.id) == identity), None)
        if post is None:
            raise failure(SourceDeleted(), publication)
        return content(post)

    async def download_media(self, publication: ExternalReference, media: MediaReference,
                             output: BinaryIO) -> MediaReference:
        current = await self.fetch(publication)
        actual = next((item for item in current.media if item.key == media.key), None)
        if actual is None or actual.name != media.name or resource_identity(actual.locator) != resource_identity(media.locator):
            raise AdapterException(AdapterIssue('media_changed', 'Вложение изменилось',
                AdapterErrorKind.media_changed, publication, media.key))
        try:
            async with asyncio.timeout(180):
                size, _ = await Downloader(self.transport).write(actual.locator, output)
        except SourceError as error:
            raise failure(error, publication, media.key, 'telegram:media:'+urlsplit(actual.locator).netloc) from error
        except TimeoutError as error:
            raise AdapterException(AdapterIssue('media-unavailable', 'Истекло время загрузки вложения',
                AdapterErrorKind.temporary, publication, media.key)) from error
        return replace(actual, size_bytes=size)
