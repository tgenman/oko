"""Runtime HTTP admission for a persisted wait shared by one remote origin."""
import asyncio
from collections.abc import Awaitable,Callable
from email.utils import parsedate_to_datetime
import math

import httpx

from oko.collection.contracts import SourceDeferred
from oko.collection.telegram.media import PublicTransport


def retry_after(response: httpx.Response, now: float) -> float | None:
    value=response.headers.get('retry-after','')
    try:
        delay=float(int(value)) if value.isdecimal() else parsedate_to_datetime(value).timestamp()-now
        return delay if math.isfinite(delay) else None
    except (ValueError,TypeError,OverflowError):
        return None


class SourceHTTP(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport | None, wall: Callable[[],float],
                 read: Callable[[str],float], write: Callable[[str,float | None,int],Awaitable[float]],
                 admitted: Callable[[],bool], generation: Callable[[],int]) -> None:
        self._transport=transport or PublicTransport()
        self._owns_transport=transport is None
        self._wall,self._read,self._write=wall,read,write
        self._admitted,self._generation=admitted,generation

    def _deferred(self, scope: str, until: float) -> SourceDeferred:
        error=SourceDeferred('Источник ограничил запросы; ожидаем разрешённого срока',
                             max(0,until-self._wall()),code='source-wait')
        error.scope=scope
        return error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self._admitted():
            raise asyncio.CancelledError
        scope='http:'+str(request.url.copy_with(path='/',query=None,fragment=None))
        until=self._read(scope)
        if until>self._wall():
            raise self._deferred(scope,until)
        generation=self._generation()
        response=await self._transport.handle_async_request(request)
        if not self._admitted() or generation!=self._generation():
            await response.aclose()
            raise asyncio.CancelledError
        if response.status_code!=429:
            return response
        try:
            until=await self._write(scope,retry_after(response,self._wall()),generation)
        finally:
            await response.aclose()
        raise self._deferred(scope,until)

    async def aclose(self) -> None:
        # Adapter clients borrow the Runtime's transport.
        pass

    async def close(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()
