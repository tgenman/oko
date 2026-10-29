"""Telegram full copies stay local until an explicit reload."""
import time

import pytest

from tests.support.app import create_app
from tests.support.telegram import Telegram, connect, settled, configure_fast_web
from tests.support.reader import wait_for
from tests.support.runtime_clock import ReaderClock
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


class Posts(Telegram):
    def __init__(self):
        self.fetches = 0
        self.text = 'Original'

    async def page(self,account,peer,after,since):
        return dict(messages=[dict(id=11,date=time.time()-60,text=self.text,group=None,media=None)] if after<11 else [],more=False)

    async def messages(self, account, peer, ids):
        self.fetches += 1
        return [dict(id=11, date=time.time()-60, text=self.text, group=None, media=None)]


def test_full_account_copy_is_not_polled_but_can_be_explicitly_reloaded(tmp_path):
    telegram = Posts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram), headers=HEADERS) as client:
        feed = connect(client)
        item = settled(client)[0]['id']
        assert telegram.fetches == 0
        telegram.text = 'Edited'
        assert client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed['id']}).status_code == 200
        wait_for(client, lambda: client.get('/api/v3/collection-status/'+feed['id']).json()['source']['state'] == 'complete')
        assert telegram.fetches == 0
        assert client.get('/api/v3/publication/'+item).json()['text'] == 'Original'
        assert client.post('/api/v3/materials/'+item+'/retry').status_code == 200
        settled(client)
        assert telegram.fetches == 1
        assert client.get('/api/v3/publication/'+item).json()['text'] == 'Edited'




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
        deadline = time.monotonic()+4
        while len(downloads)<2 and time.monotonic()<deadline:
            time.sleep(.02)
        assert downloads == ['Original','Edited']
        deadline = time.monotonic()+3
        while not client.get('/api/v3/status/details').json()['problems']['current'] and time.monotonic()<deadline:
            time.sleep(.02)
        assert client.get('/api/v3/status/details').json()['problems']['current']
        kept = client.get('/api/v3/publication/'+item).json()
        assert kept['text'] == 'Edited'
        assert kept['revision_id'] != old['revision_id']
        assert client.get([a for a in kept['media'] if a['role']=='attachment'][0]['url']).content == b'old-image'
        client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        wait_for(client, lambda: not any(op['kind']=='discover' for op in client.get('/api/v3/collection-status/'+feed).json()['operations']))
        assert downloads == ['Original','Edited']
