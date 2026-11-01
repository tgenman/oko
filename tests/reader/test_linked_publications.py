"""Linked articles remain independent publications at the reader HTTP boundary."""
from oko.storage.publication_owners import owner_expr
from tests.support.cards import read_all_cards, read_publications_for_cards
import httpx

from tests.support.publication_pipeline import publication_root
from tests.support.app import create_app
from tests.support.rss import import_feed, wait_cards
from tests.support.reader import presence
from tests.support.v3 import TestClient

HEADERS={'Authorization':'Bearer test-token'}


def test_rss_article_is_fetched_separately_and_does_not_become_a_feed_card(tmp_path):
    import asyncio
    import threading
    from tests.support.storage import database
    import json
    from tests.support.reader import wait_for
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    media_started,media_release=threading.Event(),threading.Event()
    async def response(request):
        if request.url.host=='cdn.example':
            media_started.set()
            while not media_release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,content=b'article-image',headers={'content-type':'image/png'})
        if request.url.host=='web.example':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,text='<title>Article</title><article><p>Article body</p><a href="https://web.example/another">Do not follow</a><img src="https://cdn.example/article.png"></article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><title>Announcement</title><link>https://web.example/article</link><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            import_feed(client)
            presence(client,'rss')
            card=wait_cards(client,idle=False)[0]
            initial=client.get('/api/v3/publication/'+card['id']).json()
            assert initial['text']=='Own excerpt'
            assert started.wait(3)
            release.set()
            value=wait_for(client,lambda:(value if 'Article body' in (value:=client.get('/api/v3/publication/'+card['id']).json())['text'] else None))
            assert media_started.wait(3)
            assert client.get('/api/v3/status').status_code==200
            assert client.get('/api/v3/publication/'+card['id']).status_code==200
            assert 'Own excerpt' in value['text']
            assert value['revision_id']!=initial['revision_id']
            assert len(read_all_cards(client, '/api/v3/cards'))==1
            with database(root) as db:
                linked=db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s',(card['id'],)).fetchone()[0]
                row=db.execute(f"SELECT {owner_expr('i')} AS feed_id,i.platform,i.status,i.content FROM publications i WHERE i.id = %s",(linked,)).fetchone()
                assert row[:3]==(None,'other_web','ready')
                assert 'Article body' in json.loads(row[3])['blocks'][0]['text']
                assert not db.execute('SELECT 1 FROM feed_publications WHERE publication_id = %s',(linked,)).fetchone()
                assert db.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==2
            media_release.set()
            complete=wait_for(client,lambda:(m if (m := client.get('/api/v3/publication/'+card['id']).json()) and any(media['role'] == 'attachment' for media in m['media']) else None))
            url=[media for media in complete['media'] if media['role'] == 'attachment'][0]['url']
            assert url.startswith('/api/v3/materials/'+linked+'/media/')
            assert client.get(url).content==b'article-image'
        finally:
            release.set()
            media_release.set()


def test_article_arrival_reprocesses_root_and_keeps_actual_source_references(tmp_path):
    import asyncio
    import threading
    import json
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    root=publication_root(tmp_path)
    release=threading.Event()
    calls=[]
    native=model_transport([])
    def model(request):
        if request.url.path!='/v1/chat/completions':
            return native.handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        result={'points':[{'text':source['text'],'sources':[{'id':source['id'],'url':source['url']}]} for source in task['sources'] if source['text']]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    async def response(request):
        if request.url.host=='web.example':
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,text='<article>Article body</article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><title>Announcement</title><link>https://web.example/article</link><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True})
            import_feed(client)
            presence(client,'rss')
            card=wait_for(client,lambda:next((c for c in read_publications_for_cards(client, '/api/v3/cards') if c['theses']['result']),None))
            assert len(calls)==1 and calls[0]['sources'][0]['text']=='Own excerpt'
            release.set()
            updated=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+card['id']).json())['theses']['result'] and len(value['theses']['result']['points'])==2 else None))
            assert updated['theses']['result']['points']==['Own excerpt','Article body']
            assert len(calls[-1]['sources'])==2
            article_source=calls[-1]['sources'][1]
            assert article_source['id']!=card['id'] and article_source['url']=='https://web.example/article'
            assert updated['theses']['result']['references'][1]==[{'material_id':article_source['id'],'source_url':article_source['url']}]
        finally:
            release.set()


