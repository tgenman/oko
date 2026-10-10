"""YouTube Atom updates and captions only; never downloads video/audio."""
import asyncio
import json
import re
import sys
from typing import Protocol

import httpx

from oko.collection.rss import RSSSource, Entry, entries
from oko.collection.contracts import SourceError
from oko.collection.contracts import Image, Material, Publication


class CaptionsClient(Protocol):
    async def fetch(self, video: str) -> str | None: ...


class LocalCaptions:
    """Bound the synchronous SDK in an owned, cancellable, content-free worker."""
    async def fetch(self, video: str) -> str | None:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'oko.collection.youtube.worker', video,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        assert process.stdout is not None
        try:
            async with asyncio.timeout(60):
                raw = bytearray()
                while chunk := await process.stdout.read(65536):
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise SourceError('Субтитры превышают лимит; текст не обрезан')
                await process.wait()
            result = json.loads(raw)
            if process.returncode or not isinstance(result, dict):
                raise ValueError('worker')
            if result.get('error'):
                raise SourceError('Не удалось получить субтитры; повторите загрузку')
            text = result['text']
            if text is not None and not isinstance(text, str):
                raise ValueError('text')
            return text or None
        except (TimeoutError, ValueError, KeyError, OSError):
            raise SourceError('Не удалось получить субтитры; повторите загрузку') from None
        finally:
            if process.returncode is None:
                process.kill()
            # Drain the killed child's pipe: wait() alone can hang when stdout
            # reached the asyncio stream limit during an oversized response.
            await process.communicate()


class YouTubeSource(RSSSource):
    changes = 'YouTube: повторно проверяются название, доступные субтитры и превью видео в окне Atom. Удаление видео и правки вне этого окна не определяются.'
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
                result.append(Entry(Publication(video, f'https://www.youtube.com/watch?v={video}', entry.publication.published), entry.title, ''))
        return result

    async def fetch(self, publication: Publication) -> Material:
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', publication.id):
            raise SourceError('Неверный ID YouTube')
        batch = await self.read_entries()
        entry = next((entry for entry in batch if entry.publication.id == publication.id), None)
        title = entry.title if entry else publication.teaser.title if publication.teaser else 'YouTube'
        text, state, reason = '', 'not_requested', 'Субтитры не запрошены: включите тезисы или высокую важность'
        if not self.want_captions and publication.teaser and publication.teaser.captions == 'available':
            text, state, reason = publication.teaser.text, 'available', ''
        if self.want_captions:
            try:
                transcript = await self.captions_client.fetch(publication.id)
                text, state, reason = ((transcript, 'available', '') if transcript else ('', 'unavailable', 'Субтитры недоступны'))
            except SourceError as error:
                if publication.teaser and publication.teaser.captions == 'available':
                    raise
                state, reason = 'error', error.label
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
            preview_complete = False
            if publication.teaser:
                images.extend(publication.teaser.images)
            reason = (reason + '; ' if reason else '') + 'Превью недоступно'
        return Material(title, text, state == 'available' and bool(images) and preview_complete, reason, tuple(images), captions=state)
