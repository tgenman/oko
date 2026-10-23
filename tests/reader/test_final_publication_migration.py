"""Final offline conversion and removed capabilities at the reader boundary."""
import httpx
import pytest
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import HEADERS,wait_for
from tests.support.subscriptions import add_sources
from tests.support.v3 import TestClient
from tests.support.youtube import PNG
from oko.storage.migration import migrate_publications


@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('partial',[False,True])
def test_rss_cached_article_and_image_survive_offline_conversion(tmp_path,enabled,partial):
    source=tmp_path/'legacy'
    def response(request):
        if request.url.path=='/feed':
            return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><link>https://web.test/article</link><title>Entry</title><description>Announcement</description></item></channel></rss>')
        if request.url.path=='/missing.png':
            return httpx.Response(503)
        if request.url.path=='/image.png':
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        return httpx.Response(200,text='<article>Cached article<img src="/image.png">'+('<img src="/missing.png">' if partial else '')+'</article>')
    options=dict(token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        add_sources(client,[('https://source.test/feed','RSS','Inbox')])
        card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
        wait_for(client,lambda:bool(client.get('/api/v3/materials/'+card['id']).json()['content']['images']))
        if not enabled:
            assert client.patch('/api/v3/feeds/'+card['feed_id'],json={'transport':None}).status_code==200
    # Saved date boundaries are historical data, not a new API operation.
    from oko.storage.store import Store
    import json
    from datetime import date,timedelta
    from oko.subscriptions.history import InitialHistory
    saved=Store(source,lambda _:None)
    try:
        with saved.db:
            saved.db.execute('UPDATE initial_history SET selection=?',(json.dumps(InitialHistory(mode='date',count=None,date=str(date.today()-timedelta(days=1)),timezone='UTC').selection()),))
    finally:
        saved.db.close()
    destination=tmp_path/'converted'
    report=migrate_publications(source,destination)
    assert report['success'],report
    assert any(f['platform']=='rss' for f in report['feeds'])
    assert report['history_boundaries'][0]['selection']['mode']=='date'
    assert 'count' not in report['history_boundaries'][0]['selection']
    def offline(request):
        raise AssertionError('Reading transferred latest data must not require a network')
    with TestClient(create_app(destination/'storage',token='test',api_only=True,rss_transport=httpx.MockTransport(offline)),headers=HEADERS) as client:
        material=client.get('/api/v3/materials/'+card['id']).json()
        assert 'Cached article' in material['content']['text']
        assert client.get(material['content']['attachments'][0]['url']).content==PNG
        assert len(client.get('/api/v3/cards').json())==1


def test_migration_refuses_unknown_native_source_kind_instead_of_skipping_it(tmp_path):
    from oko.storage.store import Store
    from tests.support.telegram import Telegram,connect_request
    source=tmp_path/'legacy'
    with TestClient(create_app(source,token='test',api_only=True,telegram_client=Telegram()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
    store=Store(source,lambda _:None)
    try:
        with store.db:
            store.db.execute("UPDATE feeds SET config='{}'")
    finally:
        store.db.close()
    report=migrate_publications(source,tmp_path/'converted')
    assert not report['success']
    assert any('source_kind' in entry['reason'] for entry in report['conflicts'])
    assert not (tmp_path/'converted'/'storage').exists()


def test_current_contract_has_no_restore_or_previous_version_operations():
    from tests.api.test_contract import bundled
    paths=bundled()['paths']
    assert '/api/v3/materials/{item}/filter/restore' not in paths
    assert not any('/versions/' in path for path in paths)


def test_new_telegram_connection_rejects_date_selection(tmp_path):
    from datetime import date,timedelta
    from tests.support.telegram_web import page
    def response(request):
        return httpx.Response(200,text=page([(11,'Post')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        presence(client,'reader')
        result=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',
            initial_history={'mode':'date','date':str(date.today()-timedelta(days=1)),'timezone':'UTC'}))
        assert result.status_code==422,result.text
        assert client.get('/api/v3/subscriptions').json()['feeds']==[]


def test_canonical_reader_and_results_use_sql_without_persistent_markdown_projection(tmp_path):
    from tests.support.publication_pipeline import migrated_root
    from tests.support.telegram_web import page
    from tests.support.models import model_transport
    root=migrated_root(tmp_path)
    def response(request):
        if request.url.host=='cdn.test':
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        return httpx.Response(200,text=page([(11,'Latest SQL text<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.test/image.png\')"></a>')]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response),ai_transport=model_transport([]))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':'','telegram_http_rps':10})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',initial_history={'mode':'count','count':'1'}))
        card=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
        item=card['id']
        material=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert material['content']['text']=='Latest SQL text'
        assert client.get(material['content']['attachments'][0]['url']).content==PNG
        assert not [p for p in (root/'content').rglob('*.md') if p.name in ('original.md','filter.md','theses.md')]
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        material=client.get('/api/v3/materials/'+item).json()
        assert material['content']['text']=='Latest SQL text' and material['theses']['result']
        assert client.get(material['content']['attachments'][0]['url']).content==PNG
        assert not [p for p in (root/'content').rglob('*.md') if p.name in ('original.md','filter.md','theses.md')]


def test_repeating_offline_copy_preserves_already_canonical_cursor_and_content(tmp_path):
    import sqlite3
    from tests.support.publication_pipeline import migrated_root
    from tests.support.telegram_web import page
    from tests.support.models import model_transport
    root=migrated_root(tmp_path)
    options=dict(token='test',api_only=True,ai_transport=model_transport([]),rss_transport=httpx.MockTransport(lambda r:httpx.Response(200,text=page([(11,'Preserved canonical text')]))))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        connected=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox','initial_history':{'mode':'count','count':'1'}})
        feed=connected.json()['feed_id']
        item=wait_for(client,lambda:next((c for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))['id']
    with sqlite3.connect(root/'state.sqlite') as db:
        before=db.execute('SELECT cursor FROM feeds WHERE id=?',(feed,)).fetchone()[0]
    destination=tmp_path/'second-copy'
    report=migrate_publications(root,destination)
    assert report['success'],report
    with sqlite3.connect(destination/'storage'/'state.sqlite') as db:
        assert db.execute('SELECT cursor FROM feeds WHERE id=?',(feed,)).fetchone()[0]==before
    with TestClient(create_app(destination/'storage',**options),headers=HEADERS) as client:
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Preserved canonical text'
        assert [c['id'] for c in client.get('/api/v3/cards').json()]==[item]


def test_new_cli_store_uses_canonical_publication_pipeline(tmp_path):
    from tests.support.processes import launch,connect,cleanup
    from uuid import UUID
    process=launch(tmp_path,api_only=True,web_media=True)
    try:
        with connect(tmp_path,process) as client:
            client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':'','telegram_http_rps':10})
            result=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox','initial_history':{'mode':'count','count':'1'}})
            assert result.status_code==200,result.text
            card=wait_for(client,lambda:next(iter(client.get('/api/v3/cards').json()),None))
            UUID(card['id'])
            material=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+card['id']).json())['content']['attachments'] else None))
            UUID(material['content']['attachments'][0]['url'].rsplit('/',1)[-1])
            client.post('/api/v3/stop')
        assert process.wait(timeout=5)==0
    finally:
        cleanup(process)