def test_shared_article_survives_one_root_then_disappears_after_the_last_link(tmp_path):
    from tests.support.storage import database
    from tests.support.reader import wait_for
    from tests.support.subscriptions import add_source
    root=publication_root(tmp_path)
    def response(request):
        if request.url.host=='web.example':
            return httpx.Response(200,text='<article>Shared article</article>')
        return httpx.Response(200,text='<rss><channel><item><guid>same-guid</guid><link>https://web.example/shared</link><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        for name in ('first','second'):
            add_source(client,'https://source.test/'+name,name,'News')
        presence(client,'rss')
        cards=wait_cards(client,2)
        wait_for(client,lambda:all('Shared article' in client.get('/api/v3/publication/'+card['id']).json()['text'] for card in cards))
        with database(root) as db:
            articles=db.execute("SELECT id FROM publications WHERE platform='other_web'").fetchall()
            assert len(articles)==1
            article=articles[0][0]
        client.post('/api/v3/materials/'+cards[0]['id']+'/viewed')
        assert 'Shared article' in client.get('/api/v3/publication/'+cards[1]['id']).json()['text']
        client.post('/api/v3/materials/'+cards[1]['id']+'/viewed')
        with database(root) as db:
            row=db.execute('SELECT viewed , content FROM publications WHERE id = %s',(article,)).fetchone()
            assert row is None or row==(1,'{}')
            assert not db.execute('SELECT 1 FROM publication_links WHERE target_publication_id = %s',(article,)).fetchone()


def test_unavailable_article_keeps_excerpt_and_root_retry_fetches_it_again(tmp_path):
    from tests.support.reader import wait_for
    root=publication_root(tmp_path)
    missing=[True]
    def response(request):
        if request.url.host=='web.example':
            return httpx.Response(404) if missing[0] else httpx.Response(200,text='<article>Retrieved article</article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><link>https://web.example/article</link><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        import_feed(client)
        presence(client,'rss')
        card=wait_cards(client)[0]
        value=wait_for(client,lambda:(value if 'недоступна' in (value:=client.get('/api/v3/publication/'+card['id']).json())['completeness'].get('message','').lower() else None))
        assert value['text']=='Own excerpt'
        status=client.get('/api/v3/status/details')
        assert status.status_code==200
        assert any(resource=={'kind':'material','material_id':card['id']} for problem in status.json()['problems']['current'] for resource in problem['affected'])
        missing[0]=False
        client.post('/api/v3/materials/'+card['id']+'/retry')
        wait_for(client,lambda:'Retrieved article' in client.get('/api/v3/publication/'+card['id']).json()['text'])


def test_late_article_response_is_discarded_after_its_last_root_is_read(tmp_path):
    import asyncio
    import threading
    from tests.support.storage import database
    from tests.support.reader import wait_for
    root=publication_root(tmp_path)
    started,release,returned=threading.Event(),threading.Event(),threading.Event()
    async def response(request):
        if request.url.host=='web.example':
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
            returned.set()
            return httpx.Response(200,text='<article>Late article must not survive</article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><link>https://web.example/article</link><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            import_feed(client)
            presence(client,'rss')
            card=wait_cards(client,idle=False)[0]
            assert started.wait(3)
            client.post('/api/v3/materials/'+card['id']+'/viewed')
            release.set()
            assert returned.wait(3)
            wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
            assert read_all_cards(client, '/api/v3/cards')==[]
            with database(root) as db:
                assert not db.execute('SELECT 1 FROM publication_links').fetchone()
                assert not db.execute("SELECT 1 FROM publications WHERE content LIKE '%Late article must not survive%'").fetchone()
                assert not db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND status IN ('pending','running')").fetchone()
        finally:
            release.set()
