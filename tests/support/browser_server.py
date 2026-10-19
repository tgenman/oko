"""Production HTTP reader with temporary storage and synthetic RSS/model adapters."""
import json
from pathlib import Path
import secrets
import socket
import sys
from datetime import datetime, timezone

import httpx
import uvicorn

from tests.support.app import create_app
from tests.support.models import model_transport
from tests.support.codex import CodexService
from tests.support.youtube import PNG, History, NoCaptions
from tests.support.telegram import Telegram
from tests.support.telegram_web import page


class BrowserTelegram(Telegram):
    async def accounts(self):
        return ['one', 'two']

    async def identity(self, account):
        return {'one': 9007199254740993, 'two': 202}[account]

    async def info(self, account, peer):
        result = await super().info(account, peer)
        result['peer'] = -100101 if str(peer).lstrip('@') == 'personal' else -100202
        return result

    async def history_page(self, account, peer, upper, before, since):
        result = await self.page(account, peer, 0, since)
        return dict(messages=result['messages'], upper=11, next_before=11, more=False)


def response(request):
    if request.url.host == 't.me':
        return httpx.Response(200, text=page([(11, 'Веб-публикация без аккаунта <a href="https://telegra.ph/Fixture">Статья</a>')]))
    if request.url.host == 'api.telegra.ph':
        assert request.url.path == '/getPage/Fixture'
        return httpx.Response(200, json={'ok': True, 'result': {'title': 'Статья Telegraph', 'content': [
            {'tag': 'p', 'children': ['Полный текст статьи сохранён локально.']},
            {'tag': 'img', 'attrs': {'src': '/file/fixture.png'}},
            {'tag': 'iframe', 'attrs': {'src': 'https://example.org/embed'}},
        ]}})
    if request.url.host == 'telegra.ph':
        assert request.url.path == '/file/fixture.png'
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.host == 'i.ytimg.com':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.host == 'www.youtube.com':
        assert request.url.path == '/feeds/videos.xml'
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Synthetic channel</title></feed>')
    assert request.url.host in ('a.test', 'b.test', 'empty.test', 'source.test'), request.url
    if request.url.path == '/image.png':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.path == '/feed' or request.url.host == 'source.test':
        date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        item = '' if request.url.host == 'empty.test' else f'<item><guid>article</guid><title>Статья {request.url.host}</title><link>https://{request.url.host}/article</link><pubDate>{date}</pubDate><description>Анонс библиотеки.</description></item>'
        return httpx.Response(200, text=f'<rss version="2.0"><channel><title>Синтетический источник</title>{item}</channel></rss>')
    assert request.url.path == '/article'
    return httpx.Response(200, text=f'<article><p>Открылась библиотека на 24 места.</p><img src="https://{request.url.host}/image.png"></article>')


def build_app(root, token, *, persistent=False, public_url=None):
    return create_app(root, token=token, rss_enabled=True, persistent=persistent, public_url=public_url,
                     rss_transport=httpx.MockTransport(response), telegram_client=BrowserTelegram(), ai_transport=model_transport([]), codex_client=CodexService(),
                     youtube_history=History([f'v{n:010d}' for n in range(60, 0, -1)]), youtube_captions=NoCaptions(),
                     lease=60, grace=60)


if __name__ == '__main__':
    token = secrets.token_urlsafe(24)
    app = build_app(Path(sys.argv[1]), token)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    print(json.dumps({'origin': f'http://127.0.0.1:{sock.getsockname()[1]}', 'token': token}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
