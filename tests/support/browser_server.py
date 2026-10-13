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
from tests.support.events import event_transport
from tests.support.codex import CodexService
from tests.support.youtube import PNG, History, NoCaptions


def response(request):
    if request.url.host == 'i.ytimg.com':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.host == 'www.youtube.com':
        assert request.url.path == '/feeds/videos.xml'
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Synthetic channel</title></feed>')
    assert request.url.host in ('a.test', 'b.test', 'empty.test'), request.url
    if request.url.path == '/image.png':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.path == '/feed':
        date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        item = '' if request.url.host == 'empty.test' else f'<item><guid>article</guid><title>Статья {request.url.host}</title><link>https://{request.url.host}/article</link><pubDate>{date}</pubDate><description>Анонс библиотеки.</description></item>'
        return httpx.Response(200, text=f'<rss version="2.0"><channel><title>Синтетический источник</title>{item}</channel></rss>')
    assert request.url.path == '/article'
    return httpx.Response(200, text=f'<article><p>Открылась библиотека на 24 места.</p><img src="https://{request.url.host}/image.png"></article>')


def build_app(root, token):
    return create_app(root, token=token, rss_enabled=True,
                     rss_transport=httpx.MockTransport(response), ai_transport=event_transport([]), codex_client=CodexService(),
                     youtube_history=History([f'v{n:010d}' for n in range(60, 0, -1)]), youtube_captions=NoCaptions(),
                     lease=60, grace=60)


if __name__ == '__main__':
    token = secrets.token_urlsafe(24)
    app = build_app(Path(sys.argv[1]), token)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    print(json.dumps({'origin': f'http://127.0.0.1:{sock.getsockname()[1]}', 'token': token}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
