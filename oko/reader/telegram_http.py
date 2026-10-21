"""Runtime-owned HTTP admission shared by Telegram collection and connections."""
import asyncio
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
import math
from typing import Any

import httpx

from oko.collection.contracts import SourceDeferred
from oko.collection.telegram.media import PublicTransport


class TelegramHTTP(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport | None, clock: Callable[[], float],
                 wall_clock: Callable[[], float], read: Callable[[str], dict[str, Any]],
                 write: Callable[[str, dict[str, Any], int], Awaitable[None]], admitted: Callable[[], bool],
                 generation: Callable[[], int]) -> None:
        self._transport = transport or PublicTransport()
        self._clock, self._wall = clock, wall_clock
        self._read, self._write, self._admitted = read, write, admitted
        self._generation = generation
        self._next: dict[str, float] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        group = 'html' if request.url.host in ('t.me', 'telegram.me') else str(request.url.copy_with(path='/', query=None, fragment=None))
        while True:
            if not self._admitted():
                raise asyncio.CancelledError
            state = self._read(group)
            delay = float(state.get('until', 0)) - self._wall()
            if delay > 0:
                raise SourceDeferred('Telegram ограничил запросы; ожидаем разрешённого срока', delay, code='source_unavailable')
            spacing = self._next.get(group, 0) - self._clock()
            if spacing <= 0:
                self._next[group] = self._clock() + .5
                break
            await asyncio.sleep(min(spacing, .5))
        generation = self._generation()
        response = await self._transport.handle_async_request(request)
        if not self._admitted() or generation != self._generation():
            await response.aclose()
            raise asyncio.CancelledError
        if response.status_code != 429:
            return response
        state = self._read(group)
        strikes = min(int(state.get('strikes', 0)) + 1, 7)
        delay = min(60 * 2 ** (strikes - 1), 3600)
        value = response.headers.get('retry-after', '')
        try:
            server_delay = float(int(value)) if value.isdecimal() else parsedate_to_datetime(value).timestamp() - self._wall()
            if math.isfinite(server_delay):
                delay = max(delay, server_delay)
        except (ValueError, TypeError, OverflowError):
            pass
        until = max(float(state.get('until', 0)), self._wall() + delay)
        try:
            await self._write(group, dict(until=until, strikes=strikes), generation)
        finally:
            await response.aclose()
        raise SourceDeferred('Telegram ограничил запросы; загрузка будет повторена после паузы', until-self._wall(), code='source_unavailable')

    async def aclose(self) -> None:
        # Individual AsyncClients borrow this runtime-owned transport.
        pass

    async def close(self) -> None:
        await self._transport.aclose()
