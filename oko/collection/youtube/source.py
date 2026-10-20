"""YouTube Atom updates and captions only; never downloads video/audio."""
from oko.diagnostics import record_caught
import asyncio
import json
import re
import sys
import time
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from oko.collection.rss import RSSSource, Entry, entries
from oko.collection.contracts import SourceError, SourceSkipped
from oko.collection.contracts import Image, Material, Publication, DiscoveryResult, FetchResult, FetchResources, FetchRequirements, SourceObservation, CaptionsClient
from oko.collection.youtube.history import HistoryClient, LocalHistory
from oko.collection.youtube.access import CaptionsBlocked


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
    fetch_requirements = FetchRequirements(captions=True)
    changes = 'YouTube: повторно проверяются название и превью видео в окне Atom. Полученные субтитры сохраняются без повторных запросов. Удаление видео и правки вне этого окна не определяются.'
    def __init__(self, url: str, initial_days: int, captions: CaptionsClient,
                 transport: httpx.AsyncBaseTransport | None = None,
                 history: HistoryClient | None = None) -> None:
        super().__init__(url, initial_days, transport)
        self.captions_client = captions
        self.want_captions = False
        self.history_client = history or LocalHistory()
        self.latest: dict[str, Entry] = {}
        self.rss_errors: list[str] = []
        self.rss_windows: dict[str, list[Entry]] = {}

    async def read_entries(self) -> list[Entry]:
        channel = parse_qs(urlsplit(self.url).query).get('channel_id', [''])[0]
        urls = [f'https://www.youtube.com/feeds/videos.xml?playlist_id={kind}{channel[2:]}'
                for kind in ('UULF', 'UULV')] if channel.startswith('UC') else [self.url]
        batch = []
        self.rss_errors = []
        self.rss_windows = {}
        for feed_url in urls:
            try:
                raw, url, _ = await self.download(feed_url)
                window = entries(raw, url)
                self.rss_windows[feed_url] = window
                batch.extend(window)
            except SourceError as error:
                record_caught('source.discover')
                self.rss_errors.append(error.label)
        result = []
        for entry in batch:
            match = re.fullmatch(r'yt:video:([A-Za-z0-9_-]{11})', entry.publication.id)
            if match:
                video = match[1]
                result.append(Entry(Publication(video, f'https://www.youtube.com/watch?v={video}', entry.publication.published, published_known=entry.publication.published_known), entry.title, entry.excerpt))
        self.latest = {entry.publication.id: entry for entry in result}
        return list(self.latest.values())

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        selection = self.initial_history
        previous: dict[str, Any] = json.loads(cursor) if cursor else {}
        # An existing frozen RSS window must not start backfill on upgrade.
        if selection and selection['mode'] == 'count' and (
                not selection.get('frozen') or selection.get('run') or 'youtube_history' in previous):
            return await self.discover_history(previous, selection)
        outcome = await self._discover_window(previous)
        publications, next_cursor = list(outcome.publications), outcome.cursor
        if not self.rss_windows and self.rss_errors:
            raise SourceError(' · '.join(self.rss_errors))
        if selection and selection['mode'] == 'date':
            # A previously unavailable RSS window must still obey the chosen date.
            publications = [p for p in publications if not p.published_known or p.published >= selection['since']]
        if self.rss_errors:
            self.gap = ' · '.join(self.rss_errors)
        return DiscoveryResult(tuple([replace(p, teaser=replace(p.teaser, text='', description=p.teaser.text))
                if p.teaser else p for p in publications]), next_cursor, SourceObservation(self.gap, self.changes))

    async def discover_history(self, previous: dict[str, Any], selection: dict[str, Any]) -> DiscoveryResult:
        batch = await self.read_entries()
        run = selection.get('run', 'initial')
        saved = previous.get('youtube_history', {})
        if saved.get('run') != run:
            saved = dict(run=run, baseline=[e.publication.id for e in batch], state={}, entries=[], complete=False,
                         since=time.time(), baseline_urls=list(self.rss_windows), waiting=False, resume=selection.get('resume', 0))
        # A recovered RSS window is not itself a set of new arrivals. Its first
        # successful response establishes the missing baseline; known dates can
        # still admit publications genuinely posted after this operation began.
        for url, window in self.rss_windows.items():
            if url not in saved.setdefault('baseline_urls', []):
                for entry in window:
                    p = entry.publication
                    if not p.published_known or p.published < saved['since']:
                        saved['baseline'].append(p.id.removeprefix('yt:video:'))
                saved['baseline_urls'].append(url)
        if saved.get('resume') != selection.get('resume', 0):
            saved.update(waiting=False, resume=selection.get('resume', 0), error=None)
        picked = False
        if not saved['complete'] and not saved['waiting'] and not selection.get('paused') and not selection.get('cancelled'):
            channel = parse_qs(urlsplit(self.url).query).get('channel_id', [''])[0]
            try:
                result = await self.history_client.fetch(channel, selection['count'], saved['state'])
                if result.get('diagnostic'):
                    try:
                        raise SourceError.from_worker('Не удалось подобрать историю YouTube', result['diagnostic'])
                    except SourceError:
                        record_caught('youtube.history')
                saved.update(state=result['state'], entries=result['entries'], complete=result['complete'],
                             error=result.get('error'), requests=result.get('requests', 0), waiting=not result['complete'])
                picked = True
            except SourceError as error:
                record_caught('youtube.history')
                saved.update(waiting=True, error=error.label)
        chosen = {entry['id'] for entry in saved['entries']}
        result_publications = []
        for entry in saved['entries']:
            identity = entry['id']
            current = self.latest.get(identity)
            if saved['complete'] and not picked and not current:
                continue
            result_publications.append(Publication(identity, f'https://www.youtube.com/watch?v={identity}',
                current.publication.published if current else 0,
                Material(current.title if current else entry.get('title', 'YouTube'), '', False, 'Загрузка материала ожидается',
                         description=self.excerpt(current.excerpt) if current else ''),
                published_known=bool(current and current.publication.published_known)))
        # The original RSS window cannot bypass N. Later arrivals are independent.
        for entry in batch:
            p = entry.publication
            if p.id not in chosen and p.id not in saved['baseline']:
                result_publications.append(replace(p, teaser=Material(entry.title, '', False,
                    'Загрузка материала ожидается', description=self.excerpt(entry.excerpt))))
        self.gap = ' · '.join(self.rss_errors) or None
        # Publication titles belong to the already prepared Material files,
        # not to a second content copy in SQLite's discovery cursor. Resuming
        # only needs IDs; freshly enumerated entries supply their own titles.
        for values in saved['state'].get('lists', {}).values():
            for value in values:
                value.pop('title', None)
        saved['entries'] = [dict(id=entry['id']) for entry in saved['entries']]
        next_cursor = dict(youtube_history=saved, history_enroll=list(chosen),
                           scan_complete=saved['complete'], history_waiting=saved['waiting'],
                           history_error=saved.get('error'), history_requests=saved.get('requests', 0))
        return DiscoveryResult(tuple(result_publications), json.dumps(next_cursor), SourceObservation(self.gap, self.changes))

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        captions, request_captions = resources.captions, resources.request_captions
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', publication.id):
            raise SourceError('Неверный ID YouTube')
        entry = self.latest.get(publication.id)
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
        return FetchResult(Material(title, text, state == 'available' and bool(images) and preview_complete, reason, tuple(images), captions=state, description=description), SourceObservation(self.gap, self.changes))
