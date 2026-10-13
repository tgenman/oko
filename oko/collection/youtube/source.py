"""YouTube Atom updates and captions only; never downloads video/audio."""
from oko.diagnostics import record_caught
import asyncio
import json
import re
import sys
from dataclasses import replace
from typing import Protocol

import httpx

from oko.collection.rss import RSSSource, Entry, entries
from oko.collection.contracts import SourceError, SourceSkipped
from oko.collection.contracts import Image, Material, Publication
from oko.collection.youtube.access import CaptionsBlocked


class CaptionsClient(Protocol):
    async def fetch(self, video: str) -> str | None: ...


class LocalCaptions:
    """Bound the synchronous SDK in an owned, cancellable, content-free worker."""
    def __init__(self, proxy: str | None = None) -> None:
        self.proxy = proxy

    async def fetch(self, video: str) -> str | None:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'oko.collection.youtube.worker', video,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        assert process.stdout is not None
        try:
            async with asyncio.timeout(60):
                assert process.stdin is not None
                process.stdin.write(json.dumps(dict(proxy=self.proxy)).encode())
                await process.stdin.drain()
                process.stdin.close()
                raw = bytearray()
                while chunk := await process.stdout.read(65536):
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise SourceError('Субтитры превышают лимит; текст не обрезан')
                await process.wait()
            result = json.loads(raw)
            if process.returncode or not isinstance(result, dict):
                raise ValueError('worker')
            if result.get('skip'):
                raise SourceSkipped('Анонс будущей трансляции пропущен')
            if result.get('error'):
                if result.get('blocked'):
                    raise CaptionsBlocked(float(result.get('retry_after', 0)))
                raise SourceError.from_worker('Не удалось получить субтитры; проверьте подключение и повторите загрузку', result)
            text = result['text']
            if text is not None and not isinstance(text, str):
                raise ValueError('text')
            return text or None
        except (TimeoutError, ValueError, KeyError, OSError):
            record_caught('source.fetch')
            raise SourceError('Не удалось получить субтитры; повторите загрузку') from None
        finally:
            if process.returncode is None:
                process.kill()
            # Drain the killed child's pipe: wait() alone can hang when stdout
            # reached the asyncio stream limit during an oversized response.
            await process.communicate()


class YouTubeSource(RSSSource):
    changes = 'YouTube: повторно проверяются название и превью видео в окне Atom. Полученные субтитры сохраняются без повторных запросов. Удаление видео и правки вне этого окна не определяются.'
    def __init__(self, url: str, initial_days: int, captions: CaptionsClient,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(url, initial_days, transport)
        self.captions_client = captions
        self.want_captions = False

    async def read_entries(self) -> list[Entry]:
        # RSSSource owns finite-window discovery/cursors; only stable video IDs
        # and canonical links are accepted from the YouTube feed.
        raw, url, _ = await self.download(self.url)
        batch = entries(raw, url)
        result = []
        for entry in batch:
            match = re.fullmatch(r'yt:video:([A-Za-z0-9_-]{11})', entry.publication.id)
            if match:
                video = match[1]
                result.append(Entry(Publication(video, f'https://www.youtube.com/watch?v={video}', entry.publication.published, published_known=entry.publication.published_known), entry.title, entry.excerpt))
        return result

    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]:
        publications, next_cursor = await super().discover(cursor)
        return [replace(p, teaser=replace(p.teaser, text='', description=p.teaser.text))
                if p.teaser else p for p in publications], next_cursor

    async def fetch(self, publication: Publication, *, captions: CaptionsClient | None = None, request_captions: bool = True) -> Material:
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', publication.id):
            raise SourceError('Неверный ID YouTube')
        batch = await self.read_entries()
        entry = next((entry for entry in batch if entry.publication.id == publication.id), None)
        title = entry.title if entry else publication.teaser.title if publication.teaser else 'YouTube'
        description = self.excerpt(entry.excerpt) if entry else publication.teaser.description if publication.teaser else ''
        text, state, reason = '', 'not_requested', 'Субтитры не запрошены: включите тезисы или высокую важность'
        if publication.teaser and publication.teaser.captions == 'available':
            text, state, reason = publication.teaser.text, 'available', ''
        elif self.want_captions and request_captions:
            try:
                transcript = await (captions or self.captions_client).fetch(publication.id)
                text, state, reason = ((transcript, 'available', '') if transcript else ('', 'unavailable', 'Субтитры недоступны'))
            except SourceSkipped:
                raise
            except SourceError as error:
                record_caught('source.fetch')
                state, reason = 'error', error.label
        elif self.want_captions and publication.teaser:
            text, state, reason = publication.teaser.text, publication.teaser.captions or 'error', publication.teaser.reason
            if state == 'not_requested' or not publication.teaser.captions:
                state, reason = 'error', 'Попытки получения субтитров исчерпаны; повторите загрузку'
        images: list[Image] = []
        preview_complete = True
        try:
            raw, _, mime = await self.download(f'https://i.ytimg.com/vi/{publication.id}/hqdefault.jpg', 2_000_000)
            if mime == 'image/jpeg' and raw.startswith(b'\xff\xd8\xff'):
                images.append(Image('image-0.jpg', raw))
            elif mime == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n'):
                images.append(Image('image-0.png', raw))
            else:
                raise SourceError('Превью недоступно')
        except SourceError:
            record_caught('source.fetch')
            preview_complete = False
            if publication.teaser:
                images.extend(publication.teaser.images)
            reason = (reason + '; ' if reason else '') + 'Превью недоступно'
        return Material(title, text, state == 'available' and bool(images) and preview_complete, reason, tuple(images), captions=state, description=description)
