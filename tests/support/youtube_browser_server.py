"""Temporary real HTTP reader with a synthetic blocked caption source."""
import json
from pathlib import Path
import secrets
import socket
import sys

import httpx
import uvicorn

from oko.collection.youtube.access import CaptionsBlocked
from tests.support.app import create_app
from tests.support.youtube import PNG, feed_xml


class BlockedCaptions:
    async def fetch(self, video):
        raise CaptionsBlocked()


def response(request):
    if request.url.host == 'i.ytimg.com':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    assert request.url.host == 'www.youtube.com' and request.url.path == '/feeds/videos.xml'
    return httpx.Response(200, text=feed_xml())


if __name__ == '__main__':
    token = secrets.token_urlsafe(24)
    app = create_app(Path(sys.argv[1]), token=token, rss_enabled=True,
                     rss_transport=httpx.MockTransport(response), youtube_captions=BlockedCaptions(), lease=60, grace=60)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    print(json.dumps({'origin': f'http://127.0.0.1:{sock.getsockname()[1]}', 'token': token}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
