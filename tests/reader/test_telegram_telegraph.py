"""Telegraph enrichment through public Telegram web collection."""
from tests.support.cards import read_all_cards
from tests.support.publications import body_text,reasons
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
        identity=settled(client,timeout=15)[0]['id']
        def collected():
            value=client.get('/api/v3/publication/'+identity).json()
            return value if value['linked_publications'] and all(article['content'].get('blocks') is not None for article in value['linked_publications']) and all(media['status']=='ready' for media in value['media']) else None
        material=wait_for(client,collected)
        assert body_text(material).startswith(POST)
        articles = material['linked_publications']
        assert [a['title'] for a in articles] == ['Title One', 'Title Two']
        article_media={b['media_id'] for b in articles[0]['content']['blocks'] if b['type']=='media'}
        assert any(media['id'] in article_media and media['url'] is not None for media in material['media'])
        assert body_text(material).count('Article One') == 1
        assert body_text(material).count('Article Two') == 1
        assert 'Photo caption' in body_text(material)
        assert all(media['status']=='ready' for media in material['media'])
        assert len([media for media in material['media'] if media['url'] is not None]) == 1
        assert client.get([media for media in material['media'] if media['url'] is not None][0]['url']).content == b'article-photo'
        assert not any('example.org' in url for url in calls)
        assert sum('/getPage/One?' in url for url in calls) == 1  # Saved source URL is sufficient for media download.
        assert sum('/getPage/Two?' in url for url in calls) == 1
        from tests.support.storage import database
        with database(tmp_path) as db:
            db.execute("UPDATE publications SET status='pending_collection' WHERE platform='telegraph' AND title='Title One'")
        partial=client.get('/api/v3/publication/'+identity).json()
        assert 'Article One' in body_text(partial)
        assert any(media['id'] in article_media and media['url'] is not None for media in partial['media'])
        with database(tmp_path) as db:
            db.execute("UPDATE jobs SET status='unavailable',reason_code='article-unavailable',reason_message='Article access restricted' WHERE kind='fetch' AND item=(SELECT id FROM publications WHERE platform='telegraph' AND title='Title One')")
        partial=client.get('/api/v3/publication/'+identity).json()
        assert 'Article access restricted' in reasons(partial)
        assert any(job['object_id']==articles[0]['id'] and job['reason_message']=='Article access restricted' for job in partial['jobs'])


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
        deadline = time.monotonic()+15
        pending = None
        while time.monotonic()<deadline:
            cards = read_all_cards(client, '/api/v3/cards')
            if cards:
                pending = client.get('/api/v3/publication/'+cards[0]['id']).json()
                if any('Статья Telegraph недоступна' in reason for reason in reasons(pending)):
                    break
            time.sleep(.02)
        assert pending and any('Статья Telegraph недоступна' in reason for reason in reasons(pending))
        identity = pending['id']
        phase[0] = 1
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        from tests.support.reader import wait_for
        loaded=wait_for(client,lambda: value if 'Version one' in body_text(value:=client.get('/api/v3/publication/'+identity).json()) and all(media['status']=='ready' for media in value['media']) else None)
        assert all(media['status']=='ready' for media in loaded['media']) and 'previous_versions' not in loaded
        assert 'Version one' in body_text(loaded)
        assert loaded['linked_publications'] != pending['linked_publications']
        image = [media for media in loaded['media'] if media['url'] is not None][0]['url']
        phase[0] = 2
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        retained = client.get('/api/v3/publication/'+identity).json()
        assert body_text(retained) == body_text(loaded) and all(media['status']=='ready' for media in retained['media'])
        assert 'previous_versions' not in retained and retained['content']==loaded['content'] and retained['linked_publications']==loaded['linked_publications']
        assert client.get(image).content == b'known-article-image'
        phase[0] = 3
        client.post('/api/v3/materials/'+identity+'/retry')
        settled(client)
        changed = client.get('/api/v3/publication/'+identity).json()
        assert 'Version one' in body_text(changed) and 'previous_versions' not in changed


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
        assert started.wait(10)
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
