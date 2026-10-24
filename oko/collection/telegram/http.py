"""Runtime-owned HTTP admission shared by Telegram collection and connections."""
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from oko.collection.contracts import SourceDeferred
from oko.collection.http import retry_after
from oko.collection.telegram.media import PublicTransport


class TelegramHTTP(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport | None, clock: Callable[[], float],
                 wall_clock: Callable[[], float], read: Callable[[str], dict[str, Any]],
                 write: Callable[[str, float | None, int], Awaitable[float]], admitted: Callable[[], bool],
                 generation: Callable[[], int], rate: Callable[[], float]) -> None:
        self._transport = transport or PublicTransport()
        self._clock, self._wall = clock, wall_clock
        self._read, self._write, self._admitted = read, write, admitted
        self._generation, self._rate = generation, rate
        self._last: dict[str, float] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        group = 'html' if request.url.host in ('t.me', 'telegram.me') else str(request.url.copy_with(path='/', query=None, fragment=None))
        while True:
            if not self._admitted():
                raise asyncio.CancelledError
            state = self._read(group)
            delay = float(state.get('until', 0)) - self._wall()
            if delay > 0:
                raise SourceDeferred('Telegram ограничил запросы; ожидаем разрешённого срока', delay, code='source_unavailable')
            spacing = self._last.get(group, float('-inf')) + 1/self._rate() - self._clock()
            if spacing <= 0:
                self._last[group] = self._clock()
                break
            await asyncio.sleep(min(spacing, .5))
        generation = self._generation()
        response = await self._transport.handle_async_request(request)
        if not self._admitted() or generation != self._generation():
            await response.aclose()
            raise SourceDeferred('Запрос Telegram прерван', code='source_unavailable')
        if response.status_code != 429:
            return response
        server_delay = retry_after(response,self._wall())
        try:
            until = await self._write(group, server_delay, generation)
        finally:
            await response.aclose()
        raise SourceDeferred('Telegram ограничил запросы; загрузка будет повторена после паузы', until-self._wall(), code='source_unavailable')

    async def aclose(self) -> None:
        # Individual AsyncClients borrow this runtime-owned transport.
        pass

    async def close(self) -> None:
        await self._transport.aclose()
