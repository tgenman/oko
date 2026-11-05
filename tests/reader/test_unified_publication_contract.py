"""A publication response owns its immediate reading inputs and job states."""
import httpx

from tests.support.app import create_app
from tests.support.publication_pipeline import publication_root
from tests.support.reader import HEADERS, presence, wait_for
from tests.support.rss import import_feed, wait_cards
from tests.support.v3 import TestClient


def rss_source(request):
    if request.url.host=='cdn.example':
        return httpx.Response(200,content=b'image',headers={'content-type':'image/png'})
    if request.url.host=='web.example':
        return httpx.Response(200,text='<title>Article title</title><article><p>Article body</p><img src="https://cdn.example/article.png"></article>')
    return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><title>Announcement</title><link>https://web.example/article</link><description>Own excerpt</description></item></channel></rss>')


def test_saved_media_has_the_original_file_and_publication_links(tmp_path):
    from tests.support.storage import database
    root=publication_root(tmp_path)
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(rss_source)),headers=HEADERS) as client:
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        wait_for(client,lambda:any(m['kind']=='image' for m in client.get('/api/v3/publication/'+item).json().get('media',[])))
        with database(root) as db:
            article_row=db.execute("SELECT id,content FROM publications WHERE platform='other_web'").fetchone()
            article=article_row[1]
            parent=db.execute("SELECT content FROM publications WHERE id=%s",(item,)).fetchone()[0]
            media=db.execute("SELECT id,source_url FROM media_files WHERE publication_id=(SELECT id FROM publications WHERE platform='other_web')").fetchone()
        assert any(b.get('type')=='publication' and b.get('publication_id')==article_row[0] and b.get('source_url')=='https://web.example/article' for b in parent['blocks'])
        block=next(b for b in article['blocks'] if b['type']=='media')
        assert block['media_id']==media[0]
        assert block['source_url']==media[1]=='https://cdn.example/article.png'
        assert block['publication_url']=='https://web.example/article'


def test_publication_response_contains_linked_article_media_and_jobs(tmp_path):
    root=publication_root(tmp_path)
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(rss_source)),headers=HEADERS) as client:
        import_feed(client)
        presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        def collected():
            response=client.get('/api/v3/publication/'+item)
            assert response.status_code==200
            value=response.json()
            return value if 'Article body' in value.get('text','') and value.get('media') else None
        result=wait_for(client,collected)
        assert {'content','filtered_content','summarized_content','processing_metadata','media','linked_publications','jobs'} <= result.keys()
        assert not any(key.startswith('_') for key in result['content'].get('extra',{}))
        assert all(set(job)=={'operation','object_id','status','reason_code','reason_message','retry_at'} for job in result['jobs'])
        article=result['linked_publications'][0]
        assert article['title']=='Article title' and article['source_url']=='https://web.example/article'
        assert any(b.get('text')=='Article body' for b in article['content']['blocks'])
        assert any(b.get('type')=='publication' and b.get('publication_id')==article['id'] and b.get('source_url')==article['source_url'] for b in result['content']['blocks'])
        image=next(m for m in result['media'] if m['id'] in {b.get('media_id') for b in article['content']['blocks']})
        assert image['source_url']=='https://cdn.example/article.png' and image['status']=='ready'
        assert client.get(image['url']).content==b'image'
        assert any(j['operation']=='download_media' and j['object_id']==image['id'] and j['status']=='done' for j in result['jobs'])


def test_unified_response_exposes_running_media_without_fetching_descriptions_again(tmp_path):
    import asyncio
    import threading
    root=publication_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    calls=[]
    async def held_source(request):
        calls.append(str(request.url))
        if request.url.host=='cdn.example':
            started.set()
            while not release.is_set():await asyncio.sleep(.01)
        return rss_source(request)
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(held_source)),headers=HEADERS) as client:
        try:
            import_feed(client);presence(client,'reader')
            item=wait_cards(client,idle=False)[0]['id']
            assert started.wait(10)
            result=client.get('/api/v3/publication/'+item).json()
            article=result['linked_publications'][0]
            image=next(m for m in result['media'] if m['id'] in {b.get('media_id') for b in article['content']['blocks']})
            assert (image['status'],image['url'],image['source_url'])==('pending_download',None,'https://cdn.example/article.png')
            assert any(j['operation']=='download_media' and j['object_id']==image['id'] and j['status']=='running' for j in result['jobs'])
            before=list(calls)
            again=client.get('/api/v3/publication/'+item)
            assert again.status_code==200 and calls==before
        finally:
            release.set()


def test_external_media_carries_its_display_data_in_a_common_block(tmp_path):
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import external_page, AUDIO
    def source(request):
        return httpx.Response(200,text=external_page([(11,'Own text'+AUDIO)],before=int(request.url.params.get('before','0'))))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source)),headers=HEADERS) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        result=client.get('/api/v3/publication/'+item).json()
        block=next(b for b in result['content']['blocks'] if b['type']=='external_media')
        assert block['kind']=='audio' and block['url']=='https://t.me/fixture/11'
        assert block['title']=='A recorded lesson' and block['author']=='Fixture author'


