"""Isolated public HTTP downloads, streamed into a job-owned staging directory."""
import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
import json
from email.message import Message
import re
import hashlib
from ipaddress import ip_address, IPv6Address
import socket
import ssl
from typing import Any, BinaryIO
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
from oko.collection import request_observation as observation
from oko.collection.adapters import MediaReference
from oko.diagnostics import redact

from oko.collection.contracts import SourceError, SourceDeferred, Material, ResourceManifest

from oko.collection.telegram.contracts import ExpectedMedia

MAX_MEDIA = 200*1024*1024


class MediaLimit(SourceError):
    def __init__(self) -> None:
        super().__init__('Вложение превышает лимит 200 МиБ',code='media-too-large',retryable=False)


def public_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in ('https','http') or not host or parsed.username or parsed.password or parsed.port not in (None,80,443):
            raise ValueError('invalid public HTTP address')
        if host.lower() == 'localhost' or host.lower().endswith(('.localhost','.local')) or '%' in host:
            raise ValueError('local address')
        try:
            address = ip_address(host)
        except ValueError:
            return url
        mapped = address.ipv4_mapped if isinstance(address,IPv6Address) else None
        if not address.is_global or (mapped is not None and not mapped.is_global):
            raise ValueError('private address')
        return url
    except ValueError:
        raise SourceError('Небезопасный адрес вложения',code='media-address',retryable=False) from None


_CORE_ERRORS: dict[type[Exception], type[httpx.RequestError]] = {
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.WriteTimeout: httpx.WriteTimeout,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.WriteError: httpx.WriteError,
    httpcore.ProxyError: httpx.ProxyError,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
    httpcore.ProtocolError: httpx.ProtocolError,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
}


@contextmanager
def _httpx_errors() -> Iterator[None]:
    """The custom network backend retains HTTPX's public exception contract."""
    try:
        yield
    except tuple(_CORE_ERRORS) as error:
        mapped: type[httpx.RequestError] | None = None
        for core_error, public_error in _CORE_ERRORS.items():
            if isinstance(error, core_error) and (mapped is None or issubclass(public_error, mapped)):
                mapped = public_error
        assert mapped is not None
        raise mapped(str(error)) from error


class PublicNetwork(httpcore.AnyIOBackend):
    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        public_url('https://'+('['+host+']' if ':' in host else host))
        try:
            records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM),timeout)
        except TimeoutError as error:
            raise httpcore.ConnectTimeout('DNS lookup timed out') from error
        except socket.gaierror as error:
            raise httpcore.ConnectError('DNS lookup failed') from error
        addresses = list(dict.fromkeys(str(row[4][0]) for row in records))
        if not addresses or any(not ip_address(address).is_global for address in addresses):
            raise SourceError('Небезопасный адрес вложения',code='media-address',retryable=False)
        # Connect to the verified literal IP. httpcore retains the original host
        # for Host/TLS SNI; a second DNS lookup cannot change the destination.
        return await super().connect_tcp(addresses[0],port,timeout,local_address,socket_options)


class CoreStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response) -> None:
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        with _httpx_errors():
            async for part in self.response.aiter_stream():
                yield part

    async def aclose(self) -> None:
        await self.response.aclose()


class PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.pool = httpcore.AsyncConnectionPool(ssl_context=ssl.create_default_context(),
            network_backend=PublicNetwork(),max_connections=2,max_keepalive_connections=1,retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        with _httpx_errors():
            response = await self.pool.handle_async_request(httpcore.Request(method=request.method,
                url=httpcore.URL(scheme=request.url.raw_scheme,host=request.url.raw_host,
                                 port=request.url.port,target=request.url.raw_path),
                headers=request.headers.raw,content=request.stream,extensions=request.extensions))
        return httpx.Response(response.status,headers=response.headers,stream=CoreStream(response),extensions=response.extensions)

    async def aclose(self) -> None:
        await self.pool.aclose()




class Downloader:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    @asynccontextmanager
    async def open(self, url: str, *, purpose: str = 'attachment') -> AsyncIterator[httpx.Response]:
        original_url = url
        try:
            async with httpx.AsyncClient(transport=self.transport or PublicTransport(),timeout=20,
                                         follow_redirects=False,trust_env=False) as client:
                for _ in range(5):
                    public_url(url)
                    with observation.request(url, purpose=purpose, original_url=original_url):
                        async with client.stream('GET',url) as response:
                            observation.response_received(response)
                            if response.is_redirect:
                                url = urljoin(url,response.headers['location'])
                                continue
                            response.raise_for_status()
                            yield response
                            return
                raise SourceError('Слишком много перенаправлений вложения')
        except (httpx.HTTPError,httpcore.NetworkError,httpcore.TimeoutException,httpcore.ProtocolError,TimeoutError,OSError,KeyError) as error:
            delay,code = 60,'media-unavailable'
            label = 'Не удалось загрузить вложение'
            if isinstance(error, (httpcore.ProtocolError, httpx.ProtocolError)):
                label = 'Не удалось загрузить вложение: соединение оборвано до завершения передачи'
            elif isinstance(error, (httpcore.TimeoutException, httpx.TimeoutException, TimeoutError)):
                label = 'Не удалось загрузить вложение: истекло время ожидания'
            if isinstance(error,httpx.HTTPStatusError):
                label += f': HTTP {error.response.status_code}'
                if error.response.status_code == 429:
                    value = error.response.headers.get('retry-after','60')
                    delay = max(1,min(86400,int(value))) if value.isdecimal() else 60
                elif error.response.status_code in (404,410):
                    code = 'media-not-found'
            raise SourceError(label,delay,code=code,retryable=code!='media-not-found') from error

    async def metadata(self, url: str) -> Any:
        try:
            async with asyncio.timeout(30), self.open(url, purpose='publication') as response:
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data)>2*1024*1024:
                        raise SourceError('Слишком большой ответ статьи',code='metadata-limit',retryable=False)
                return json.loads(data)
        except (ValueError,TimeoutError) as error:
            raise SourceError('Не удалось прочитать статью',code='article-unavailable') from error

    async def write(self, url: str, output: BinaryIO, admitted: Callable[[], bool] = lambda: True, *, allow_empty: bool = False) -> tuple[int, str]:
        """Validate and stream one resource; the caller owns its output file."""
        async with self.open(url) as response:
            return await self._write(response,output,admitted,allow_empty=allow_empty)

    async def write_media(self, media: MediaReference, output: BinaryIO, *, allow_empty: bool = False) -> MediaReference:
        async with self.open(media.locator) as response:
            size,_=await self._write(response,output,lambda:True,allow_empty=allow_empty)
            disposition=Message()
            disposition['Content-Disposition']=response.headers.get('content-disposition','')
            filename=disposition.get_filename()
            mime=response.headers.get('content-type','').split(';',1)[0].strip() or media.mime_type
            return replace(media,size_bytes=size,mime_type=mime,name=filename or media.name)

    async def _write(self, response: httpx.Response, output: BinaryIO, admitted: Callable[[],bool], *, allow_empty: bool) -> tuple[int,str]:
        if not admitted():
            raise asyncio.CancelledError
        length = response.headers.get('content-length','')
        if length.isdecimal() and int(length)>MAX_MEDIA:
            raise MediaLimit()
        if 'text/html' in response.headers.get('content-type','').lower():
            raise SourceError('Сервер не предоставил файл вложения',code='media-not-file',retryable=False)
        digest,size = hashlib.sha256(),0
        async for part in response.aiter_bytes():
            if not admitted():
                raise asyncio.CancelledError
            if not size and part.lstrip().lower().startswith((b'<!doctype html',b'<html')):
                raise SourceError('Сервер не предоставил файл вложения',code='media-not-file',retryable=False)
            size += len(part)
            if size>MAX_MEDIA:
                raise MediaLimit()
            output.write(part)
            digest.update(part)
        if not size and not allow_empty:
            raise SourceError('Пустое вложение')
        return size, digest.hexdigest()
