"""The public HTTP transport preserves HTTPX failures at network and stream seams."""
import asyncio
from io import BytesIO
import socket

import httpcore
import httpx
import pytest

from oko.collection.adapters import AdapterErrorKind, AdapterException, MediaReference
from oko.collection.telegram.adapter import TelegramWebAdapter, publication_reference
from oko.collection.telegram.media import PublicTransport


def fail_core(monkeypatch, error, phase):
    class BrokenBody:
        async def __aiter__(self):
            raise error('synthetic network failure')
            yield b''

        async def aclose(self):
            pass

    async def request(pool, request):
        if phase == 'headers':
            raise error('synthetic network failure')
        return httpcore.Response(200, content=BrokenBody())

    monkeypatch.setattr(httpcore.AsyncConnectionPool, 'handle_async_request', request)


@pytest.mark.parametrize('core_error,public_error', [
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
])
@pytest.mark.parametrize('phase', ['headers', 'body'])
def test_network_and_body_failures_follow_httpx_contract(monkeypatch, core_error, public_error, phase):
    fail_core(monkeypatch, core_error, phase)

    async def scenario():
        async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False) as client:
            with pytest.raises(public_error) as caught:
                await client.get('https://source.example/publication')
            assert caught.value.request.url.host == 'source.example'
            assert isinstance(caught.value.__cause__, core_error)

    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['headers', 'body'])
def test_telegram_media_preview_timeout_remains_retryable(monkeypatch, phase):
    fail_core(monkeypatch, httpcore.ReadTimeout, phase)

    async def scenario():
        adapter = TelegramWebAdapter(PublicTransport())
        output = BytesIO()
        with pytest.raises(AdapterException) as caught:
            await adapter.download_media(publication_reference('https://t.me/fixture/1'),
                MediaReference('telegram-web:1:0:photo', 'https://cdn.example/image', 'image.jpg'), output)
        assert caught.value.issue.kind == AdapterErrorKind.temporary
        assert output.getvalue() == b'' and not output.closed

    asyncio.run(scenario())


@pytest.mark.parametrize('failure,public_error', [
    (TimeoutError('synthetic DNS timeout'), httpx.ConnectTimeout),
    (socket.gaierror(-3, 'synthetic DNS failure'), httpx.ConnectError),
])
def test_dns_failures_follow_httpx_connection_contract(monkeypatch, failure, public_error):
    async def scenario():
        async def dns(*args, **kwargs):
            raise failure
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', dns)
        async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False) as client:
            with pytest.raises(public_error):
                await client.get('https://source.example/publication')

    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['headers', 'body'])
def test_transport_preserves_task_cancellation_and_closes_received_body(monkeypatch, phase):
    async def scenario():
        started = asyncio.Event()
        closed = []

        class WaitingBody:
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b''

            async def aclose(self):
                closed.append(True)

        async def request(pool, request):
            if phase == 'headers':
                started.set()
                await asyncio.Event().wait()
            return httpcore.Response(200, content=WaitingBody())

        monkeypatch.setattr(httpcore.AsyncConnectionPool, 'handle_async_request', request)
        async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False) as client:
            task = asyncio.create_task(client.get('https://source.example/publication'))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if phase == 'body':
            assert closed == [True]

    asyncio.run(scenario())


def test_dns_resolution_to_private_address_remains_a_permanent_source_error(monkeypatch):
    from oko.collection.contracts import SourceError

    async def scenario():
        async def dns(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', dns)
        async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False) as client:
            with pytest.raises(SourceError) as caught:
                await client.get('https://source.example/publication')
        assert caught.value.code == 'media-address'
        assert caught.value.retryable is False

    asyncio.run(scenario())