def test_album_media_blocks_keep_their_own_message_links(tmp_path):
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import page
    photos=''.join(f'<a class="grouped_media_wrap" href="https://t.me/fixture/{i}?single"><span class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/{i}.jpg\')"></span></a>' for i in (5,6))
    def source(request):
        if request.url.host=='cdn.example':return httpx.Response(200,content=b'image',headers={'content-type':'image/jpeg'})
        before=int(request.url.params.get('before','0'))
        return httpx.Response(200,text=page([(5,'Album'+photos)] if not before or 5<before else []))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(source)),headers=HEADERS) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).status_code==200
        item=settled(client,timeout=15)[0]['id']
        content=client.get('/api/v3/publication/'+item).json()['content']
        from tests.support.storage import database
        with database(tmp_path) as db:
            saved=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()
        assert content['extra']['telegram']['members']==[5,6]
        assert saved[0]['extra']['telegram']['members']==[5,6]
        assert '_reader' not in saved[0].get('extra',{})
        blocks=[b for b in content['blocks'] if b['type']=='media']
        assert [(b['source_url'],b['publication_url']) for b in blocks]==[
            ('https://cdn.example/5.jpg','https://t.me/fixture/5'),('https://cdn.example/6.jpg','https://t.me/fixture/6')]


def test_response_keeps_accepted_summary_while_new_attempt_is_running(tmp_path):
    import asyncio
    import threading
    from tests.reviews.test_publication_processing import coordinated_transport, setup
    from tests.support.telegram_web import page
    from tests.support.cards import read_all_cards
    root=publication_root(tmp_path)
    entered,release=threading.Event(),threading.Event()
    held=[False]
    native=coordinated_transport([])
    async def model(request):
        if held[0] and request.url.path=='/v1/chat/completions':
            entered.set()
            while not release.is_set():await asyncio.sleep(.01)
        return native.handler(request)
    source=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Original text')])))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=source,ai_transport=httpx.MockTransport(model)),headers=HEADERS) as client:
        try:
            setup(client)
            item=wait_for(client,lambda:(cards[0]['id'] if (cards:=read_all_cards(client,'/api/v3/cards')) and client.get('/api/v3/publication/'+cards[0]['id']).json()['summarized_content'].get('points') else None))
            before=client.get('/api/v3/publication/'+item).json()['summarized_content']
            held[0]=True
            assert client.post('/api/v3/materials/'+item+'/theses/retry').status_code==200
            assert entered.wait(10)
            during=client.get('/api/v3/publication/'+item).json()
            assert during['summarized_content']==before
            assert '_reader' not in str(during['filtered_content'])
            assert any(j['operation']=='theses' and j['object_id']==item and j['status']=='running' for j in during['jobs'])
        finally:
            release.set()


def test_common_content_is_readable_without_reader_metadata(tmp_path):
    from tests.support.cards import read_all_cards
    from tests.support.storage import database
    root=publication_root(tmp_path)
    with TestClient(create_app(root,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(rss_source)),headers=HEADERS) as client:
        import_feed(client);presence(client,'reader')
        item=wait_cards(client,idle=False)[0]['id']
        wait_for(client,lambda:'Article body' in client.get('/api/v3/publication/'+item).json().get('text',''))
        with database(root) as db:
            db.execute("UPDATE publications SET content=content #- '{extra,_reader}' WHERE id=%s OR id IN (SELECT target_publication_id FROM publication_links WHERE source_publication_id=%s)",(item,item))
            db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,source_url,name,mime_type,status) VALUES ('sample-video',%s,'video','https://cdn.example/v.mp4','https://cdn.example/v.mp4','video.mp4','video/mp4','pending_download')",(item,))
            db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,source_url,name,mime_type,status) VALUES ('sample-circle',%s,'circle','https://cdn.example/c.mp4','https://cdn.example/c.mp4','circle.mp4','video/mp4','pending_download')",(item,))
            db.execute("UPDATE publications SET content=jsonb_set(content,'{blocks}',content->'blocks' || '[{\"type\":\"media\",\"role\":\"attachment\",\"media_id\":\"sample-video\",\"source_url\":\"https://cdn.example/v.mp4\",\"publication_url\":null},{\"type\":\"media\",\"role\":\"video\",\"presentation\":\"circle\",\"media_id\":\"sample-circle\",\"source_url\":\"https://cdn.example/c.mp4\",\"publication_url\":null}]'::jsonb) WHERE id=%s",(item,))
        response=client.get('/api/v3/publication/'+item)
        assert response.status_code==200
        assert any(block.get('text')=='Own excerpt' for block in response.json()['content']['blocks'])
        assert next(media for media in response.json()['media'] if media['id']=='sample-video')['kind']=='video'
        assert next(media for media in response.json()['media'] if media['id']=='sample-circle')['kind']=='circle'
        assert any(card['id']==item and card['title']=='Announcement' for card in read_all_cards(client,'/api/v3/cards'))


def test_card_and_publication_prefer_the_saved_title_over_body(tmp_path):
    from tests.support.storage import database
    from tests.support.telegram import configure_fast_web,settled
    from tests.support.telegram_web import page
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda _:httpx.Response(200,text=page([(11,'Other body')])))),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client)[0]['id']
        with database(tmp_path) as db:db.execute("UPDATE publications SET title='Canonical title' WHERE id=%s",(item,))
        assert client.get('/api/v3/publication/'+item).json()['title']=='Canonical title'
        assert next(card for card in client.get('/api/v3/cards').json()['items'] if card['id']==item)['title']=='Canonical title'
