"""RSS records are publications; an article URL is a direct linked publication."""
from datetime import datetime, timezone
import json
from typing import Any, BinaryIO

from bs4 import BeautifulSoup
import httpx

from oko.collection.adapter_errors import failure
from oko.collection.adapters import (AdapterException, AdapterErrorKind, AdapterIssue, DiscoveryPage,
    ExternalKey, ExternalReference, FeedMetadata, MediaReference, Platform, PublicationContent,
    PublicationMetadata)
from oko.collection.contracts import SourceError
from oko.collection.rss import entries, feed_description, feed_title, normalized, Entry
from oko.collection.rss_history import continuation_links
from oko.collection.telegram.media import Downloader
from oko.collection.web_adapter import reference, _Content


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class RSSAdapter:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.downloader = Downloader(transport)

    async def _read(self, url: str) -> tuple[bytes, str]:
        try:
            async with self.downloader.open(url, purpose='feed') as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4_000_000:
                        raise SourceError('Превышен размер RSS', retryable=False)
                return bytes(raw), str(response.url)
        except SourceError as error:
            if error.code=='media-unavailable':
                error.label,error.code='Не удалось загрузить RSS','rss-unavailable'
            raise failure(error, scope='rss:'+url) from error

    async def _window(self, url: str, *, archives: bool = True, wanted: set[str] | None = None,
                      before: tuple[str,int] | None = None) -> tuple[bytes, list[Entry], list[AdapterIssue]]:
        pending = [url]
        visited: set[str] = set()
        batch: dict[str, Entry] = {}
        issues: list[AdapterIssue] = []
        first = b''
        try:
            while pending:
                current = pending.pop(0)
                if current in visited:
                    continue
                if len(visited) >= 1000:
                    issues.append(AdapterIssue('rss-page-limit', 'Достигнут предел 1000 страниц RSS', AdapterErrorKind.notice))
                    break
                raw, base = await self._read(current)
                if not visited:
                    first = raw
                visited.add(current)
                visited.add(base)
                diagnostics: list[str] = []
                for entry in entries(raw, base, strict=True, diagnostics=diagnostics):
                    prior = batch.get(entry.publication.id)
                    if prior is not None and prior != entry:
                        raise SourceError('Разные записи RSS имеют один внешний ID',
                            code='rss-identity-conflict', retryable=False)
                    batch[entry.publication.id] = entry
                issues.extend(AdapterIssue(code, 'Запись RSS без внешнего ключа пропущена' if code=='rss-missing-key' else 'Неверная ссылка RSS; анонс сохранён', AdapterErrorKind.notice)
                              for code in diagnostics)
                if not archives or wanted is not None and wanted <= batch.keys():
                    break
                if before and before[0] in batch:
                    positions=list(batch)
                    if len(positions)-positions.index(before[0])-1 >= before[1]:
                        break
                links = continuation_links(raw, base)
                pending.extend(target for targets in links.values() for target in targets
                               if target not in visited and target not in pending)
            return first, list(batch.values()), issues
        except SourceError as error:
            raise failure(error, scope='rss:'+url) from error

    @staticmethod
    def _state(feed: ExternalReference, cursor: str) -> dict[str, Any]:
        try:
            state = json.loads(cursor) if cursor else dict(feed=feed.url,keys=[],position=0,done=True)
            if (feed.key != ExternalKey(Platform.rss, normalized(feed.url))
                    or not isinstance(state, dict) or state.get('feed') != feed.url
                    or not isinstance(state.get('keys'), list)
                    or not all(isinstance(key, str) and key for key in state['keys'])
                    or len(set(state['keys'])) != len(state['keys'])
                    or type(state.get('position')) is not int
                    or not 0 <= state['position'] <= len(state['keys'])
                    or type(state.get('done')) is not bool):
                raise ValueError('cursor')
            return state
        except (ValueError, TypeError, SourceError) as error:
            raise AdapterException(AdapterIssue('invalid-cursor', 'Недопустимый курсор RSS',
                AdapterErrorKind.permanent)) from error

    @staticmethod
    def _parsed_value(feed: str, entry: Entry) -> PublicationContent:
        pub = entry.publication
        # A record without a permalink opens its real feed; this is not an article link.
        metadata=RSSAdapter._metadata(feed,entry)
        content = _Content(pub.url or feed)
        soup = BeautifulSoup(entry.excerpt, 'html.parser')
        for child in soup.children:
            content.walk(child)
        content.flush()
        for media in entry.media:
            content.media.append(media)
            kind = (media.mime_type or '').split('/', 1)[0]
            content.blocks.append(dict(type='media', ref={'$media': media.key},
                                       role=kind if kind in ('image', 'video', 'audio') else 'attachment'))
        linked = (reference(pub.url),) if pub.url else ()
        return PublicationContent(metadata,
            {'blocks': content.blocks, 'extra': {}}, tuple(content.media), linked)

    @staticmethod
    def _metadata(feed: str, entry: Entry) -> PublicationMetadata:
        pub=entry.publication
        return PublicationMetadata(ExternalReference(ExternalKey(Platform.rss,_encode([feed,pub.id])),pub.url or feed),
            entry.title,datetime.fromtimestamp(pub.published,timezone.utc) if pub.published_known else None)

    @staticmethod
    def _value(feed: str, entry: Entry) -> PublicationContent:
        try:
            return RSSAdapter._parsed_value(feed,entry)
        except SourceError as error:
            raise failure(error,RSSAdapter._metadata(feed,entry).reference,scope='rss:'+feed) from error

    async def resolve_feed(self, url: str) -> FeedMetadata:
        try:
            url = normalized(url)
            raw, batch, _ = await self._window(url,archives=False)
            return FeedMetadata(ExternalReference(ExternalKey(Platform.rss, url), url),
                feed_title(raw), feed_description(raw) or '',
                _encode({'feed': url, 'keys': [e.publication.id for e in reversed(batch)],
                         'position': max(0, len(batch)-1), 'done': False}))
        except SourceError as error:
            raise failure(error, scope='rss:'+url) from error

    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str:
        state = self._state(feed, cursor)
        if offset < 0:
            raise ValueError('offset must be non-negative')
        if offset > state['position'] and state['keys']:
            position=min(state['position'],len(state['keys'])-1)
            anchor=state['keys'][position]
            _,batch,_=await self._window(feed.url,before=(anchor,offset-(state['position']-position)))
            keys=[entry.publication.id for entry in reversed(batch)]
            upper=state['keys'][-1]
            if anchor in keys and upper in keys:
                state['position']=keys.index(anchor)+(state['position']-position)
                state['keys']=keys[:keys.index(upper)+1]
        state['shortfall'] = max(0, offset-state['position'])
        state['position'] = max(0, state['position']-offset)
        return _encode(state)

    async def discover(self, feed: ExternalReference, cursor: str, limit: int) -> DiscoveryPage:
        state = self._state(feed, cursor)
        if limit <= 0:
            raise ValueError('limit must be positive')
        wanted=set(state['keys'][state['position']:state['position']+limit])
        _, entries_, issues = await self._window(feed.url,archives=not state['done'],wanted=wanted if not state['done'] else None)
        batch = {e.publication.id: e for e in entries_}
        if state['done']:
            state = {'feed': feed.url, 'keys': list(reversed(batch)), 'position': 0, 'done': False}
        keys = state['keys'][state['position']:state['position']+limit]
        publications=[]
        for key in keys:
            if key not in batch:
                continue
            try:
                publications.append(self._value(feed.url,batch[key]))
            except AdapterException as error:
                publications.append(PublicationContent(self._metadata(feed.url,batch[key]),
                    needs_fetch=True,issues=(error.issue,)))
        values=tuple(publications)
        if any(key not in batch for key in keys):
            issues.append(AdapterIssue('rss-window-changed', 'Часть записей исчезла из доступного окна RSS', AdapterErrorKind.notice))
        state['position'] += len(keys)
        state['done'] = state['position'] >= len(state['keys'])
        if state.pop('shortfall', 0):
            issues.append(AdapterIssue('history-shortfall', 'Доступно меньше публикаций, чем запрошено', AdapterErrorKind.notice))
        return DiscoveryPage(values, _encode(state), not state['done'], tuple(issues))

    async def fetch(self, publication: ExternalReference) -> PublicationContent:
        try:
            key = json.loads(publication.key.external_id)
            if (publication.key.platform != Platform.rss or not isinstance(key, list) or len(key) != 2
                    or not all(isinstance(part, str) and part for part in key)
                    or normalized(key[0]) != key[0]):
                raise ValueError('reference')
            feed, identity = key
        except (ValueError, TypeError, SourceError) as error:
            raise AdapterException(AdapterIssue('invalid-reference', 'Недопустимый внешний ключ RSS',
                AdapterErrorKind.permanent, publication)) from error
        _, batch, _ = await self._window(feed,wanted={identity})
        match = next((e for e in batch if e.publication.id == identity), None)
        if match is None:
            raise AdapterException(AdapterIssue('rss-entry-unavailable', 'Запись отсутствует в доступном RSS',
                AdapterErrorKind.unavailable, publication))
        return self._value(feed, match)

    async def download_media(self, publication: ExternalReference, media: MediaReference,
                             output: BinaryIO) -> MediaReference:
        value = await self.fetch(publication)
        current = next((item for item in value.media if item.key == media.key), None)
        if current is None or current.locator != media.locator:
            raise AdapterException(AdapterIssue('media-changed', 'Описание вложения изменилось',
                AdapterErrorKind.media_changed, publication, media.key))
        try:
            attachment = any(block.get('ref') == {'$media': media.key} and block.get('role') == 'attachment'
                             for block in value.content['blocks'])
            return await self.downloader.write_media(current,output,allow_empty=attachment)
        except SourceError as error:
            raise failure(error, publication, media.key, scope='rss:'+publication.key.external_id) from error
