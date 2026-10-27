"""Finite RSS/Atom windows; content stays outside the state database."""
from oko.diagnostics import record_caught
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from html import escape
import json
import time
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import httpx
from oko.collection import request_observation as observation
from oko.collection.adapters import MediaReference
from oko.collection.contracts import Material, Publication, SourceError, SourceDeleted, DiscoveryResult, FetchResult, SourceObservation


def normalized(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise SourceError('Недопустимый адрес источника')
    host = parts.netloc.lower()
    if parts.scheme == 'https' and host.endswith(':443'):
        host = host[:-4]
    elif parts.scheme == 'http' and host.endswith(':80'):
        host = host[:-3]
    return urlunsplit((parts.scheme.lower(), host, parts.path or '/', parts.query, ''))


class Article(HTMLParser):
    """Extract an explicitly marked article, otherwise keep the feed excerpt."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[str] = []
        self.stack: list[tuple[str, bool, bool]] = []
        self.article = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        inherited = self.stack[-1][1] if self.stack else False
        skip = (self.stack[-1][2] if self.stack else False) or tag in ('script', 'style', 'nav', 'footer', 'aside', 'form')
        marked = tag in ('article', 'main') or any(c in (values.get('class') or '').split() for c in ('entry-content', 'post-content', 'article-body'))
        active = inherited or marked
        self.article |= marked
        if tag == 'img' and active and not skip:
            src = values.get('src') or values.get('data-src')
            if src:
                self.images.append(src)
        if active and not skip and tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n\n')
        if tag not in ('img', 'br', 'hr', 'meta', 'link', 'input', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track', 'col'):
            self.stack.append((tag, active, skip))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1][1] and not self.stack[-1][2]:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return '\n\n'.join(p.strip() for p in ''.join(self.parts).split('\n\n') if p.strip())


@dataclass(frozen=True)
class Entry:
    publication: Publication
    title: str
    excerpt: str
    media: tuple[MediaReference, ...] = ()


def feed_title(raw: bytes) -> str:
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise SourceError('Неподдерживаемый XML источника')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise SourceError('Неверный RSS/Atom') from error
    def local(tag: str) -> str:
        return tag.rsplit('}', 1)[-1]
    kind = local(root.tag)
    if kind not in ('rss', 'feed', 'RDF'):
        raise SourceError('Нужен RSS/Atom-фид, а не веб-страница')
    parent = root if kind == 'feed' else next((node for node in root if local(node.tag) == 'channel'), None)
    if parent is None:
        raise SourceError('Неверный RSS: отсутствует channel')
    title = next((node for node in parent if local(node.tag) == 'title'), None)
    return ''.join(title.itertext()).strip()[:200] if title is not None else ''


def feed_description(raw: bytes) -> str | None:
    # entries() has already validated the same bytes (including entity rejection).
    root = ET.fromstring(raw)
    def local(tag: str) -> str:
        return tag.rsplit('}', 1)[-1]
    parent = root if local(root.tag) == 'feed' else next((node for node in root if local(node.tag) == 'channel'), None)
    if parent is None:
        return None
    node = next((node for node in parent if local(node.tag) in ('description', 'subtitle')), None)
    return RSSSource.excerpt(''.join(node.itertext())) if node is not None else None


def entries(raw: bytes, base: str, *, strict: bool = False, diagnostics: list[str] | None = None) -> list[Entry]:
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise SourceError('Неподдерживаемый XML источника')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        record_caught('rss.entries')
        raise SourceError('Неверный RSS/Atom') from None
    def local(tag: str) -> str:
        return tag.rsplit('}', 1)[-1]
    if local(root.tag) not in ('rss', 'feed', 'RDF'):
        raise SourceError('Неверный RSS/Atom')
    result = []
    for node in root.iter():
        if local(node.tag) not in ('item', 'entry'):
            continue
        children = {local(c.tag): c for c in node}
        def value(name: str) -> str:
            child = children.get(name)
            return ''.join(child.itertext()).strip() if child is not None else ''
        def markup(name: str) -> str:
            child = children.get(name)
            if child is None:
                return ''
            def render(element: ET.Element) -> str:
                tag = local(element.tag)
                attrs = ''.join(f' {local(k)}="{escape(v, quote=True)}"' for k, v in element.attrib.items())
                return '<' + tag + attrs + '>' + escape(element.text or '') + ''.join(render(c) + escape(c.tail or '') for c in element) + '</' + tag + '>'
            return (child.text or '') + ''.join(render(c) + (c.tail or '') for c in child) if len(child) else value(name)
        entry_base = urljoin(urljoin(base, root.get('{http://www.w3.org/XML/1998/namespace}base', '')), node.get('{http://www.w3.org/XML/1998/namespace}base', ''))
        link = value('link')
        for child in node:
            if local(child.tag) == 'link' and child.get('rel', 'alternate') == 'alternate' and child.get('href'):
                link = child.get('href', '')
                break
        identity = value('guid') or value('id')
        if not link and not (strict and identity):
            if strict and diagnostics is not None:
                diagnostics.append('rss-missing-key')
            continue
        try:
            url = normalized(urljoin(entry_base, link)) if link else ''
        except (SourceError,ValueError):
            record_caught('rss.entries')
            if strict and identity and diagnostics is not None:
                diagnostics.append('rss-invalid-link')
            if not strict or not identity:
                if strict and diagnostics is not None:
                    diagnostics.append('rss-missing-key')
                continue
            url = ''
        date = value('pubDate') or value('published') or value('updated') or value('date')
        known = True
        try:
            dt = parsedate_to_datetime(date) if ',' in date else datetime.fromisoformat(date.replace('Z', '+00:00'))
            stamp = dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, OverflowError):
            # Unknown date cannot justify excluding an otherwise new publication.
            stamp, known = 0., False
        identity = value('guid') or value('id') or url
        media: list[MediaReference] = []
        if strict:
            for child in node:
                if not (local(child.tag) == 'enclosure' or local(child.tag) == 'link' and child.get('rel') == 'enclosure'):
                    continue
                locator = child.get('url') or child.get('href')
                if not locator:
                    continue
                try:
                    locator = normalized(urljoin(entry_base, locator))
                except SourceError:
                    continue
                length = child.get('length', '')
                size = int(length) if length.isascii() and length.isdecimal() else None
                media.append(MediaReference('rss:enclosure:'+str(len(media)), locator, '', child.get('type'), size))
        result.append(Entry(Publication(identity, url, stamp, published_known=known), value('title') or ('' if strict else 'Без заголовка'),
                            markup('description') or markup('summary') or markup('encoded') or markup('content')
                            or next((''.join(c.itertext()).strip() for c in node.iter()
                                     if c.tag == '{http://search.yahoo.com/mrss/}description'), ''), tuple(media)))
    if strict:
        unique: dict[str, Entry] = {}
        for entry in result:
            prior = unique.get(entry.publication.id)
            if prior is not None and prior != entry:
                raise SourceError('Разные записи RSS имеют один внешний ID',
                                  code='rss-identity-conflict', retryable=False)
            unique[entry.publication.id] = entry
        return list(unique.values())
    counts: dict[str, int] = {}
    for entry in result:
        counts[entry.publication.id] = counts.get(entry.publication.id, 0) + 1
    return [Entry(Publication(e.publication.url if counts[e.publication.id] > 1 else e.publication.id, e.publication.url, e.publication.published, published_known=e.publication.published_known), e.title, e.excerpt) for e in result]


class RSSSource:
    changes = 'RSS: articles are checked again within the available feed window; a local copy is kept after 404/410 responses. Disappearance from the window does not mean deletion.'
    def __init__(self, url: str, initial_days: int, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url, self.initial_days, self.transport = normalized(url), initial_days, transport
        self.gap: str | None = None
        self.initial_history: dict[str, Any] | None = None
        self.description: str | None = None

    async def download(self, url: str, limit: int = 4_000_000, *, purpose: str = 'feed') -> tuple[bytes, str, str]:
        try:
            with observation.request(url, purpose=purpose):
                return await self._download(url, limit, purpose)
        except httpx.HTTPError as error:
            record_caught('rss.download')
            raise SourceError('Источник недоступен') from error

    async def _download(self, url: str, limit: int, purpose: str) -> tuple[bytes, str, str]:
        async with httpx.AsyncClient(transport=self.transport, timeout=20, follow_redirects=True, max_redirects=5,
                                    trust_env=False, headers={'User-Agent': 'Oko/0.1 local reader'}) as client:
            async with client.stream('GET', normalized(url)) as response:
                if response.status_code not in (404, 410) or purpose != 'publication':
                    observation.response_failure(response, purpose=purpose, original_url=url)
                if response.status_code in (401, 403):
                    raise SourceError('Требуется авторизация')
                if response.status_code == 429:
                    try:
                        delay = max(1., min(float(response.headers.get('retry-after', '60')), 86400.))
                    except ValueError:
                        delay = 60.
                    raise SourceError('Ограничение частоты', delay)
                if response.status_code in (404, 410):
                    raise SourceDeleted()
                if response.status_code >= 400:
                    raise SourceError('Источник недоступен')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise SourceError('Превышен размер материала')
                return bytes(data), str(response.url), response.headers.get('content-type', '').split(';')[0]





    @staticmethod
    def excerpt(html: str) -> str:
        parser = Article()
        parser.feed('<article>' + html + '</article>')
        return parser.text
