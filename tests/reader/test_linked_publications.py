"""Linked articles remain independent publications at the reader HTTP boundary."""
import httpx

from tests.reader.test_independent_media import migrated_root
from tests.support.app import create_app
from tests.support.rss import import_feed, wait_cards
from tests.support.reader import presence
from tests.support.v3 import TestClient

HEADERS={'Authorization':'Bearer test-token'}


def test_rss_ready_excerpt_without_article_url_is_saved_without_a_fetch(tmp_path):
    root=migrated_root(tmp_path)
    def response(request):
        return httpx.Response(200,text='<rss><channel><title>Feed</title><item><guid>entry-1</guid><title>News</title><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        import_feed(client)
        presence(client,'rss')
        card=wait_cards(client)[0]
        material=client.get('/api/v3/materials/'+card['id']).json()
        assert material['content']['text']=='Own excerpt'
        assert material['content']['previous_versions']==[]
        import sqlite3
        with sqlite3.connect(root/'state.sqlite') as db:
            assert db.execute('SELECT platform FROM items WHERE id=?',(card['id'],)).fetchone()==('rss',)
            assert not db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=?",(card['id'],)).fetchone()


def test_two_rss_records_with_one_url_keep_distinct_guid_identities(tmp_path):
    root=migrated_root(tmp_path)
    def response(request):
        return httpx.Response(200,text='<rss><channel><item><guid>two</guid><link>https://web.example/shared</link><description>Two</description></item><item><guid>one</guid><link>https://web.example/shared</link><description>One</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        from tests.support.subscriptions import add_source
        from tests.support.reader import wait_for
        add_source(client,'https://source.test/feed','RSS','News')
        presence(client,'rss')
        # Explicit replay opens the whole currently available RSS window.
        wait_cards(client)
        client.post('/api/v3/collect')
        cards=wait_for(client,lambda:(cards if len(cards:=client.get('/api/v3/cards').json())==2 else None))
        assert len({card['id'] for card in cards})==2


def test_rss_article_is_fetched_separately_and_does_not_become_a_feed_card(tmp_path):
    import asyncio
    import threading
    import sqlite3
    import json
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    async def response(request):
        if request.url.host=='web.example':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,text='<title>Article</title><article><p>Article body</p><a href="https://web.example/another">Do not follow</a></article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><title>Announcement</title><link>https://web.example/article</link><description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            import_feed(client)
            presence(client,'rss')
            card=wait_cards(client)[0]
            assert client.get('/api/v3/materials/'+card['id']).json()['content']['text']=='Own excerpt'
            assert started.wait(3)
            release.set()
            value=wait_for(client,lambda:(value if 'Article body' in (value:=client.get('/api/v3/materials/'+card['id']).json())['content']['text'] else None))
            assert 'Own excerpt' in value['content']['text']
            assert value['content']['revision_id']!=card['revision_id']
            assert len(client.get('/api/v3/cards').json())==1
            with sqlite3.connect(root/'state.sqlite') as db:
                linked=db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=?',(card['id'],)).fetchone()[0]
                row=db.execute('SELECT feed_id,platform,status,content FROM items WHERE id=?',(linked,)).fetchone()
                assert row[:3]==(None,'other_web','ready')
                assert 'Article body' in json.loads(row[3])['blocks'][0]['text']
                assert not db.execute('SELECT 1 FROM feed_publications WHERE publication_id=?',(linked,)).fetchone()
                assert db.execute('SELECT COUNT(*) FROM items').fetchone()[0]==2
        finally:
            release.set()


def test_article_arrival_reprocesses_root_and_keeps_actual_source_references(tmp_path):
    import asyncio
    import threading
    import json
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
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
            card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
            assert len(calls)==1 and calls[0]['sources'][0]['text']=='Own excerpt'
            release.set()
            updated=wait_for(client,lambda:(value if (value:=client.get('/api/v3/materials/'+card['id']).json())['theses']['result'] and len(value['theses']['result']['points'])==2 else None))
            assert [p['text'] for p in updated['theses']['result']['points']]==['Own excerpt','Article body']
            assert len(calls[-1]['sources'])==2
            article_source=calls[-1]['sources'][1]
            assert article_source['id']!=card['id'] and article_source['url']=='https://web.example/article'
            assert updated['theses']['result']['points'][1]['references']==[{'material_id':article_source['id'],'source_url':article_source['url']}]
        finally:
            release.set()


def test_filter_uses_article_with_context_but_only_changes_roots_derived_content(tmp_path):
    import json
    import sqlite3
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
    calls=[]
    native=model_transport([])
    def model(request):
        if request.url.path!='/v1/chat/completions':
            return native.handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        if task['operation']=='filter':
            present=any('Реклама.' in source['text'] for source in task['sources'])
            result=dict(hidden=False,reason='Реклама' if present else '',removed=['Реклама.'] if present else [])
        else:
            result={'points':[{'text':source['text'],'sources':[{'id':source['id'],'url':source['url']}]} for source in task['sources'] if source['text'].strip()]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    def response(request):
        if request.url.host=='web.example':
            return httpx.Response(200,text='<article>Article body Реклама.</article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><title>Announcement</title><link>https://web.example/article</link><description>Own context</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'Убери рекламу.'})
        import_feed(client)
        presence(client,'rss')
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result'] and len(c['theses']['result']['points'])==2),None))
        filters=[task for task in calls if task['operation']=='filter' and len(task['sources'])==2]
        assert filters and filters[-1]['sources'][0]['text']=='Own context'
        article=filters[-1]['sources'][1]['id']
        assert 'Реклама.' in filters[-1]['sources'][1]['text']
        assert all('Реклама.' not in source['text'] for task in calls if task['operation']=='theses' for source in task['sources'])
        with sqlite3.connect(root/'state.sqlite') as db:
            original=json.loads(db.execute('SELECT content FROM items WHERE id=?',(article,)).fetchone()[0])
            filtered=json.loads(db.execute('SELECT filtered_content FROM items WHERE id=?',(card['id'],)).fetchone()[0])
            assert original['blocks'][0]['text']=='Article body Реклама.'
            assert filtered['removed']==[{'publication_id':article,'block_index':0,'text':'Реклама.'}]
            assert db.execute('SELECT filtered_content,summarized_content FROM items WHERE id=?',(article,)).fetchone()==('{}','{}')


def test_shared_article_survives_one_root_then_disappears_after_the_last_link(tmp_path):
    import sqlite3
    from tests.support.reader import wait_for
    from tests.support.subscriptions import add_source
    root=migrated_root(tmp_path)
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
        wait_for(client,lambda:all('Shared article' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'] for card in cards))
        with sqlite3.connect(root/'state.sqlite') as db:
            articles=db.execute("SELECT id FROM items WHERE platform='other_web'").fetchall()
            assert len(articles)==1
            article=articles[0][0]
        client.post('/api/v3/materials/'+cards[0]['id']+'/viewed')
        assert 'Shared article' in client.get('/api/v3/materials/'+cards[1]['id']).json()['content']['text']
        client.post('/api/v3/materials/'+cards[1]['id']+'/viewed')
        with sqlite3.connect(root/'state.sqlite') as db:
            row=db.execute('SELECT viewed,content FROM items WHERE id=?',(article,)).fetchone()
            assert row is None or row==(1,'{}')
            assert not db.execute('SELECT 1 FROM publication_links WHERE target_publication_id=?',(article,)).fetchone()


def test_rss_replayed_record_replaces_a_removed_link_and_its_context(tmp_path):
    from tests.support.reader import wait_for
    import sqlite3
    root=migrated_root(tmp_path)
    linked=[True]
    def response(request):
        if request.url.host=='web.example':
            return httpx.Response(200,text='<article>Old article</article>')
        link='<link>https://web.example/article</link>' if linked[0] else ''
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid>'+link+'<description>Own excerpt</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        import_feed(client)
        presence(client,'rss')
        card=wait_cards(client)[0]
        wait_for(client,lambda:'Old article' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])
        linked[0]=False
        client.post('/api/v3/collect')
        wait_for(client,lambda:client.get('/api/v3/materials/'+card['id']).json()['content']['text']=='Own excerpt')
        with sqlite3.connect(root/'state.sqlite') as db:
            assert not db.execute('SELECT 1 FROM publication_links WHERE source_publication_id=?',(card['id'],)).fetchone()


def test_unavailable_article_keeps_excerpt_and_root_retry_fetches_it_again(tmp_path):
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
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
        value=wait_for(client,lambda:(value if 'недоступна' in (value:=client.get('/api/v3/materials/'+card['id']).json())['content']['completeness'].get('message','').lower() else None))
        assert value['content']['text']=='Own excerpt'
        missing[0]=False
        client.post('/api/v3/materials/'+card['id']+'/retry')
        wait_for(client,lambda:'Retrieved article' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])


def test_empty_rss_excerpt_waits_for_article_then_processes_its_actual_input(tmp_path):
    import asyncio
    import threading
    import json
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
    release=threading.Event()
    calls=[]
    native=model_transport([])
    def model(request):
        if request.url.path!='/v1/chat/completions':
            return native.handler(request)
        task=json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        result={'points':[{'text':source['text'],'sources':[{'id':source['id'],'url':source['url']}]} for source in task['sources'] if source['text'].strip()]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    async def response(request):
        if request.url.host=='web.example':
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,text='<article>Actual article text</article>')
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><link>https://web.example/article</link></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
            rss_transport=httpx.MockTransport(response),ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
            client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True})
            import_feed(client)
            presence(client,'rss')
            card=wait_cards(client)[0]
            assert not calls and card['theses']['result'] is None
            release.set()
            updated=wait_for(client,lambda:(value if (value:=client.get('/api/v3/materials/'+card['id']).json())['theses']['result'] and value['theses']['result']['points'] else None))
            assert updated['theses']['result']['points'][0]['text']=='Actual article text'
            assert len(calls)==1
        finally:
            release.set()


def test_explicit_rss_replay_can_collect_a_previously_read_record_again(tmp_path):
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
    def response(request):
        return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><description>Again</description></item></channel></rss>')
    with TestClient(create_app(root,token='test-token',api_only=True,rss_enabled=True,
                              rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        import_feed(client)
        presence(client,'rss')
        card=wait_cards(client)[0]
        client.post('/api/v3/materials/'+card['id']+'/viewed')
        wait_for(client,lambda:client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
        assert client.get('/api/v3/cards').json()==[]
        client.post('/api/v3/collect')
        replayed=wait_cards(client)[0]
        assert replayed['id']!=card['id']
        assert client.get('/api/v3/materials/'+replayed['id']).json()['content']['text']=='Again'


def test_restricted_telegram_retains_own_source_without_copying_article_into_it(tmp_path):
    import json
    import sqlite3
    from tests.support.telegram import configure_fast_web
    from tests.support.telegram_web import page,mark_post_unavailable
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
    restricted=[False]
    def response(request):
        if request.url.host=='api.telegra.ph':
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Separate article text']}]}})
        html=page([(11,'' if restricted[0] else 'Own post <a href="https://telegra.ph/Fixture">Article</a>')])
        return httpx.Response(200,text=mark_post_unavailable(html,'fixture/11') if restricted[0] else html)
    with TestClient(create_app(root,token='test-token',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card=wait_cards(client)[0]
        wait_for(client,lambda:'Separate article text' in client.get('/api/v3/materials/'+card['id']).json()['content']['text'])
        restricted[0]=True
        client.post('/api/v3/materials/'+card['id']+'/retry')
        wait_for(client,lambda:client.get('/api/v3/materials/'+card['id']).json()['content']['post_web_unavailable'])
        with sqlite3.connect(root/'state.sqlite') as db:
            own=json.loads(db.execute('SELECT content FROM items WHERE id=?',(card['id'],)).fetchone()[0])
            assert all('Separate article text' not in block.get('text','') for block in own['blocks'])
        assert 'Separate article text' in client.get('/api/v3/materials/'+card['id']).json()['content']['text']


def test_late_article_response_is_discarded_after_its_last_root_is_read(tmp_path):
    import asyncio
    import threading
    import sqlite3
    from tests.support.reader import wait_for
    root=migrated_root(tmp_path)
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
            card=wait_cards(client)[0]
            assert started.wait(3)
            client.post('/api/v3/materials/'+card['id']+'/viewed')
            release.set()
            assert returned.wait(3)
            wait_for(client,lambda:client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
            assert client.get('/api/v3/cards').json()==[]
            with sqlite3.connect(root/'state.sqlite') as db:
                assert not db.execute('SELECT 1 FROM publication_links').fetchone()
                assert not db.execute("SELECT 1 FROM items WHERE content LIKE '%Late article must not survive%'").fetchone()
                assert not db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND status IN ('pending','running')").fetchone()
        finally:
            release.set()


def test_article_host_wait_blocks_new_jobs_across_restart_but_not_other_hosts(tmp_path):
    from tests.support.reader import wait_for
    from tests.support.subscriptions import add_source
    root=migrated_root(tmp_path)
    calls=[]
    def response(request):
        if request.url.host=='feeds.example':
            name=request.url.path.strip('/')
            host='other.example' if name=='other' else 'blocked.example'
            return httpx.Response(200,text=f'<rss><channel><item><guid>{name}</guid><link>https://{host}/{name}</link><description>Own excerpt {name}</description></item></channel></rss>')
        calls.append(str(request.url))
        if request.url.host=='blocked.example':
            return httpx.Response(429,headers={'retry-after':'3600'})
        return httpx.Response(200,text='<article>Other host works</article>')
    options=dict(token='test-token',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        add_source(client,'https://feeds.example/first','First','News')
        presence(client,'rss')
        first=wait_cards(client)[0]
        wait_for(client,lambda:'недоступна' in client.get('/api/v3/materials/'+first['id']).json()['content']['completeness'].get('message',''))
        add_source(client,'https://feeds.example/second','Second','News')
        add_source(client,'https://feeds.example/other','Other','News')
        cards=wait_cards(client,3)
        other=next(card for card in cards if card['feed_title']=='Other')
        wait_for(client,lambda:'Other host works' in client.get('/api/v3/materials/'+other['id']).json()['content']['text'])
        assert calls==['https://blocked.example/first','https://other.example/other']
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        add_source(client,'https://feeds.example/third','Third','News')
        presence(client,'rss')
        wait_cards(client,4)
        third=next(card for card in client.get('/api/v3/cards').json() if card['feed_title']=='Third')
        wait_for(client,lambda:'недоступна' in client.get('/api/v3/materials/'+third['id']).json()['content']['completeness'].get('message',''))
        assert calls==['https://blocked.example/first','https://other.example/other']
