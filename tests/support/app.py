"""Production reader assembled with synthetic external defaults for tests."""
import inspect
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from oko.reader.app import create_app as production_app
from oko.processing.codex import CodexClient
from oko.processing.contracts import ProcessingError
from oko.processing.execution import CodexChoice


class UnavailableCodex(CodexClient):
    async def check(self) -> dict[str, Any]:
        return dict(available=False, local=False, auth='synthetic', version=None,
                    limits_checked=False, reason='Синтетический Codex не настроен')

    async def catalog(self) -> list[dict[str, Any]]:
        raise ProcessingError('Синтетический каталог Codex не настроен')

    async def complete(self, prompt: str, schema: dict[str, Any], directory: Path, choice: CodexChoice) -> str:
        raise AssertionError('Provide a synthetic CodexClient explicitly for generation')


class NoCaptions:
    async def fetch(self, video: str) -> str | None:
        return None


class EmptyHistory:
    async def fetch(self, channel: str, count: int, state: dict[str, Any]) -> dict[str, Any]:
        return dict(entries=[], state={}, complete=True, error=None, requests=0)


class RejectTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('Tests require an explicit synthetic transport for HTTP', request=request)


class OwnedServerTransport(httpx.AsyncBaseTransport):
    """Explicit opt-in to the exact origin of a server started by the test."""
    def __init__(self, origin: str) -> None:
        url = httpx.URL(origin)
        try:
            allowed = url.host == 'localhost' or ip_address(url.host).is_loopback
        except ValueError:
            allowed = False
        if not allowed or url.scheme not in ('http', 'https') or url.path not in ('', '/') or url.query or url.fragment or url.username:
            raise ValueError('Expected the origin of an owned loopback fixture server')
        self.origin = (url.scheme, url.host, url.port)
        self.transport = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (request.url.scheme, request.url.host, request.url.port) != self.origin:
            raise httpx.ConnectError('Request is outside the owned fixture origin', request=request)
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self.transport.aclose()


def create_app(root: Path, **options: Any) -> FastAPI:
    defaults: dict[str, Any] = dict(codex_client=UnavailableCodex(), youtube_captions=NoCaptions(), youtube_history=EmptyHistory(),
                    rss_transport=RejectTransport(), ai_transport=RejectTransport())
    if 'youtube_history' not in inspect.signature(production_app).parameters:
        defaults.pop('youtube_history')
    # None also means use the synthetic default, just like production assembly.
    defaults.update({key: value for key, value in options.items() if value is not None})
    return production_app(root, **defaults)
