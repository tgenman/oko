"""Bounded metadata fallback for an explicitly restricted forwarded Telegram post."""
import asyncio
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpcore
import httpx

from oko.collection import request_observation as observation
from oko.collection.contracts import SourceError


async def fragment(username: str, message: str, transport: httpx.AsyncBaseTransport | None) -> tuple[str, str]:
    url = f'https://t.me/{username}/{message}'
    try:
        async with asyncio.timeout(30), httpx.AsyncClient(transport=transport, timeout=20,
                follow_redirects=False, trust_env=False) as client:
            for _ in range(4):
                parsed = urlsplit(url)
                if (parsed.scheme != 'https' or parsed.hostname not in ('t.me', 'telegram.me')
                        or parsed.username or parsed.port or parsed.query or parsed.fragment
                        or parsed.path.lower() != f'/{username}/{message}'.lower()):
                    raise ValueError('redirect outside requested publication')
                with observation.request(url, purpose='publication'):
                    async with client.stream('GET', url) as response:
                        observation.response_received(response)
                        if response.is_redirect:
                            url = urljoin(url, response.headers['location'])
                            continue
                        response.raise_for_status()
                        data = bytearray()
                        async for part in response.aiter_bytes():
                            data.extend(part)
                            if len(data) > 2 * 1024 * 1024:
                                raise ValueError('metadata response too large')
                document = BeautifulSoup(bytes(data), 'html.parser')
                identities = [str(node.get('data-telegram-post', '')).lower()
                              for node in document.select('script[data-telegram-post]')]
                site = document.select_one('meta[property="og:site_name"]')
                text = document.select_one('meta[property="og:description"]')
                if (identities != [f'{username}/{message}'.lower()] or not site
                        or site.get('content') != 'Telegram' or not text
                        or not str(text.get('content', '')).strip()):
                    raise ValueError('metadata is not a confirmed publication excerpt')
                return str(text['content']).strip(), ''
            raise ValueError('too many redirects')
    except httpx.HTTPStatusError as error:
        return '', f'Не удалось получить фрагмент предпросмотра: HTTP {error.response.status_code}'
    except (httpx.HTTPError, httpcore.NetworkError, httpcore.ProtocolError,
            httpcore.TimeoutException, TimeoutError, OSError, ValueError, KeyError):
        return '', 'Не удалось получить подтверждённый фрагмент предпросмотра'
    # SourceDeferred from runtime HTTP admission and cancellation propagate.
