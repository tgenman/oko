"""Whole-post web restrictions through the real reader HTTP interface."""
from tests.support.telegram import configure_fast_web
import httpx

from tests.support.app import create_app
from tests.support.codex import CodexService, configure_codex
from tests.support.telegram import settled
from tests.support.telegram_web import external_page, mark_post_unavailable
from tests.support.v3 import TestClient


def unavailable_page(before=0, text='', media='', restricted=True):
    html = external_page([(11, text+media)], before=before)
    return mark_post_unavailable(html, 'fixture/11') if restricted else html


def test_whole_post_restriction_is_saved_as_unavailable_without_ai(tmp_path):
    fetches = []
    def response(request):
        if request.url.params.get('before'): fetches.append(str(request.url))
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0'))))
    codex = CodexService()
    options = dict(codex_client=codex, token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        configure_codex(client)
        client.patch('/api/v3/settings/oko/global', json=dict(theses=True, prompt_filters='Filter the post'))
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        assert result.status_code == 200, result.text
        cards = settled(client)
        url = '/api/v3/materials/'+cards[0]['id']
        material = client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        from tests.support.storage import database
        with database(tmp_path) as db:
            job=db.execute("SELECT p.status,j.status,j.reason_code FROM publications p JOIN jobs j ON j.kind='fetch' AND j.item=p.id WHERE p.id=%s",(cards[0]['id'],)).fetchone()
            assert job and tuple(job)==('pending_collection','unavailable','post-web-unavailable')
        assert client.get('/api/v3/status/details').json()['problems']['count'] > 0
        assert material['status']=='pending_collection'
        assert not any(block.get('text') for block in material['content'].get('blocks',[]) if block['type']=='text')
        assert not any(block['type']=='external_media' for block in material['content'].get('blocks',[]))
        assert [media for media in material['media'] if media['url'] is not None] == []
        frame = client.get('/api/v3/publication/'+cards[0]['id']).json()
        assert any(job['operation']=='fetch' and job['status']=='unavailable' for job in frame['jobs'])
        assert cards[0]['title'] == 'Пост доступен только в Telegram'
        assert not any(job['operation'] in ('filter','theses') and job['status'] in ('pending','running') for job in material['jobs'])
        assert codex.inputs == []
        baseline = len(fetches)
        client.post('/api/v3/collect'); settled(client)
        assert len(fetches) == baseline
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        material = client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert material['status']=='pending_collection'
        assert any(job['operation']=='fetch' and job['status']=='unavailable' for job in material['jobs'])
        assert client.get('/api/v3/status/details').json()['problems']['count'] > 0


import pytest
import time


def test_previous_local_text_is_kept_when_whole_post_becomes_restricted(tmp_path):
    restricted = [False]
    def response(request):
        return httpx.Response(200, text=unavailable_page(int(request.url.params.get('before', '0')), '' if restricted[0] else 'Saved own text', restricted=restricted[0]))
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        url = '/api/v3/materials/'+settled(client)[0]['id']
        from tests.support.publications import body_text
        assert body_text(client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()) == 'Saved own text'
        restricted[0] = True
        client.post(url+'/retry'); settled(client)
        content = client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert body_text(content) == 'Saved own text'
        assert content['status']!='pending_collection'
        assert 'changed' not in content and 'source_deleted' not in content
        assert client.get('/api/v3/status/details').json()['problems']['count'] == 0


def test_missing_saved_file_is_downloaded_again_without_refetching_post(tmp_path):
    from tests.support.reader import wait_for
    from tests.support.storage import database

    calls=[]
    photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
    def response(request):
        calls.append(str(request.url))
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'saved photo',headers={'content-type':'image/jpeg'})
        return httpx.Response(200,text=unavailable_page(int(request.url.params.get('before','0')),'Saved text',photo))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        wait_for(client,lambda:any(media['url'] for media in client.get('/api/v3/publication/'+item).json()['media']))
        with database(tmp_path) as db:
            media=db.execute("SELECT id,storage_key FROM media_files WHERE publication_id=%s AND status='ready'",(item,)).fetchone()
            media_id=media['id']
            (tmp_path/media['storage_key']).unlink()
        old_post_calls=sum('t.me' in url for url in calls)
        client.post('/api/v3/materials/'+item+'/retry')
        recovered=wait_for(client,lambda:(value if (value:=client.get('/api/v3/publication/'+item).json()) and any(media['status']=='ready' and media['url'] for media in value['media']) else None))
        file=next(media for media in recovered['media'] if media['id']==media_id)
        assert client.get(file['url']).content==b'saved photo'
        assert sum('t.me' in url for url in calls)==old_post_calls
        assert any(job['object_id']==media_id and job['status']=='done' for job in recovered['jobs'])


def test_retry_missing_file_preserves_source_wait_deadline(tmp_path):
    import time
    from tests.support.reader import wait_for
    from tests.support.storage import database

    calls=[]
    photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
    def response(request):
        calls.append(str(request.url))
        if request.url.host=='cdn.example':return httpx.Response(200,content=b'file',headers={'content-type':'image/jpeg'})
        return httpx.Response(200,text=unavailable_page(int(request.url.params.get('before','0')),'Saved',photo))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox'})
        item=settled(client)[0]['id']
        wait_for(client,lambda:any(media['url'] for media in client.get('/api/v3/publication/'+item).json()['media']))
        deadline=time.time()+600
        with database(tmp_path) as db:
            media=db.execute("SELECT id,storage_key FROM media_files WHERE publication_id=%s AND status='ready'",(item,)).fetchone()
            (tmp_path/media['storage_key']).unlink()
            db.execute("UPDATE jobs SET status='pending',wait_scope='test-wait',retry_at=%s,next=%s,reason_code='source-wait',reason_message='Wait' WHERE kind='download_media' AND item=%s",(deadline,time.monotonic()+600,media['id']))
        before=len([url for url in calls if 'cdn.example' in url])
        client.post('/api/v3/materials/'+item+'/retry')
        with database(tmp_path) as db:
            job=db.execute("SELECT retry_at,wait_scope FROM jobs WHERE kind='download_media' AND item=%s",(media['id'],)).fetchone()
        assert job['retry_at']==deadline and job['wait_scope']=='test-wait'
        assert len([url for url in calls if 'cdn.example' in url])==before
