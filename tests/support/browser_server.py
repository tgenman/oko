"""Production HTTP reader with temporary storage and synthetic RSS/model adapters."""
import json
import asyncio
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
from tests.support.telegram_web import page, external_page, AUDIO, VIDEO, mark_post_unavailable


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
    if request.url.host == 'cdn.example':
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    if request.url.host == 't.me' and any('/'+name in request.url.path for name in ('externalmedia','postunavailable')):
        username = 'postunavailable' if '/postunavailable' in request.url.path else 'externalmedia'
        posts = [(11,'External audio post'+AUDIO),(12,'External video post'+VIDEO)]
        if username == 'externalmedia':
            from tests.support.telegram_web import DOCUMENT
            posts.append((13,'External document post'+DOCUMENT))
        if username == 'postunavailable':
            posts.append((13,''))
        html = external_page(posts,before=int(request.url.params.get('before','0')))
        if username == 'postunavailable':
            html = mark_post_unavailable(html,'fixture/13')
        return httpx.Response(200,text=html.replace('fixture',username))
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
    if request.url.path in ('/feed', '/markdown-feed') or request.url.host == 'source.test':
        date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        article_path = 'markdown-article' if request.url.path == '/markdown-feed' else 'article'
        item = '' if request.url.host == 'empty.test' else f'<item><guid>article</guid><title>Статья {request.url.host}</title><link>https://{request.url.host}/{article_path}</link><pubDate>{date}</pubDate><description>Анонс библиотеки.</description></item>'
        return httpx.Response(200, text=f'<rss version="2.0"><channel><title>Синтетический источник</title>{item}</channel></rss>')
    if request.url.path == '/markdown-article':
        return httpx.Response(200, text='<article><p>[YouTube\n\nChrista Pike 911 Calls](https://youtu.be/\n  BPoce9VGatE)</p><p>[Статья](https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust)</p></article>')
    assert request.url.path == '/article'
    return httpx.Response(200, text=f'<article><p>Открылась библиотека на 24 места.</p><img src="https://{request.url.host}/image.png"></article>')


def build_app(root, token, *, persistent=False, public_url=None,collection=True,processing_delay=0):
    models=model_transport([])
    async def delayed_model(request):
        if request.url.path=='/v1/chat/completions':
            await asyncio.sleep(processing_delay)
        return await models.handle_async_request(request)
    ai=httpx.MockTransport(delayed_model) if processing_delay else models
    return create_app(root, token=token, rss_enabled=collection, persistent=persistent, public_url=public_url,
                     rss_transport=httpx.MockTransport(response), telegram_client=BrowserTelegram(), ai_transport=ai, codex_client=CodexService(),
                     youtube_history=History([f'v{n:010d}' for n in range(60, 0, -1)]), youtube_captions=NoCaptions(),
                     lease=60, grace=60)


if __name__ == '__main__':
    token = secrets.token_urlsafe(24)
    app = build_app(Path(sys.argv[1]), token)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    print(json.dumps({'origin': f'http://127.0.0.1:{sock.getsockname()[1]}', 'token': token}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
