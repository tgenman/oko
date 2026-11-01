"""One Telegraph enrichment path shared by web and verified client collection."""
from tests.support.cards import read_all_cards
from tests.support.telegram import configure_fast_web
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import Telegram, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

POST = 'Original post https://telegra.ph/One https://www.telegra.ph/One https://telegra.ph/Two'


class TelegraphClient(Telegram):
    post_text = POST
    async def page(self, account, peer, after, since):
        rows = (await super().page(account,peer,after,since))['messages']
        rows[0]['text'] = self.post_text
        return dict(messages=rows,more=False)

    async def messages(self, account, peer, ids):
        return (await self.page(account,peer,0,0))['messages']

    async def history_page(self, account, peer, upper, before, since):
        return dict(messages=(await self.page(account,peer,0,0))['messages'],upper=11,next_before=11,more=False)


@pytest.mark.parametrize('access',['web','client'])
def test_direct_articles_are_complete_local_and_unique_without_recursive_crawling(tmp_path,access):
    calls = []
    async def response(request):
        calls.append(str(request.url))
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if request.url.host == 'api.telegra.ph':
            assert request.url.params['return_content'] == 'true'
            name = request.url.path.split('/')[-1]
            content = [{'tag':'p','children':['Article '+name+' ', {'tag':'a','attrs':{'href':'https://example.org/no-follow'},'children':['ordinary link']}]}]
            if name == 'One':
                content.append({'tag':'figure','children':[{'tag':'img','attrs':{'src':'/file/photo.jpg'}},{'tag':'figcaption','children':['Photo caption']}]})
            return httpx.Response(200,json={'ok':True,'result':{'title':'Title '+name,'content':content}})
        if request.url.host == 'telegra.ph':
            return httpx.Response(200,content=b'article-photo',headers={'content-type':'image/jpeg'})
        assert request.url.host == 't.me'
        return httpx.Response(200,text=page([(11,POST)]))
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    if access == 'client':
        options['telegram_client'] = TelegraphClient()
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture',folder='Inbox')
        if access == 'client':
            client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
            body['access'] = {'kind':'client','profile':'personal'}
        assert client.post('/api/v3/telegram/connect',json=body).status_code == 200
        from tests.support.reader import wait_for
        identity=settled(client)[0]['id']
        material=wait_for(client,lambda: value if (value:=client.get('/api/v3/publication/'+identity).json())['completeness']['state']=='complete' else None)
        assert material['text'].startswith(POST)
        linked = material['linked_content']
        assert linked['post_text'] == POST
        assert [a['title'] for a in linked['articles']] == ['Title One', 'Title Two']
        assert linked['articles'][0]['media_urls'] == [[media for media in material['media'] if media['role'] == 'attachment'][0]['url']]
        assert material['text'].count('Article One') == 1
        assert material['text'].count('Article Two') == 1
        assert 'Photo caption' in material['text']
        assert (material['completeness']['state']=='complete') is True
        assert len([media for media in material['media'] if media['role'] == 'attachment']) == 1
        assert client.get([media for media in material['media'] if media['role'] == 'attachment'][0]['url']).content == b'article-photo'
        assert not any('example.org' in url for url in calls)
        assert sum('/getPage/One?' in url for url in calls) == 2  # Metadata, then media locator validation.
        assert sum('/getPage/Two?' in url for url in calls) == 1


@pytest.mark.parametrize('access',['web','client'])
def test_article_completion_and_retry_keep_previous_copy_without_false_edit(tmp_path,access):
    import time
    post = 'Original https://telegra.ph/One'
    phase = [0]
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            if phase[0]==0:
                return httpx.Response(200,json={'ok':False,'error':'PAGE_NOT_FOUND'})
            if phase[0]==2:
                return httpx.Response(503)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[
                {'tag':'p','children':['Version two' if phase[0]==3 else 'Version one']},
                {'tag':'img','attrs':{'src':'https://cdn.example/image?signature='+str(phase[0])}}]}})
        if request.url.host == 'cdn.example':
            return httpx.Response(200,content=b'known-article-image')
        return httpx.Response(200,text=page([(11,post)]))
    telegram = TelegraphClient()
    telegram.post_text = post
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    if access=='client':
        options['telegram_client'] = telegram
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture',folder='Inbox')
        if access=='client':
            client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
            body['access'] = {'kind':'client','profile':'personal'}
        client.post('/api/v3/telegram/connect',json=body)
        deadline = time.monotonic()+2
        pending = None
        while time.monotonic()<deadline:
            cards = read_all_cards(client, '/api/v3/cards')
            if cards:
                pending = client.get('/api/v3/publication/'+cards[0]['id']).json()
                if 'Статья Telegraph недоступна' in (pending.get('completeness') or {}).get('message',''):
                    break
            time.sleep(.02)
        assert pending and 'Статья Telegraph недоступна' in pending['completeness'].get('message','')
        identity = pending['id']
        phase[0] = 1
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        from tests.support.reader import wait_for
        loaded=wait_for(client,lambda: value if (value:=client.get('/api/v3/publication/'+identity).json())['completeness']['state']=='complete' else None)
        assert (loaded['completeness']['state']=='complete') is True and 'previous_versions' not in loaded
        assert 'Version one' in loaded['text']
        assert loaded['revision_id'] != pending['revision_id']
        image = [media for media in loaded['media'] if media['role'] == 'attachment'][0]['url']
        phase[0] = 2
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        retained = client.get('/api/v3/publication/'+identity).json()
        assert retained['text'] == loaded['text'] and (retained['completeness']['state']=='complete') is True
        assert 'previous_versions' not in retained and retained['revision_id']==loaded['revision_id']
        assert client.get(image).content == b'known-article-image'
        phase[0] = 3
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        changed = client.get('/api/v3/publication/'+identity).json()
        assert 'Version two' in changed['text'] and 'previous_versions' not in changed


def test_late_article_after_feed_deletion_cannot_download_media_or_publish(tmp_path):
    import asyncio
    import threading
    import time
    started,cancelled,release = threading.Event(),threading.Event(),threading.Event()
    assets = []
    async def response(request):
        if request.url.host == 'api.telegra.ph':
            started.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                cancelled.set()
                while not release.is_set():
                    await asyncio.sleep(.01)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Late','content':[
                {'tag':'img','attrs':{'src':'https://cdn.example/late.jpg'}}]}})
        if request.url.host == 'cdn.example':
            assets.append(str(request.url))
            return httpx.Response(200,content=b'late')
        return httpx.Response(200,text=page([(11,'Original https://telegra.ph/Late')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),
                    headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        assert started.wait(2)
        try:
            assert client.delete('/api/v3/feeds/'+result.json()['feed_id']).status_code == 200
            assert cancelled.is_set()
            release.set()
            deadline = time.monotonic()+2
            while time.monotonic()<deadline and (client.get('/api/v3/subscriptions').json()['feeds'] or list((tmp_path/'staging').rglob('incoming-*'))):
                time.sleep(.02)
            time.sleep(.05)
            assert not read_all_cards(client, '/api/v3/cards')
            assert not assets
        finally:
            release.set()
    assert not list((tmp_path/'staging').rglob('incoming-*'))
