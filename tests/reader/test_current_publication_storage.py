"""Current Publication storage and removed capabilities at the reader boundary."""
import httpx
import pytest
from tests.support.app import create_app
from tests.support.presence import presence
from tests.support.reader import HEADERS,wait_for
from tests.support.subscriptions import add_sources
from tests.support.v3 import TestClient
from tests.support.youtube import PNG






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
    from tests.support.publication_pipeline import publication_root
    from tests.support.telegram_web import page
    from tests.support.models import model_transport
    root=publication_root(tmp_path)
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




def test_disabled_rss_does_not_retry_linked_article_after_restart(tmp_path):
    from tests.support.publication_pipeline import publication_root
    import time
    root=publication_root(tmp_path)
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










def test_unsupported_store_is_rejected_before_any_database_write(tmp_path, postgres_database_url):
    import psycopg
    from oko.storage.postgres_migrations import SchemaMismatch
    from tests.support.storage import Store
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        db.execute('CREATE TABLE feeds(id TEXT PRIMARY KEY)')
        db.execute("INSERT INTO feeds VALUES ('old-source')")
    with pytest.raises(SchemaMismatch):
        Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        assert db.execute('SELECT * FROM feeds').fetchall() == [('old-source',)]
        assert db.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall() == [('feeds',)]
