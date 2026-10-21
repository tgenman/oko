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
from oko.collection.contracts import Material, Publication, Image, SourceError, SourceDeleted, DiscoveryResult, FetchResult, FetchResources, FetchRequirements, SourceObservation


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


def entries(raw: bytes, base: str) -> list[Entry]:
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
        if not link:
            continue
        try:
            url = normalized(urljoin(entry_base, link))
        except SourceError:
            record_caught('rss.entries')
            continue
        date = value('pubDate') or value('published') or value('updated') or value('date')
        known = True
        try:
            dt = parsedate_to_datetime(date) if ',' in date else datetime.fromisoformat(date.replace('Z', '+00:00'))
            stamp = dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, OverflowError):
            # Unknown date cannot justify excluding an otherwise new publication.
            stamp, known = 0., False
        identity = value('guid') or value('id') or url
        result.append(Entry(Publication(identity, url, stamp, published_known=known), value('title') or 'Без заголовка',
                            markup('description') or markup('summary') or markup('encoded') or markup('content')
                            or next((''.join(c.itertext()).strip() for c in node.iter()
                                     if c.tag == '{http://search.yahoo.com/mrss/}description'), '')))
    counts: dict[str, int] = {}
    for entry in result:
        counts[entry.publication.id] = counts.get(entry.publication.id, 0) + 1
    return [Entry(Publication(e.publication.url if counts[e.publication.id] > 1 else e.publication.id, e.publication.url, e.publication.published, published_known=e.publication.published_known), e.title, e.excerpt) for e in result]


