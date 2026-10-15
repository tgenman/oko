"""Public-preview connection check; collection/history is implemented in #20."""
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx

from oko.collection.contracts import SourceError


async def preview(username: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    url = 'https://t.me/s/' + username
    try:
        async with httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False) as client:
            for _ in range(5):
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or parsed.hostname not in ('t.me', 'telegram.me') or parsed.username or parsed.port:
                    raise ValueError('redirect outside public Telegram')
                async with client.stream('GET', url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers['location'])
                        continue
                    response.raise_for_status()
                    data = bytearray()
                    async for part in response.aiter_bytes():
                        data.extend(part)
                        if len(data) > 2 * 1024 * 1024:
                            raise ValueError('preview too large')
                document = BeautifulSoup(bytes(data), 'html.parser')
                if document.select_one('.tgme_channel_history') is None:
                    raise ValueError('not a public channel history')
                if parsed.path.rstrip('/') != '/s/' + username:
                    raise ValueError('redirect changed source')
                title = document.select_one('.tgme_channel_info_header_title')
                return dict(username=username, title=title.get_text(' ', strip=True) if title else username, preview_url=url)
            raise ValueError('too many redirects')
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise SourceError('Публичный предпросмотр Telegram недоступен', code='source_unavailable') from error
