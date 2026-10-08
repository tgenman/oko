"""Finite RSS/Atom windows; content stays outside the state database."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from html import escape
import json
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import httpx
from .sources import Material, Publication, Image


class SourceError(Exception):
    def __init__(self, label: str, delay: float = 60) -> None:
        self.label, self.delay = label, delay


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


def entries(raw: bytes, base: str) -> list[Entry]:
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise SourceError('Неподдерживаемый XML источника')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
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
            continue
        date = value('pubDate') or value('published') or value('updated') or value('date')
        try:
            dt = parsedate_to_datetime(date) if ',' in date else datetime.fromisoformat(date.replace('Z', '+00:00'))
            stamp = dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, OverflowError):
            # Unknown date cannot justify excluding an otherwise new publication.
            stamp = time.time()
        identity = value('guid') or value('id') or url
        result.append(Entry(Publication(identity, url, stamp), value('title') or 'Без заголовка',
                            markup('description') or markup('summary') or markup('encoded') or markup('content')))
    counts: dict[str, int] = {}
    for entry in result:
        counts[entry.publication.id] = counts.get(entry.publication.id, 0) + 1
    return [Entry(Publication(e.publication.url if counts[e.publication.id] > 1 else e.publication.id, e.publication.url, e.publication.published), e.title, e.excerpt) for e in result]


class RSSSource:
    def __init__(self, url: str, initial_days: int, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url, self.initial_days, self.transport = normalized(url), initial_days, transport
        self.gap: str | None = None

    async def download(self, url: str, limit: int = 4_000_000) -> tuple[bytes, str, str]:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20, follow_redirects=True, max_redirects=5,
                                        trust_env=False, headers={'User-Agent': 'Oko/0.1 local reader'}) as client:
                async with client.stream('GET', normalized(url)) as response:
                    if response.status_code in (401, 403):
                        raise SourceError('Требуется авторизация')
                    if response.status_code == 429:
                        try:
                            delay = max(1., min(float(response.headers.get('retry-after', '60')), 86400.))
                        except ValueError:
                            delay = 60.
                        raise SourceError('Ограничение частоты', delay)
                    if response.status_code >= 400:
                        raise SourceError('Источник недоступен')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > limit:
                            raise SourceError('Превышен размер материала')
                    return bytes(data), str(response.url), response.headers.get('content-type', '').split(';')[0]
        except httpx.HTTPError:
            raise SourceError('Источник недоступен') from None

    async def read_entries(self) -> list[Entry]:
        raw, url, _ = await self.download(self.url)
        return entries(raw, url)

    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]:
        batch = await self.read_entries()
        previous = json.loads(cursor) if cursor else None
        cutoff = previous['since'] if previous else time.time() - self.initial_days * 86400
        self.gap = None
        if not batch or min(e.publication.published for e in batch) > cutoff:
            self.gap = 'RSS отдаёт ограниченное окно: полнота истории за период не подтверждена'
        # Keep the original import boundary. RSS order/dates are not a cursor:
        # replaying the finite window also captures delayed and out-of-order items.
        return [Publication(e.publication.id, e.publication.url, e.publication.published, Material(e.title, self.excerpt(e.excerpt), False, 'Анонс сохранён; загрузка статьи ожидается')) for e in batch if e.publication.published >= cutoff], json.dumps({'since': cutoff})

    @staticmethod
    def excerpt(html: str) -> str:
        parser = Article()
        parser.feed('<article>' + html + '</article>')
        return parser.text

    async def fetch(self, publication: Publication) -> Material:
        try:
            batch = await self.read_entries()
        except SourceError:
            batch = []
        entry = next((e for e in batch if e.publication.id == publication.id or e.publication.url == publication.url), None)
        title, excerpt = (entry.title, entry.excerpt) if entry else (publication.teaser.title, publication.teaser.text) if publication.teaser else ('Статья', '')
        fallback = Article()
        fallback.feed('<article>' + excerpt + '</article>')
        text = fallback.text
        images: list[Image] = []
        full, reason = False, 'Полный текст не извлечён; доступен анонс'
        urls = fallback.images
        base = publication.url
        try:
            raw, base, _ = await self.download(publication.url)
            article = Article()
            article.feed(raw.decode('utf-8', errors='replace'))
            if article.article and article.text:
                text, urls, full, reason = article.text, article.images, True, ''
        except SourceError as error:
            reason = error.label + '; доступен анонс'
        unique_urls = list(dict.fromkeys(urls))
        if len(unique_urls) > 30:
            full, reason = False, 'Не все изображения сохранены (лимит 30)'
        for url in unique_urls[:30]:
            try:
                raw, _, mime = await self.download(urljoin(base, url), 10_000_000)
                signatures = {'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff', 'image/gif': b'GIF8', 'image/webp': b'RIFF'}
                if mime not in signatures or not raw.startswith(signatures[mime]):
                    raise SourceError('Неподдерживаемое изображение')
                suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[mime]
                images.append(Image(f'image-{len(images)}.{suffix}', raw))
            except SourceError:
                full, reason = False, 'Не все изображения сохранены'
        return Material(title, text, full, reason, tuple(images))
