"""Completed source content is collected once; discovery remains repeatable."""
import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import wait_for
from tests.support.rss import import_feed, rss, wait_cards


def test_collect_does_not_refetch_a_successfully_saved_article_or_file(tmp_path):
    calls=[]
    version=[1]
    def source(request):
        calls.append(request.url.path)
        if request.url.path=='/feed':
            return httpx.Response(200,text=rss())
        if request.url.path=='/article':
            return httpx.Response(200,text=f'<article><p>Version {version[0]}</p><img src="/image.png"></article>')
        return httpx.Response(200,content=f'image-{version[0]}'.encode(),headers={'content-type':'image/png'})
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client)[0]['id']
        original=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+item).json()) and any(file['url'] for file in value['media']) else None))
        saved_url=next(file['url'] for file in original['media'] if file['url'])
        assert client.get(saved_url).content==b'image-1'
        wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
        counts=(calls.count('/article'),calls.count('/image.png'))
        assert counts==(1,1),calls
        version[0]=2
        assert client.post('/api/v3/collect').status_code==200
        wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
        current=client.get('/api/v3/publication/'+item).json()
        assert 'source_deleted' not in current and 'changed' not in current
        from tests.support.storage import database
        with database(tmp_path) as db:
            rows=db.execute('SELECT content FROM publications WHERE id=%s OR id IN (SELECT target_publication_id FROM publication_links WHERE source_publication_id=%s)',(item,item)).fetchall()
        assert all('_input_hash' not in row[0].get('extra',{}) and '_collection_revision' not in row[0].get('extra',{}) for row in rows)
        assert 'changed' not in client.get('/api/v3/cards').json()['items'][0]
        assert (calls.count('/article'),calls.count('/image.png'))==counts
        assert any('Version 1' in str(article['content']) for article in current['linked_publications'])
        assert client.get(saved_url).content==b'image-1'


def test_retrying_a_missing_file_does_not_refetch_saved_article(tmp_path):
    calls=[]
    fail=[True]
    def source(request):
        calls.append(request.url.path)
        if request.url.path=='/feed':return httpx.Response(200,text=rss())
        if request.url.path=='/article':return httpx.Response(200,text='<article><p>Saved article</p><img src="/image.png"></article>')
        if fail[0]:return httpx.Response(503)
        return httpx.Response(200,content=b'image',headers={'content-type':'image/png'})
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'retry_initial_seconds':1,'retry_max_seconds':1})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        wait_for(client,lambda:any(job['operation']=='download_media' and job['reason_message'] for job in client.get('/api/v3/publication/'+item).json()['jobs']))
        assert calls.count('/article')==1
        feeds_before=calls.count('/feed')
        assert client.post('/api/v3/collect').status_code==200
        wait_for(client,lambda:calls.count('/feed')>feeds_before and not any(work['phase']=='collecting' for work in client.get('/api/v3/status/details').json()['work']))
        assert calls.count('/article')==1
        fail[0]=False
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:any(file['status']=='ready' and file['url'] for file in client.get('/api/v3/publication/'+item).json()['media']))
        assert calls.count('/article')==1


def test_failed_media_is_retried_until_saved_without_refetching_article(tmp_path):
    import time
    calls=[]
    offset=[0.0]
    def source(request):
        calls.append(request.url.path)
        if request.url.path=='/feed':return httpx.Response(200,text=rss())
        if request.url.path=='/article':return httpx.Response(200,text='<article><p>Saved article</p><img src="/image.png"></article>')
        if calls.count('/image.png')==1:return httpx.Response(503)
        return httpx.Response(200,content=b'image',headers={'content-type':'image/png'})
    options=dict(token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source),
                 clock=lambda:time.monotonic()+offset[0],wall_clock=lambda:time.time()+offset[0])
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'retry_initial_seconds':1,'retry_max_seconds':2})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        wait_for(client,lambda:any(job['operation']=='download_media' and job['reason_message'] for job in client.get('/api/v3/publication/'+item).json()['jobs']))
        offset[0]+=61
        wait_for(client,lambda:any(file['status']=='ready' and file['url'] for file in client.get('/api/v3/publication/'+item).json()['media']))
        assert calls.count('/image.png')==2
        assert calls.count('/article')==1


