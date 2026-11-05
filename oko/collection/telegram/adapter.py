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
from oko.collection.contracts import SourceUnavailable, SourceDeferred, SourceError
from oko.collection.telegram.telegraph import direct_articles
from oko.collection.telegram.contracts import TelegramPost
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
    """Resolve public links without inventing a Telegram numeric peer ID."""
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
    if isinstance(error, SourceUnavailable):
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




def content(post: TelegramPost, *, linked_descriptions: bool = False) -> PublicationContent:
    ref=reference(post.url)
    metadata=PublicationMetadata(ref,post.title,
        datetime.fromtimestamp(post.published,timezone.utc) if post.published is not None else None)
    extra={'telegram':{'members':list(post.members)}}
    if post.malformed:
        return PublicationContent(metadata,{'extra':extra},needs_fetch=True)
    blocks: list[dict[str, Any]] = []
    if post.text:
        blocks.append(dict(type='text', role='body', text=post.text))
    if post.reply_preview:
        blocks.append(dict(type='quote', **post.reply_preview))
    if post.description:
        blocks.append(dict(type='text',role='description',text=post.description))
    media = []
    issues = []
    name=urlsplit(post.url).path.strip('/').split('/')[0]
    for item in post.media:
        if item.url:
            member=re.fullmatch(r'telegram-web:([1-9][0-9]*):[0-9]+:.+',item.id)
            original=(f'https://t.me/{name}/{member[1]}' if member else
                      post.url if len(post.members)<=1 else None)
            media.append(MediaReference(item.id, item.url, item.original_name, item.mime, item.size, resource_identity(item.url),source_url=item.url,publication_url=original))
            role={'photo':'image','document':'attachment','unsupported':'attachment'}.get(item.kind,item.kind)
            blocks.append(dict(type='media', ref={'$media': item.id}, role=role))
        elif item.external:
            external=item.external.model_dump(exclude={'kind','source_url','poster_key'},exclude_none=True)
            blocks.append(dict(type='external_media',kind=item.external.kind,url=item.external.source_url,**external,
                               **({'poster_ref':item.external.poster_key} if item.external.poster_key else {})))
        else:
            issues.append(AdapterIssue('media_unavailable', item.reason or 'Вложение недоступно',
                AdapterErrorKind.unavailable, ref, item.id))
    return PublicationContent(metadata,dict(blocks=blocks,extra=extra),tuple(media),
        linked_publications=tuple(_article_reference(url) for url in direct_articles(post.text)) if linked_descriptions and not post.restricted else (),
        needs_fetch=bool(issues or post.restricted),issues=tuple(issues))


def _article_reference(url: str) -> ExternalReference:
    from oko.collection.web_adapter import reference as article_reference
    return article_reference(url)


def resource_identity(locator: str) -> str:
    url = urlsplit(locator)
    query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
             if key.lower() not in ('signature', 'expires')]
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, urlencode(query), ''))


def finish_window(cursor: str) -> str:
    state=decode(cursor) if cursor else dict(upper=0)
    return encode(state['upper']+1,state['upper'],done=True)


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
        upper = max((max(p.members) for p in page.posts), default=0)
        self._head = (name, upper, page)
        return FeedMetadata(reference('https://t.me/s/'+name), page.title, page.description,
                            encode(upper, upper) if upper else '')

    async def _window(self, feed: ExternalReference, start: int, upper: int,
                      *, count: int | None = None) -> tuple[list[TelegramPost], tuple[AdapterIssue, ...]]:
        name = username(feed)
        before = upper + 1
        posts: list[TelegramPost] = []
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
            for post in sorted(page.posts, key=lambda p: max(p.members), reverse=True):
                members = set(post.members)
                if max(members) < start or max(members) > upper:
                    continue
                existing = next((p for p in posts if members.intersection(p.members)), None)
                if existing is None:
                    posts.append(post)
                # Overlapping pages must not count one album twice.
            if (count is not None and len(posts) >= count) or not page.next_before or any(
                    min(p.members) <= start for p in page.posts):
                return sorted(posts, key=lambda p: max(p.members), reverse=True), tuple(issues)
            before = page.next_before

    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str:
        if offset < 0:
            raise ValueError('offset must be nonnegative')
        if not cursor or offset == 0:
            return cursor
        state = decode(cursor)
        posts, _ = await self._window(feed, 0, state['start'], count=offset+1)
        return encode(max(posts[min(offset, len(posts)-1)].members), state['upper']) if posts else ''

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
        start = max(selected[-1].members)+1 if selected else state['upper']+1
        values=[]
        for post in selected:
            value=content(post,linked_descriptions=self.linked_descriptions)
            if not value.content:
                value=replace(value,content={'extra':{'telegram':{'members':list(post.members)}}})
            values.append(value)
        return DiscoveryPage(tuple(values), encode(start, state['upper'], done=not more), more, issues)

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
        post = next((p for p in page.posts if identity in p.members or int(p.id) == identity), None)
        if post is None:
            raise failure(SourceUnavailable(), publication)
        value = content(post,linked_descriptions=self.linked_descriptions)
        if post.restricted:
            detail='Публикация недоступна для веб-просмотра'
            if post.text.strip():
                from oko.collection.telegram.preview import fragment
                try:
                    preview,preview_error=await fragment(name,post.id,self.transport)
                except SourceError as source_error:
                    raise failure(source_error,publication) from source_error
                if preview:value.content['blocks'].append(dict(type='text',role='body',text=preview))
                if preview_error:detail=preview_error
            issue=AdapterIssue('post-web-unavailable',detail,AdapterErrorKind.unavailable,publication)
            return replace(value,needs_fetch=True,issues=(*value.issues,issue))
        return value

    async def download_media(self, publication: ExternalReference, media: MediaReference,
                             output: BinaryIO) -> MediaReference:
        try:
            async with asyncio.timeout(180):
                size, _ = await Downloader(self.transport).write(media.locator, output,
                    allow_empty=media.mime_type is None or media.mime_type.startswith(('application/','text/')))
        except SourceError as error:
            raise failure(error, publication, media.key, 'telegram:media:'+urlsplit(media.locator).netloc) from error
        except TimeoutError as error:
            raise AdapterException(AdapterIssue('media-unavailable', 'Истекло время загрузки вложения',
                AdapterErrorKind.temporary, publication, media.key)) from error
        return replace(media, size_bytes=size)
