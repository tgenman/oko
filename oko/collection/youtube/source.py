"""YouTube Atom updates and captions only; never downloads video/audio."""
from oko.diagnostics import record_caught
from oko.collection import request_observation as observation
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
from oko.collection.contracts import Material, Publication, DiscoveryResult, FetchResult, SourceObservation, CaptionsClient
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
            if not result.get('error') or result.get('blocked'):
                observation.remote(result)
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
    changes = 'YouTube: video titles and previews are checked again within the Atom window. Downloaded subtitles are kept without further requests. Deleted videos and changes outside this window are not detected.'
    def __init__(self, url: str, initial_days: int, captions: CaptionsClient,
                 transport: httpx.AsyncBaseTransport | None = None,
                 history: HistoryClient | None = None) -> None:
        super().__init__(url, initial_days, transport)
        self.captions_client = captions
        self.want_captions = False
        self.history_client = history or LocalHistory()