class RSSSource:
    fetch_requirements = FetchRequirements()
    changes = 'RSS: повторно проверяются статьи в доступном окне фида; 404/410 сохраняют локальную копию. Исчезновение из окна не означает удаление.'
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
                if response.status_code not in (404, 410):
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

    async def read_entries(self) -> list[Entry]:
        raw, url, _ = await self.download(self.url)
        result = entries(raw, url)
        self.description = feed_description(raw)
        return result

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        previous = json.loads(cursor) if cursor else None
        if self.initial_history and (self.initial_history.get('run') or not (previous and 'history_ids' in previous)):
            return await self._discover_rss_history(previous)
        return await self._discover_window(previous)

    async def _discover_window(self, previous: dict[str, Any] | None) -> DiscoveryResult:
        batch = await self.read_entries()
        if self.initial_history:
            if previous and 'history_ids' in previous:
                excluded = set(previous['excluded'])
                chosen = [e for e in batch if e.publication.id not in excluded]
                next_cursor = previous
            else:
                selection = self.initial_history
                unique = list({e.publication.id: e for e in batch}.values())
                known_dates = [e.publication.published for e in unique if e.publication.published_known]
                descending = all(a >= b for a, b in zip(known_dates, known_dates[1:]))
                has_unknown = any(not e.publication.published_known for e in unique)
                # When a date is absent only the producer's order can place it.
                ordered = unique if has_unknown else sorted(unique, key=lambda e: e.publication.published, reverse=True)
                chosen = ordered[:selection['count']] if selection['mode'] == 'count' else [e for e in unique if not e.publication.published_known or e.publication.published >= selection['since']]
                warnings = []
                if selection['mode'] == 'date':
                    warnings.append('RSS отдаёт ограниченное окно: полнота истории за период не подтверждена')
                elif has_unknown and (len(known_dates) < 2 or not descending):
                    warnings.append('Порядок RSS неоднозначен: точность выбора последних N не подтверждена')
                selected = {e.publication.id for e in chosen}
                next_cursor = {'history_ids': list(selected), 'excluded': [e.publication.id for e in batch if e.publication.id not in selected], 'since': time.time(), 'warnings': warnings}
            self.gap = ' · '.join(next_cursor.get('warnings', [])) or None
            return DiscoveryResult(tuple([Publication(e.publication.id, e.publication.url, e.publication.published, Material(e.title, self.excerpt(e.excerpt), False, 'Анонс сохранён; загрузка статьи ожидается'), published_known=e.publication.published_known) for e in chosen]), json.dumps(next_cursor), SourceObservation(self.gap, self.changes, description=self.description))
        cutoff = previous['since'] if previous else time.time() - self.initial_days * 86400
        self.gap = None
        if not batch or min(e.publication.published for e in batch) > cutoff:
            self.gap = 'RSS отдаёт ограниченное окно: полнота истории за период не подтверждена'
        # Keep the original import boundary. RSS order/dates are not a cursor:
        # replaying the finite window also captures delayed and out-of-order items.
        return DiscoveryResult(tuple([Publication(e.publication.id, e.publication.url, e.publication.published, Material(e.title, self.excerpt(e.excerpt), False, 'Анонс сохранён; загрузка статьи ожидается'), published_known=e.publication.published_known) for e in batch if not e.publication.published_known or e.publication.published >= cutoff]), json.dumps({'since': cutoff}), SourceObservation(self.gap, self.changes, description=self.description))

    async def _discover_rss_history(self, previous: dict[str, Any] | None) -> DiscoveryResult:
        from oko.collection.rss_history import RSSHistory
        assert self.initial_history is not None
        walk = RSSHistory(self.url, self.initial_history, previous)
        if walk.complete:
            batch = await self.read_entries()
            chosen = walk.updates(batch)
        else:
            try:
                raw, final, _ = await self.download(walk.url)
                batch = entries(raw, final)
                if walk.requests == 0:
                    self.description = feed_description(raw)
                chosen = walk.accept(batch, raw, final)
            except (SourceError, ValueError, httpx.InvalidURL) as error:
                record_caught('rss.history')
                walk.fail(error.label if isinstance(error, SourceError) else 'Недопустимый адрес источника')
                chosen = []
        self.gap = ' · '.join(walk.warnings) or None
        publications = tuple(Publication(e.publication.id, e.publication.url, e.publication.published,
            Material(e.title, self.excerpt(e.excerpt), False, 'Анонс сохранён; загрузка статьи ожидается'),
            published_known=e.publication.published_known) for e in chosen)
        return DiscoveryResult(publications, json.dumps(walk.cursor()),
            SourceObservation(self.gap, self.changes, description=self.description),
            continuation=None if walk.complete else 0.)

    @staticmethod
    def excerpt(html: str) -> str:
        parser = Article()
        parser.feed('<article>' + html + '</article>')
        return parser.text

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        try:
            batch = await self.read_entries()
        except SourceError:
            record_caught('rss.fetch')
            batch = []
        entry = next((e for e in batch if e.publication.url == publication.url), None)
        title, excerpt = (entry.title, entry.excerpt) if entry else (publication.teaser.title, publication.teaser.text) if publication.teaser else ('Статья', '')
        fallback = Article()
        fallback.feed('<article>' + excerpt + '</article>')
        text = fallback.text
        images: list[Image] = []
        full, reason = False, 'Полный текст не извлечён; доступен анонс'
        collection_error: SourceError | None = None
        urls = fallback.images
        base = publication.url
        try:
            raw, base, _ = await self.download(publication.url, purpose='publication')
            article = Article()
            article.feed(raw.decode('utf-8', errors='replace'))
            if article.article and article.text:
                text, urls, full, reason = article.text, article.images, True, ''
        except SourceDeleted as error:
            collection_error = error
            if publication.teaser and publication.teaser.full:
                raise
            reason = 'Публикация недоступна; доступен анонс'
        except SourceError as error:
            collection_error = error
            record_caught('rss.fetch')
            if publication.teaser and publication.teaser.full:
                raise
            reason = error.label + '; доступен анонс'
        unique_urls = list(dict.fromkeys(urls))
        if len(unique_urls) > 30:
            full, reason = False, 'Не все изображения сохранены (лимит 30)'
        for position, url in enumerate(unique_urls[:30]):
            try:
                raw, _, mime = await self.download(urljoin(base, url), 10_000_000, purpose='attachment')
                signatures = {'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff', 'image/gif': b'GIF8', 'image/webp': b'RIFF'}
                if mime not in signatures or not raw.startswith(signatures[mime]):
                    raise SourceError('Неподдерживаемое изображение')
                suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[mime]
                images.append(Image(f'image-{position}.{suffix}', raw))
            except SourceError as error:
                record_caught('rss.fetch')
                if not isinstance(error, SourceDeleted):
                    collection_error = collection_error or error
                full, reason = False, 'Не все изображения сохранены'
                if publication.teaser:
                    images.extend(image for image in publication.teaser.images if image.name.startswith(f'image-{position}.'))
        return FetchResult(Material(title, text, full, reason, tuple(images), collection_error=collection_error), SourceObservation(self.gap, self.changes, description=self.description))
