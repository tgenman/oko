"""Telegram full copies stay local until an explicit reload."""
import time

import pytest

from tests.support.app import create_app
from tests.support.telegram import settled, configure_fast_web
from tests.support.reader import wait_for
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


def test_web_full_copy_is_not_polled_and_terminal_reload_keeps_old_media(tmp_path):
    import httpx
    from tests.support.telegram_web import page
    state = ['Original']
    html_fetches, downloads = [], []
    def response(request):
        if request.url.host == 'cdn.example':
            downloads.append(state[0])
            return httpx.Response(200, content=b'old-image') if state[0]=='Original' else httpx.Response(404)
        if 'before' in request.url.params:
            html_fetches.append(state[0])
        photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image.jpg\')"></a>'
        return httpx.Response(200, text=page([(11,state[0]+photo)]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        configure_fast_web(client)
        feed = client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
        item = settled(client)[0]['id']
        old = client.get('/api/v3/publication/'+item).json()
        assert downloads == ['Original']
        state[0] = 'Edited'
        client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        wait_for(client, lambda: client.get('/api/v3/collection-status/'+feed).json()['source']['state'] == 'complete')
        assert downloads == ['Original']
        client.post('/api/v3/materials/'+item+'/retry')
        kept=client.get('/api/v3/publication/'+item).json()
        assert kept['text']=='Original' and kept['revision_id']==old['revision_id']
        assert client.get([a for a in kept['media'] if a['url'] is not None][0]['url']).content==b'old-image'
        assert downloads==['Original']