def test_rediscovery_does_not_replace_a_collected_telegram_post(tmp_path,monkeypatch):
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import page
    from oko.collection.telegram.adapter import TelegramWebAdapter
    observed=[]
    original_fetch=TelegramWebAdapter.fetch
    async def observed_fetch(adapter,reference):
        observed.append(reference.url)
        return await original_fetch(adapter,reference)
    monkeypatch.setattr(TelegramWebAdapter,'fetch',observed_fetch)
    text=['Saved version']
    def source(request):
        return httpx.Response(200,text=page([(11,text[0])]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        from tests.support.publications import body_text
        assert 'Saved version' in body_text(client.get('/api/v3/publication/'+item).json())
        text[0]='Remote edit'
        assert client.post('/api/v3/collect').status_code==200
        wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
        assert 'Saved version' in body_text(client.get('/api/v3/publication/'+item).json())
        assert observed==[]


def test_linked_article_worker_calls_web_adapter_without_legacy_source(tmp_path,monkeypatch):
    from oko.collection.web_adapter import WebAdapter
    fetch=WebAdapter.fetch
    seen=[]
    async def observed_fetch(adapter,reference):
        seen.append(reference.url)
        return await fetch(adapter,reference)
    monkeypatch.setattr(WebAdapter,'fetch',observed_fetch)
    def source(request):
        if request.url.path=='/feed':return httpx.Response(200,text=rss())
        return httpx.Response(200,text='<article><p>Article collected by adapter</p></article>')
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        result=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+item).json()) and value.get('linked_publications') and any('Article collected by adapter' in str(article['content']) for article in value['linked_publications']) else None))
        assert len(result['linked_publications'])==1
        assert seen==['https://source.test/article']


def test_retry_of_rss_enclosure_does_not_refetch_the_feed_record(tmp_path):
    calls=[]
    unavailable=[True]
    def source(request):
        calls.append(request.url.path)
        if request.url.path=='/feed':
            return httpx.Response(200,text='<rss><channel><item><guid>one</guid><title>Post</title><description>Body</description><enclosure url="https://source.test/file.pdf" type="application/pdf"/></item></channel></rss>')
        return httpx.Response(503) if unavailable[0] else httpx.Response(200,content=b'file',headers={'content-type':'application/pdf'})
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        wait_for(client,lambda:any(job['operation']=='download_media' and job['reason_message'] for job in client.get('/api/v3/publication/'+item).json()['jobs']))
        from tests.support.storage import database
        def fetch_generation():
            with database(tmp_path) as db:
                row=db.execute("SELECT generation FROM jobs WHERE kind='fetch' AND item=%s",(item,)).fetchone()
                return row[0] if row else None
        before_fetch=fetch_generation()
        feeds_before=calls.count('/feed')
        assert client.post('/api/v3/collect').status_code==200
        wait_for(client,lambda:calls.count('/feed')>feeds_before and not any(work['phase']=='collecting' for work in client.get('/api/v3/status/details').json()['work']))
        assert fetch_generation()==before_fetch
        feeds_before=calls.count('/feed')
        unavailable[0]=False
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        wait_for(client,lambda:any(file['status']=='ready' and file['url'] for file in client.get('/api/v3/publication/'+item).json()['media']))
        assert calls.count('/feed')==feeds_before


def test_unavailable_article_is_a_job_result_without_source_deleted_flag(tmp_path):
    from tests.support.storage import database
    def source(request):
        return httpx.Response(200,text=rss()) if request.url.path=='/feed' else httpx.Response(404)
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        value=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and any(job['operation']=='fetch' and job['status']=='unavailable' for job in v['jobs']) else None))
        article=value['linked_publications'][0]
        assert any(job['object_id']==article['id'] and job['status']=='unavailable' and job['reason_code'] for job in value['jobs'])
        with database(tmp_path) as db:
            assert not db.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='source_deleted')").fetchone()[0]


def test_incomplete_rss_record_is_fetched_directly_by_adapter(tmp_path,monkeypatch):
    from tests.support.storage import database
    def source(request):
        if request.url.path=='/feed':return httpx.Response(200,text=rss(url=''))
        return httpx.Response(200,text='<article>unused</article>')
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client)[0]['id']
        with database(tmp_path) as db:
            db.execute("UPDATE publications SET content='{}'::jsonb,status='pending_collection' WHERE id=%s",(item,))
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        value=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and v['status']!='pending_collection' and v['content'].get('blocks') else None))
        assert any('Анонс статьи' in str(block) for block in value['content']['blocks'])


