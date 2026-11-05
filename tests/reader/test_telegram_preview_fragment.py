"""Forward-only web restrictions keep metadata as an explicitly incomplete excerpt."""
import time
import httpx
import pytest
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web,settled
from tests.support.telegram_web import page, forwarded_unavailable_page, preview_metadata_page
from tests.support.v3 import TestClient


def test_metadata_does_not_replace_a_genuine_full_local_copy(tmp_path):
    restricted=[False]
    def response(request):
        if request.url.path=='/fixture/11':return httpx.Response(200,text=preview_metadata_page('Short snippet'))
        return httpx.Response(200,text=forwarded_unavailable_page() if restricted[0] else page([(11,'Complete original body')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        path='/api/v3/materials/'+settled(client)[0]['id']
        restricted[0]=True;client.post(path+'/retry');settled(client)
        content=client.get(path.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert content['text']=='Complete original body'
        assert not content.get('post_web_unavailable')
        assert 'previous_versions' not in content and 'changed' not in content


def test_restricted_source_preserves_legacy_conflicting_quote(tmp_path):
    import json
    import psycopg
    from tests.support.storage import database_url

    restricted=[False]
    reply='<a class="tgme_widget_message_reply" href="/fixture/10"><div class="js-message_reply_text">Block quote</div></a>'
    def response(request):
        if request.url.path=='/fixture/11':
            return httpx.Response(200,text=preview_metadata_page('Short snippet'))
        return httpx.Response(200,text=forwarded_unavailable_page() if restricted[0] else page([(11,reply+'Complete original body')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client)[0]['id']
        with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
            raw=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            content=raw
            content['extra'].setdefault('_reader',{})['reply_preview']=dict(text='Legacy quote',author=None,source_url=None)
            db.execute('UPDATE publications SET content=%s WHERE id=%s',(json.dumps(content),item))
        path='/api/v3/materials/'+item
        assert client.get('/api/v3/publication/'+item).json()['reply_preview']['text']=='Block quote'
        restricted[0]=True
        client.post(path+'/retry')
        settled(client)
        saved=client.get('/api/v3/publication/'+item).json()
        assert not saved.get('post_web_unavailable')
        assert saved['reply_preview']['text']=='Block quote'


def test_preview_rate_limit_keeps_runtime_wait_and_saved_fragment(tmp_path):
    limited=[False]
    def response(request):
        if request.url.path=='/fixture/11':
            return httpx.Response(429,headers={'retry-after':'60'}) if limited[0] else httpx.Response(200,text=preview_metadata_page('Saved fragment'))
        return httpx.Response(200,text=forwarded_unavailable_page())
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        feed=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'}).json()['feed_id']
        path='/api/v3/materials/'+settled(client)[0]['id']
        limited[0]=True;client.post(path+'/retry')
        for _ in range(100):
            source=client.get('/api/v3/collection-status/'+feed).json()['source']
            if source['state']=='waiting':break
            time.sleep(.02)
        assert source['state']=='waiting' and source['operation']['retry_at']
        assert 'Saved fragment' in client.get(path.replace('/api/v3/materials/', '/api/v3/publication/')).json()['text']
        assert 'HTTP 429' not in str(client.get(path.replace('/api/v3/materials/', '/api/v3/publication/')).json()['content']['blocks'])
        from tests.support.storage import database
        item=path.rsplit('/',1)[-1]
        with database(tmp_path) as db:
            job=db.execute("SELECT reason_message FROM jobs WHERE kind='fetch' AND item=%s",(item,)).fetchone()
            assert db.execute('SELECT status FROM publications WHERE id=%s',(item,)).fetchone()[0]=='pending_collection'
        assert job and job['reason_message']
        assert job['reason_message'] in client.get('/api/v3/publication/'+item).json()['completeness']['message']
        import json
        old_reason=job['reason_message']
        with database(tmp_path) as db:
            raw=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            if isinstance(raw,str):raw=json.loads(raw)
            raw['extra']['_collection_reason']=old_reason
            db.execute('UPDATE publications SET content=%s WHERE id=%s',(json.dumps(raw),item))
        client.post(path+'/retry')
        with database(tmp_path) as db:
            job=db.execute("SELECT reason_message,wait_scope,retry_at FROM jobs WHERE kind='fetch' AND item=%s",(item,)).fetchone()
        assert job and job['reason_message'] is None and job['wait_scope'] and job['retry_at']
        assert old_reason not in client.get('/api/v3/publication/'+item).json()['completeness']['message']


def test_saved_preview_is_readable_while_publication_collection_is_pending(tmp_path):
    from tests.support.storage import database
    def response(request):
        if request.url.path=='/fixture/11':
            return httpx.Response(200,text=preview_metadata_page('Saved partial text'))
        return httpx.Response(200,text=forwarded_unavailable_page())
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client)[0]['id']
        with database(tmp_path) as db:
            saved=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            manifest=saved.get('extra',{}).get('_reader',{}).get('source_manifest',{})
            assert not {'preview_fragment','preview_error','post_web_unavailable'} & manifest.keys()
            db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
        result=client.get('/api/v3/publication/'+item)
        assert result.status_code==200
        assert 'Saved partial text' in result.json()['text']
        from tests.support.cards import read_all_cards
        card=next(card for card in read_all_cards(client,'/api/v3/cards') if card['id']==item)
        assert card['title']!='Материал загружается'


def test_preview_failure_reason_lives_in_job_and_not_publication_content(tmp_path):
    from tests.support.storage import database
    def response(request):
        if request.url.path=='/fixture/11':return httpx.Response(200,text='<html><body>No metadata</body></html>')
        return httpx.Response(200,text=forwarded_unavailable_page())
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client)[0]['id']
        with database(tmp_path) as db:
            content=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            job=db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item=%s",(item,)).fetchone()
        manifest=content.get('extra',{}).get('_reader',{}).get('source_manifest',{})
        assert not {'preview_fragment','preview_error','post_web_unavailable'} & manifest.keys()
        assert job['status']=='unavailable' and job['reason_code']=='post-web-unavailable'
        assert 'фрагмент' in job['reason_message']
