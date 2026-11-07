"""Telegraph enrichment through public Telegram web collection."""
from tests.support.cards import read_all_cards
from tests.support.telegram import configure_fast_web
import httpx

from tests.support.app import create_app
from tests.support.telegram import settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

POST = 'Original post https://telegra.ph/One https://www.telegra.ph/One https://telegra.ph/Two'


def test_direct_articles_are_complete_local_and_unique_without_recursive_crawling(tmp_path):
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
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture',folder='Inbox')
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


def test_article_completion_and_retry_keep_previous_copy_without_false_edit(tmp_path):
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
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        body = dict(source_locator='@fixture',folder='Inbox')
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


def test_late_article_after_feed_deletion_finishes_and_is_restored(tmp_path):
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
            assert not cancelled.is_set()
            release.set()
            deadline = time.monotonic()+10
            while time.monotonic()<deadline and not assets:
                time.sleep(.02)
            assert not read_all_cards(client, '/api/v3/cards')
            assert assets
            restored = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
            assert restored.status_code == 200 and restored.json()['feed_id'] == result.json()['feed_id']
            card = settled(client)[0]
            material = client.get('/api/v3/publication/'+card['id']).json()
            media = next(media['url'] for media in material['media'] if media['role'] == 'attachment')
            assert client.get(media).content == b'late'
        finally:
            release.set()
    assert not list((tmp_path/'staging').rglob('incoming-*'))
