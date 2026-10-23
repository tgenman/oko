"""Public Telegram adapter. Cursors describe external windows, never database rows."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from copy import deepcopy
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
from oko.collection.contracts import SourceDeleted, SourceDeferred, SourceError, ArticleManifest
from oko.collection.telegram.telegraph import direct_articles, describe
from oko.collection.telegram.contracts import TelegramPublication
from oko.collection.telegram.media import Downloader
from oko.collection.telegram.web import load, WebPage


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


def publication_reference(url: str) -> ExternalReference:
    """Resolve legacy public links without inventing a Telegram numeric peer ID."""
    parsed = urlsplit(url)
    name = username(reference(url))
    message = parsed.path.rstrip('/').rsplit('/', 1)[-1]
    if not message.isascii() or not message.isdecimal() or int(message) <= 0:
        raise AdapterException(AdapterIssue('invalid_reference', 'Неверный адрес публикации Telegram', AdapterErrorKind.permanent))
    return reference('https://t.me/'+name+'/'+str(int(message)))


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


def content(post: TelegramPublication, *, linked_descriptions: bool = False) -> PublicationContent:
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
            media.append(MediaReference(item.id, item.url, item.original_name, item.mime, item.size, resource_identity(item.url)))
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
        tuple(media), needs_fetch=bool(teaser.source_manifest.post_web_unavailable and post.text.strip() or linked_descriptions and direct_articles(post.text)), issues=tuple(issues))


def resource_identity(locator: str) -> str:
    url = urlsplit(locator)
    query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
             if key.lower() not in ('signature', 'expires')]
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, urlencode(query), ''))


class TelegramWebAdapter:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, *, linked_descriptions: bool = False) -> None:
        self.transport = transport
        self.linked_descriptions = linked_descriptions
        self._head: tuple[str, int, WebPage] | None = None

    async def resolve_feed(self, url: str) -> FeedMetadata:
        name = username(reference(url))
        try:
            page, _ = await load(name, 0, self.transport, partial=True)
        except SourceError as error:
            raise failure(error) from error
        if not page.posts and page.issues:
            raise AdapterException(AdapterIssue('source_identity_unavailable',
                'Telegram не предоставил пригодных идентификаторов публикаций', AdapterErrorKind.permanent))
        upper = max((max(p.publication.members) for p in page.posts), default=0)
        self._head = (name, upper, page)
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
                if self._head and self._head[:2] == (name, upper) and before == upper+1:
                    page = self._head[2]
                else:
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
        return DiscoveryPage(tuple(content(p,linked_descriptions=self.linked_descriptions) for p in selected), encode(start, state['upper'], done=not more), more, issues)

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
        value = content(post,linked_descriptions=self.linked_descriptions)
        if value.needs_fetch and post.publication.teaser and post.publication.teaser.source_manifest and post.publication.teaser.source_manifest.post_web_unavailable:
            from oko.collection.telegram.preview import fragment
            try:
                preview, preview_error = await fragment(name, post.publication.id, self.transport, None)
            except SourceError as source_error:
                raise failure(source_error, publication) from source_error
            manifest = value.content['extra']['telegram']['manifest']
            manifest.update(preview_fragment=preview, preview_error=preview_error)
            if preview:
                value.content['blocks'].append(dict(type='text', role='body', text=preview))
            value = replace(value, needs_fetch=False)
        if self.linked_descriptions and value.content:
            body = deepcopy(value.content)
            manifest = body['extra']['telegram']['manifest']
            if manifest.get('preview_fragment') is None:
                media_refs = list(value.media)
                articles = []
                issues = list(value.issues)
                for url in direct_articles(str(manifest['post'].get('text',''))):
                    try:
                        article, expected, resources = await describe(url,self.transport)
                        offset = len(manifest['resources'])
                        manifest['resources'].extend(r.model_copy(update={'order':offset+i}).model_dump(mode='json') for i,r in enumerate(resources))
                        for resource in expected:
                            if resource.url:
                                media_refs.append(MediaReference(resource.id,resource.url,resource.original_name,resource.mime,resource.size,resource_identity(resource.url)))
                                body['blocks'].append(dict(type='media',ref={'$media':resource.id},role='image' if resource.kind=='photo' else 'attachment' if resource.kind=='document' else resource.kind))
                        if article.text is not None:
                            body['blocks'].append(dict(type='text',role='body',text=article.text))
                    except SourceError as error:
                        article = ArticleManifest(url=url,reason=error.label)
                        issues.append(replace(failure(error,publication).issue,scope='telegraph:'+url))
                    articles.append(article.model_dump(mode='json'))
                manifest['articles'] = articles
                value = replace(value,content=body,media=tuple(media_refs),needs_fetch=False,issues=tuple(issues))
        return value

    async def download_media(self, publication: ExternalReference, media: MediaReference,
                             output: BinaryIO) -> MediaReference:
        current = await self.fetch(publication)
        actual = next((item for item in current.media if item.key == media.key), None)
        if actual is None:
            for issue in current.issues:
                if issue.scope and issue.scope.startswith('telegraph:') and media.key.startswith(issue.scope+':'):
                    raise AdapterException(replace(issue,publication=publication,media_key=media.key))
        if actual is None or resource_identity(actual.locator) != resource_identity(media.locator):
            raise AdapterException(AdapterIssue('media_changed', 'Вложение изменилось',
                AdapterErrorKind.media_changed, publication, media.key))
        try:
            async with asyncio.timeout(180):
                size, _ = await Downloader(self.transport).write(actual.locator, output,
                    allow_empty=actual.mime_type is None or actual.mime_type.startswith(('application/','text/')))
        except SourceError as error:
            raise failure(error, publication, media.key, 'telegram:media:'+urlsplit(actual.locator).netloc) from error
        except TimeoutError as error:
            raise AdapterException(AdapterIssue('media-unavailable', 'Истекло время загрузки вложения',
                AdapterErrorKind.temporary, publication, media.key)) from error
        return replace(actual, size_bytes=size)