def test_failed_article_fetch_is_retried_until_success(tmp_path):
    import time
    calls=[]
    offset=[0.0]
    def source(request):
        calls.append(request.url.path)
        if request.url.path=='/feed':return httpx.Response(200,text=rss())
        if calls.count('/article')==1:return httpx.Response(503)
        return httpx.Response(200,text='<article><p>Recovered article</p></article>')
    options=dict(token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source),
                 clock=lambda:time.monotonic()+offset[0],wall_clock=lambda:time.time()+offset[0])
    with TestClient(create_app(tmp_path,**options),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'retry_initial_seconds':1,'retry_max_seconds':2})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        wait_for(client,lambda:any(job['operation']=='fetch' and job['reason_message'] for job in client.get('/api/v3/publication/'+item).json()['jobs']))
        offset[0]+=61
        value=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and any('Recovered article' in str(article['content']) for article in v['linked_publications']) else None))
        assert calls.count('/article')==2
        assert value['linked_publications'][0]['status']=='ready'


def test_new_rss_publications_are_still_discovered_after_a_successful_collection(tmp_path):
    entries=[('first','First body')]
    def source(request):
        if request.url.path=='/feed':
            items=''.join(f'<item><guid>{identity}</guid><title>{identity}</title><description>{body}</description></item>' for identity,body in entries)
            return httpx.Response(200,text='<rss><channel>'+items+'</channel></rss>')
        return httpx.Response(404)
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        first=wait_cards(client)[0]['id']
        entries.insert(0,('second','Second body'))
        assert client.post('/api/v3/collect').status_code==200
        cards=wait_cards(client,count=2)
        assert first in {card['id'] for card in cards}
        contents=[client.get('/api/v3/publication/'+card['id']).json()['content'] for card in cards]
        assert any('First body' in str(content) for content in contents)
        assert any('Second body' in str(content) for content in contents)


def test_pending_telegram_members_without_body_are_not_treated_as_collected(tmp_path):
    from tests.support.storage import database
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import page
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Post')])))),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        with database(tmp_path) as db:
            db.execute("UPDATE publications SET status='pending_collection',content=%s::jsonb WHERE id=%s",('{"extra":{"telegram":{"members":[11]}}}',item))
        response=client.get('/api/v3/publication/'+item)
        assert response.status_code==200, response.text
        assert response.json()['status']=='pending_collection'
        assert response.json()['content']['extra']['telegram']['members']==[11]
        assert response.json()['content']['blocks']==[]


def test_retry_rejects_late_response_from_previous_fetch_generation(tmp_path):
    import asyncio
    import threading
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    attempts=[0]
    async def source(request):
        if request.url.path=='/feed':return httpx.Response(200,text=rss())
        attempts[0]+=1
        if attempts[0]==1:
            started.set()
            while not release.is_set():
                try:await asyncio.sleep(.01)
                except asyncio.CancelledError:pass
            finished.set()
            return httpx.Response(200,text='<article><p>Old late response</p></article>')
        return httpx.Response(200,text='<article><p>Current response</p></article>')
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        try:
            client.patch('/api/v3/settings/oko/global',json={'theses':False})
            import_feed(client);presence(client,'reader')
            item=wait_cards(client,idle=False)[0]['id']
            assert started.wait(10)
            assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
            current=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and any('Current response' in str(article['content']) for article in v['linked_publications']) else None))
            release.set()
            assert finished.wait(10)
            wait_for(client,lambda:client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
            latest=client.get('/api/v3/publication/'+item).json()
            assert latest['linked_publications'][0]['content']==current['linked_publications'][0]['content']
            assert 'Old late response' not in str(latest['linked_publications'])
        finally:
            release.set()


def test_incomplete_telegram_post_is_fetched_directly_by_adapter(tmp_path,monkeypatch):
    from tests.support.storage import database
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import page
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Recovered post')])))),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        with database(tmp_path) as db:
            db.execute("UPDATE publications SET status='pending_collection',content=%s::jsonb WHERE id=%s",('{"extra":{"telegram":{"members":[11]}}}',item))
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        value=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and v['status']!='pending_collection' and 'Recovered post' in str(v['content']['blocks']) else None))
        assert value['content']['extra']['telegram']['members']==[11]


def test_incomplete_youtube_publication_is_fetched_directly_by_adapter(tmp_path,monkeypatch):
    from tests.support.storage import database
    from tests.support.youtube import VIDEO,PNG,feed_xml,subscribe,History
    def source(request):
        if request.url.path=='/feeds/videos.xml':return httpx.Response(200,text=feed_xml())
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    class Captions:
        async def fetch(self,video):return 'captions'
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source),youtube_captions=Captions(),youtube_history=History([VIDEO])),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        subscribe(client);presence(client,'reader')
        item=wait_cards(client)[0]['id']
        with database(tmp_path) as db:
            db.execute("UPDATE publications SET status='pending_collection',content='{}'::jsonb WHERE id=%s",(item,))
        assert client.post('/api/v3/materials/'+item+'/retry').status_code==200
        value=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and v['status']!='pending_collection' and v['content'].get('blocks') else None))
        assert any(block['type']=='external_media' and block['kind']=='video' for block in value['content']['blocks'])


