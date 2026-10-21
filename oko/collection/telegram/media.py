"""Isolated public HTTP downloads, streamed into a job-owned staging directory."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import re
import hashlib
from ipaddress import ip_address, IPv6Address
import socket
import ssl
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from oko.collection.contracts import DownloadStage, SourceError, SourceDeferred, StagedPayload, Attachment, Material, ResourceManifest

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


class PublicNetwork(httpcore.AnyIOBackend):
    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        public_url('https://'+('['+host+']' if ':' in host else host))
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM),timeout)
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
        async for part in self.response.aiter_stream():
            yield part

    async def aclose(self) -> None:
        await self.response.aclose()


class PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.pool = httpcore.AsyncConnectionPool(ssl_context=ssl.create_default_context(),
            network_backend=PublicNetwork(),max_connections=2,max_keepalive_connections=1,retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.pool.handle_async_request(httpcore.Request(method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme,host=request.url.raw_host,
                             port=request.url.port,target=request.url.raw_path),
            headers=request.headers.raw,content=request.stream,extensions=request.extensions))
        return httpx.Response(response.status,headers=response.headers,stream=CoreStream(response),extensions=response.extensions)

    async def aclose(self) -> None:
        await self.pool.aclose()


@dataclass(frozen=True)
class CollectedMedia:
    attachments: tuple[Attachment, ...]
    resources: tuple[ResourceManifest, ...]
    reasons: tuple[str, ...]
    error: SourceError | None


class Downloader:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    @asynccontextmanager
    async def open(self, url: str) -> AsyncIterator[httpx.Response]:
        try:
            async with httpx.AsyncClient(transport=self.transport or PublicTransport(),timeout=20,
                                         follow_redirects=False,trust_env=False) as client:
                for _ in range(5):
                    public_url(url)
                    async with client.stream('GET',url) as response:
                        if response.is_redirect:
                            url = urljoin(url,response.headers['location'])
                            continue
                        response.raise_for_status()
                        yield response
                        return
                raise SourceError('Слишком много перенаправлений вложения')
        except (httpx.HTTPError,httpcore.NetworkError,httpcore.TimeoutException,TimeoutError,OSError,KeyError) as error:
            delay,code = 60,'media-unavailable'
            if isinstance(error,httpx.HTTPStatusError):
                if error.response.status_code == 429:
                    value = error.response.headers.get('retry-after','60')
                    delay = max(1,min(86400,int(value))) if value.isdecimal() else 60
                elif error.response.status_code in (404,410):
                    code = 'media-not-found'
            raise SourceError('Не удалось загрузить вложение',delay,code=code,retryable=code!='media-not-found') from error

    async def metadata(self, url: str) -> Any:
        try:
            async with asyncio.timeout(30), self.open(url) as response:
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data)>2*1024*1024:
                        raise SourceError('Слишком большой ответ статьи',code='metadata-limit',retryable=False)
                return json.loads(data)
        except (ValueError,TimeoutError) as error:
            raise SourceError('Не удалось прочитать статью',code='article-unavailable') from error

    async def download(self, url: str, stage: DownloadStage, name: str) -> StagedPayload:
        if not stage.admitted():
            raise asyncio.CancelledError
        if not re.fullmatch(r'media-[0-9]+\.[a-z0-9]+',name):
            raise ValueError('media file basename')
        target = stage.path/('incoming-'+name)
        try:
            async with asyncio.timeout(180), self.open(url) as response:
                if not stage.admitted():
                    raise asyncio.CancelledError
                length = response.headers.get('content-length','')
                if length.isdecimal() and int(length)>MAX_MEDIA:
                    raise MediaLimit()
                if 'text/html' in response.headers.get('content-type','').lower():
                    raise SourceError('Сервер не предоставил файл вложения',code='media-not-file',retryable=False)
                digest,size = hashlib.sha256(),0
                with target.open('wb') as output:
                    async for part in response.aiter_bytes():
                        if not stage.admitted():
                            raise asyncio.CancelledError
                        if not size and part.lstrip().lower().startswith((b'<!doctype html',b'<html')):
                            raise SourceError('Сервер не предоставил файл вложения',code='media-not-file',retryable=False)
                        size += len(part)
                        if size>MAX_MEDIA:
                            raise MediaLimit()
                        output.write(part)
                        digest.update(part)
                if not size:
                    raise SourceError('Пустое вложение')
                return StagedPayload(target,size,digest.hexdigest())
        except BaseException as error:
            target.unlink(missing_ok=True)
            if isinstance(error,TimeoutError):
                raise SourceError('Истекло время загрузки вложения',code='media-unavailable') from error
            raise

    async def collect(self, expected: tuple[ExpectedMedia, ...], templates: tuple[ResourceManifest, ...],
                      previous: Material | None, stage: DownloadStage | None) -> CollectedMedia:
        old_resources = {r.key:r for r in previous.source_manifest.resources} if previous and previous.source_manifest else {}
        old_attachments = {a.name:a for a in previous.attachments} if previous else {}
        attachments: list[Attachment] = []
        resources: list[ResourceManifest] = []
        reasons: list[str] = []
        retry = None
        terminal = ('media-unsupported','media-address','media-not-found','media-not-file','metadata-limit')
        for media,resource in zip(expected,templates):
            old = old_resources.get(resource.key)
            name = old.name if old else resource.name
            resource = resource.model_copy(update={'name':name})
            retained = old_attachments.get(name)
            try:
                if not media.url:
                    raise SourceError(media.reason or 'Вложение недоступно',code='media-unsupported',retryable=False)
                if stage is None:
                    raise ValueError('Media requires an owned download stage')
                payload = await self.download(media.url,stage,name)
                attachments.append(Attachment(name,payload,resource.kind,resource.mime))
                resource = resource.model_copy(update={'status':'loaded','reason':'','sha256':payload.sha256})
            except SourceError as error:
                if isinstance(error, SourceDeferred):
                    if retry is not None and not retry.retryable:
                        raise retry
                    raise
                if retained and old:
                    attachments.append(retained)
                    resource = resource.model_copy(update={'status':'loaded','sha256':old.sha256,'reason':error.label})
                else:
                    resource = resource.model_copy(update={'status':'unsupported' if isinstance(error,MediaLimit) or error.code in terminal else 'missing','reason':error.label})
                    reasons.append(error.label)
                if retry is None or not error.retryable:
                    retry = error
            resources.append(resource)
        return CollectedMedia(tuple(attachments),tuple(resources),tuple(reasons),retry)
