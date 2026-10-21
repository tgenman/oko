"""Telegram full copies stay local until an explicit reload."""
import time

import pytest

from tests.support.app import create_app
from tests.support.telegram import Telegram, connect, settled
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


class Posts(Telegram):
    def __init__(self):
        self.fetches = 0
        self.text = 'Original'

    async def messages(self, account, peer, ids):
        self.fetches += 1
        return [dict(id=11, date=time.time()-60, text=self.text, group=None, media=None)]


def test_full_account_copy_is_not_polled_but_can_be_explicitly_reloaded(tmp_path):
    telegram = Posts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram), headers=HEADERS) as client:
        feed = connect(client)
        item = settled(client)[0]['id']
        assert telegram.fetches == 1
        telegram.text = 'Edited'
        assert client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed['id']}).status_code == 200
        time.sleep(.3)
        assert telegram.fetches == 1
        assert client.get('/api/v3/materials/'+item).json()['content']['text'] == 'Original'
        assert client.post('/api/v3/materials/'+item+'/retry').status_code == 200
        settled(client)
        assert telegram.fetches == 2
        assert client.get('/api/v3/materials/'+item).json()['content']['text'] == 'Edited'


def test_failed_reload_keeps_full_account_copy_until_complete_replacement(tmp_path):
    from oko.collection.contracts import SourceError
    class MediaPosts(Posts):
        broken = False
        async def messages(self, account, peer, ids):
            rows = await super().messages(account, peer, ids)
            rows[0]['media'] = dict(kind='image', extension='jpg', mime='image/jpeg')
            return rows
        async def download(self, account, peer, id):
            if self.broken:
                raise SourceError('temporary media outage', 1)
            return self.text.encode()
    telegram = MediaPosts()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram), headers=HEADERS) as client:
        connect(client)
        item = settled(client)[0]['id']
        old = client.get('/api/v3/materials/'+item).json()['content']
        telegram.text, telegram.broken = 'Edited', True
        assert client.post('/api/v3/materials/'+item+'/retry').status_code == 200
        time.sleep(.3)
        kept = client.get('/api/v3/materials/'+item).json()['content']
        assert kept['text'] == 'Original'
        assert kept['revision_id'] == old['revision_id']
        assert client.get(kept['attachments'][0]['url']).content == b'Original'
        telegram.broken = False
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            fresh = client.get('/api/v3/materials/'+item).json()['content']
            if fresh['text'] == 'Edited':
                break
            time.sleep(.02)
        assert fresh['text'] == 'Edited'
        assert client.get(fresh['attachments'][0]['url']).content == b'Edited'


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
        feed = client.post('/api/v3/telegram/connect', json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
        item = settled(client)[0]['id']
        old = client.get('/api/v3/materials/'+item).json()['content']
        assert downloads == ['Original']
        state[0] = 'Edited'
        client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        time.sleep(.7)
        assert downloads == ['Original']
        client.post('/api/v3/materials/'+item+'/retry')
        deadline = time.monotonic()+4
        while len(downloads)<2 and time.monotonic()<deadline:
            time.sleep(.02)
        assert downloads == ['Original','Edited']
        deadline = time.monotonic()+3
        while not client.get('/api/v3/status').json()['problems']['current'] and time.monotonic()<deadline:
            time.sleep(.02)
        assert client.get('/api/v3/status').json()['problems']['current']
        kept = client.get('/api/v3/materials/'+item).json()['content']
        assert kept['text'] == 'Original'
        assert kept['revision_id'] == old['revision_id']
        assert client.get(kept['attachments'][0]['url']).content == b'old-image'
        client.post('/api/v3/collect', json={'scope':'feed','feed_id':feed})
        time.sleep(.7)
        assert downloads == ['Original','Edited']


def test_permanent_media_failure_wins_over_temporary_failure_in_same_reload(tmp_path):
    from oko.collection.contracts import SourceError
    class Album(Telegram):
        broken = False
        downloads = 0
        async def messages(self, account, peer, ids):
            return [dict(id=i,date=time.time()-(1 if self.broken else 60),text='Album',group=100,media=dict(kind='image',extension='jpg',mime='image/jpeg')) for i in (11,12)]
        async def page(self, account, peer, after, since):
            return dict(messages=await self.messages(account,peer,[]),more=False)
        async def download(self, account, peer, id):
            self.downloads += 1
            if self.broken:
                raise SourceError('permanently missing' if id==11 else 'temporarily missing',1,retryable=id!=11)
            return b'old'
    telegram, advance = Album(), [0.]
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram,clock=lambda:time.monotonic()+advance[0]),headers=HEADERS) as client:
        feed = connect(client)
        item = settled(client)[0]['id']
        telegram.broken = True
        client.post('/api/v3/materials/'+item+'/retry')
        deadline = time.monotonic()+3
        while telegram.downloads<4 and time.monotonic()<deadline:
            time.sleep(.02)
        assert telegram.downloads == 4
        time.sleep(.1)
        advance[0] = 2
        time.sleep(.3)
        assert telegram.downloads == 4
        assert client.get('/api/v3/materials/'+item).json()['content']['completeness']['state'] == 'complete'
        assert any('permanently missing' in p['message'] for p in client.get('/api/v3/status').json()['problems']['current'])


@pytest.mark.parametrize('article', [False, True])
def test_web_reload_stops_on_permanent_error_even_with_transient_media_error(tmp_path, article):
    import httpx
    from tests.support.telegram_web import page
    broken, advance, requests = [False], [0.], []
    def photo(name):
        return '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+name+'.jpg\')"></a>'
    def response(request):
        if request.url.host == 'api.telegra.ph':
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'img','attrs':{'src':'https://cdn.example/flaky.jpg'}}]}})
        if request.url.host == 'cdn.example':
            requests.append(request.url.path)
            return httpx.Response((404 if request.url.path=='/gone.jpg' else 503) if broken[0] else 200,content=b'old')
        return httpx.Response(200,text=page([(11,('Edited' if broken[0] else 'Original')+photo('gone')+(' https://telegra.ph/Article' if article else photo('flaky')))]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response),clock=lambda:time.monotonic()+advance[0]),headers=HEADERS) as client:
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client,timeout=8)[0]['id']
        broken[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        deadline=time.monotonic()+5
        while len(requests)<4 and time.monotonic()<deadline:
            time.sleep(.02)
        assert len(requests)==4
        time.sleep(.1)
        advance[0]=65
        time.sleep(1)
        assert len(requests)==4
        assert 'Original' in client.get('/api/v3/materials/'+item).json()['content']['text']
        assert client.get('/api/v3/status').json()['problems']['current']