def test_rss_discovery_accepts_adapter_content_without_material(tmp_path,monkeypatch):
    def source(request):return httpx.Response(200,text=rss(url=''))
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source)),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        value=wait_for(client,lambda:(v if (v:=client.get('/api/v3/publication/'+item).json()) and 'Анонс статьи' in str(v['content']['blocks']) else None))
        assert value['platform']=='rss'


def test_telegram_discovery_accepts_adapter_content_without_material(tmp_path,monkeypatch):
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import page
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'New post')])))),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        value=client.get('/api/v3/publication/'+item).json()
        assert any('New post' in str(block) for block in value['content']['blocks'])
        assert value['content']['extra']['telegram']['members']==[11]


def test_youtube_discovery_accepts_adapter_content_without_material(tmp_path,monkeypatch):
    from tests.support.youtube import VIDEO,PNG,feed_xml,subscribe,History
    def source(request):
        if request.url.path=='/feeds/videos.xml':return httpx.Response(200,text=feed_xml())
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    class Captions:
        async def fetch(self,video):return 'captions'
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source),youtube_captions=Captions(),youtube_history=History([VIDEO])),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        subscribe(client);presence(client,'reader')
        item=wait_cards(client)[0]['id']
        value=client.get('/api/v3/publication/'+item).json()
        assert any(block['type']=='external_media' and block['kind']=='video' for block in value['content']['blocks'])


def test_injected_source_fetch_also_uses_publication_adapter(tmp_path,monkeypatch):
    import time
    from oko.collection.contracts import DiscoveryResult
    from tests.support.legacy_source import FetchResult, Publication
    from tests.support.source import StructuredSource
    from tests.support.synthetic_material import material
    from oko.collection.adapters import ExternalReference
    original=StructuredSource.fetch
    seen=[]
    async def adapter_fetch(self,reference):
        assert isinstance(reference,ExternalReference)
        seen.append(reference)
        return await original(self,reference)
    monkeypatch.setattr(StructuredSource,'fetch',adapter_fetch)
    class Synthetic:
        changes=None
        async def discover(self,cursor):return DiscoveryResult((Publication('one','https://source.test/one',time.time()),),'one')
        async def fetch(self,publication):return FetchResult(material('Title','From adapter'))
    with TestClient(create_app(tmp_path,token='test-token',source=Synthetic()),headers={'Authorization':'Bearer test-token'}) as client:
        presence(client,'reader')
        item=wait_cards(client)[0]['id']
        assert 'From adapter' in str(client.get('/api/v3/publication/'+item).json()['content']['blocks'])
        assert seen


def test_worker_discovers_rss_without_source_wrapper(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=rss(url='')))),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        import_feed(client);presence(client,'reader')
        item=wait_cards(client)[0]['id']
        assert 'Анонс статьи' in str(client.get('/api/v3/publication/'+item).json()['content']['blocks'])


def test_worker_discovers_telegram_without_source_wrapper(tmp_path,monkeypatch):
    from tests.support.telegram import configure_fast_web,settled
    from tests.support.telegram_web import page
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Telegram post')])))),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        assert 'Telegram post' in str(client.get('/api/v3/publication/'+item).json()['content']['blocks'])


def test_worker_discovers_youtube_without_source_wrapper(tmp_path,monkeypatch):
    from tests.support.youtube import VIDEO,PNG,feed_xml,subscribe,History
    def source(request):
        if request.url.path=='/feeds/videos.xml':return httpx.Response(200,text=feed_xml())
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    class Captions:
        async def fetch(self,video):return 'captions'
    with TestClient(create_app(tmp_path,token='test-token',rss_enabled=True,rss_transport=httpx.MockTransport(source),youtube_captions=Captions(),youtube_history=History([VIDEO])),headers={'Authorization':'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        subscribe(client);presence(client,'reader')
        item=wait_cards(client)[0]['id']
        value=client.get('/api/v3/publication/'+item).json()
        assert any(block['type']=='external_media' and block['kind']=='video' for block in value['content']['blocks'])
