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
from tests.support.youtube import PNG


def response(request):
    assert request.url.host in ('a.test', 'b.test', 'empty.test'), request.url
    if request.url.path == '/image.png':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.path == '/feed':
        date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        item = '' if request.url.host == 'empty.test' else f'<item><guid>article</guid><title>Статья {request.url.host}</title><link>https://{request.url.host}/article</link><pubDate>{date}</pubDate><description>Анонс библиотеки.</description></item>'
        return httpx.Response(200, text=f'<rss version="2.0"><channel><title>Синтетический источник</title>{item}</channel></rss>')
    assert request.url.path == '/article'
    return httpx.Response(200, text=f'<article><p>Открылась библиотека на 24 места.</p><img src="https://{request.url.host}/image.png"></article>')


if __name__ == '__main__':
    token = secrets.token_urlsafe(24)
    app = create_app(Path(sys.argv[1]), token=token, rss_enabled=True,
                     rss_transport=httpx.MockTransport(response), ai_transport=event_transport([]), codex_client=CodexService(),
                     lease=60, grace=60)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    print(json.dumps({'origin': f'http://127.0.0.1:{sock.getsockname()[1]}', 'token': token}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