def test_cli_requires_offline_copy_before_opening_populated_legacy_store(tmp_path):
    from oko.storage.store import Store
    from oko.subscriptions.subscriptions import AddFeed
    from tests.support.processes import launch,cleanup
    import hashlib
    store=Store(tmp_path,lambda _:None)
    try:
        store.subscriptions.add_feed(AddFeed(url='https://source.test/feed',verification='fixture',title='Saved',folder='Inbox'))
    finally:
        store.db.close()
    before=hashlib.sha256((tmp_path/'state.sqlite').read_bytes()).hexdigest()
    process=launch(tmp_path,api_only=True)
    try:
        assert process.wait(timeout=5)==1
        assert 'офлайн-перенос' in process.log_path.read_text()
        assert hashlib.sha256((tmp_path/'state.sqlite').read_bytes()).hexdigest()==before
    finally:
        cleanup(process)


def test_disabled_rss_does_not_retry_linked_article_after_restart(tmp_path):
    from tests.support.publication_pipeline import migrated_root
    import time
    root=migrated_root(tmp_path)
    calls=[]
    def response(request):
        if request.url.path=='/feed':
            return httpx.Response(200,text='<rss><channel><item><guid>entry</guid><title>Title</title><link>https://web.test/article</link><description>Announcement</description></item></channel></rss>')
        calls.append(str(request.url))
        return httpx.Response(503)
    options=dict(token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':1,'retry_max_seconds':1})
        feed=add_sources(client,[('https://source.test/feed','RSS','Inbox')]).json()['feeds'][0]
        wait_for(client,lambda:bool(calls))
        assert client.patch('/api/v3/feeds/'+feed['id'],json={'transport':None}).status_code==200
    before=len(calls)
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        presence(client,'reader')
        time.sleep(1.2)
        assert len(calls)==before
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['transport'] is None


@pytest.mark.parametrize('status',['pending','failed','unavailable'])
def test_unknown_pending_job_blocks_migration_without_publishing_partial_storage(tmp_path,status):
    from oko.storage.store import Store
    source=tmp_path/'legacy'
    store=Store(source,lambda _:None)
    with store.db:
        store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('unknown-old-stage','orphan',?)",(status,))
    store.db.close()
    report=migrate_publications(source,tmp_path/'converted')
    assert not report['success']
    assert any(conflict.get('job_kind')=='unknown-old-stage' for conflict in report['conflicts'])
    assert (tmp_path/'converted'/'backup'/'state.sqlite').is_file()
    assert not (tmp_path/'converted'/'storage').exists()


def test_migration_removes_old_viewed_tombstones_but_keeps_backup(tmp_path):
    import sqlite3
    from tests.support.telegram import configure_fast_web,settled
    from tests.support.telegram_web import page
    source=tmp_path/'legacy'
    with TestClient(create_app(source,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda request:httpx.Response(200,text=page([(11,'Post')])))),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        assert client.post('/api/v3/materials/'+item+'/viewed').status_code==200
    report=migrate_publications(source,tmp_path/'converted')
    assert report['success'],report
    assert item in report['removed_viewed']
    with sqlite3.connect(tmp_path/'converted'/'storage'/'state.sqlite') as db:
        assert db.execute('SELECT 1 FROM items WHERE id=?',(item,)).fetchone() is None
    with sqlite3.connect(tmp_path/'converted'/'backup'/'state.sqlite') as db:
        assert db.execute('SELECT viewed FROM items WHERE id=?',(item,)).fetchone()==(1,)


def test_migration_reports_and_drops_unverified_canonical_result(tmp_path):
    import json,sqlite3
    from tests.support.publication_pipeline import migrated_root
    from tests.support.telegram import configure_fast_web,settled
    from tests.support.telegram_web import page
    root=migrated_root(tmp_path)
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(lambda request:httpx.Response(200,text=page([(11,'Post')])))),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
    from oko.storage.store import Store
    saved=Store(root,lambda _:None)
    try:
        with saved.db:
            saved.db.execute('UPDATE items SET summarized_content=?,processing_metadata=? WHERE id=?',
                (json.dumps({'points':[{'text':'Unproven','source_publication_ids':[item]}]}),json.dumps({'summary':{'signature':'unproven'}}),item))
    finally:
        saved.db.close()
    report=migrate_publications(root,tmp_path/'again')
    assert report['success'],report
    assert any(entry.get('publication_id')==item and 'summary result removed' in entry['reason'] for entry in report['unportable'])
    with sqlite3.connect(tmp_path/'again'/'storage'/'state.sqlite') as db:
        assert db.execute('SELECT summarized_content FROM items WHERE id=?',(item,)).fetchone()==('{}',)


def test_offline_conversion_preserves_legacy_group_viewed_history(tmp_path):
    import sqlite3
    from tests.support.telegram import Telegram,connect_request,settled
    class Group(Telegram):
        async def info(self,*args):
            return dict(peer=-1001234567890,username='fixture',title='Group',kind='group',forum=False)
    source=tmp_path/'legacy'
    with TestClient(create_app(source,token='test',api_only=True,telegram_client=Group()),headers=HEADERS) as client:
        presence(client,'reader')
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        result=connect_request(client,json=dict(account='fixture',peer='@fixture',initial_history={'mode':'count','count':1}))
        assert result.status_code==200,result.text
        item=settled(client)[0]['id']
        client.post('/api/v3/materials/'+item+'/viewed')
    report=migrate_publications(source,tmp_path/'converted')
    assert report['success'],report
    assert item not in report['removed_viewed']
    with sqlite3.connect(tmp_path/'converted'/'storage'/'state.sqlite') as db:
        assert db.execute('SELECT viewed,platform FROM items WHERE id=?',(item,)).fetchone()==(1,None)
