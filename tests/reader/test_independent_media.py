"""Publication text and individual media jobs through the real reader HTTP."""
import asyncio
import threading

import httpx

from oko.storage.migration import migrate_publications
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient
from tests.support.reader import wait_for

HEADERS = {'Authorization':'Bearer test'}


def migrated_root(tmp_path):
    source = tmp_path/'old'
    with TestClient(create_app(source, token='test', api_only=True), headers=HEADERS):
        pass
    target = tmp_path/'migration'
    assert migrate_publications(source, target)['success']
    return target/'storage'


def test_edited_text_is_readable_while_new_photo_download_is_held(tmp_path):
    root = migrated_root(tmp_path)
    phase = [0]
    started, release = threading.Event(), threading.Event()
    async def response(request):
        if request.url.host == 'cdn.example':
            if phase[0]:
                started.set()
                while not release.is_set():
                    await asyncio.sleep(.01)
            return httpx.Response(200, content=b'old-photo' if phase[0]==0 else b'new-photo', headers={'content-type':'image/jpeg'})
        photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/image-'+str(phase[0])+'.jpg\')"></a>'
        return httpx.Response(200, text=page([(11, ('Initial text' if phase[0]==0 else 'Edited text')+photo)]))
    with TestClient(create_app(root, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
            item = settled(client)[0]['id']
            old = client.get('/api/v3/materials/'+item).json()
            assert client.get(old['content']['attachments'][0]['url']).content == b'old-photo'
            phase[0] = 1
            client.post('/api/v3/materials/'+item+'/retry')
            assert started.wait(3)
            updated = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/'+item).json())['content']['text']=='Edited text' else None))
            assert updated['content']['attachments'] == []
            assert updated['content']['completeness']['state'] == 'incomplete'
            release.set()
            complete = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
            assert client.get(complete['content']['attachments'][0]['url']).content == b'new-photo'
            assert complete['content']['completeness']['state'] == 'complete'
            assert complete['content']['previous_versions'] == []
        finally:
            release.set()


def test_viewed_cancels_a_media_job_and_rejects_its_late_file(tmp_path):
    root = migrated_root(tmp_path)
    started, release = threading.Event(), threading.Event()
    async def response(request):
        if request.url.host == 'cdn.example':
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
            return httpx.Response(200, content=b'late-private-photo', headers={'content-type':'image/jpeg'})
        photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/late.jpg\')"></a>'
        return httpx.Response(200, text=page([(11, 'Saved text'+photo)]))
    with TestClient(create_app(root, token='test', api_only=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
            assert started.wait(3)
            cards = client.get('/api/v3/cards').json()
            assert len(cards)==1
            item = cards[0]['id']
            assert client.get('/api/v3/status').status_code == 200
            assert client.post('/api/v3/materials/'+item+'/viewed').status_code == 200
            release.set()
            wait_for(client, lambda: client.get('/api/v3/status').json()['work']==[{'phase':'idle'}])
            assert client.get('/api/v3/materials/'+item).status_code == 404
            assert not any(b'late-private-photo' in path.read_bytes() for path in root.rglob('*') if path.is_file())
        finally:
            release.set()


def test_retry_delays_are_global_settings_and_validate_the_effective_pair(tmp_path):
    root = migrated_root(tmp_path)
    with TestClient(create_app(root, token='test', api_only=True), headers=HEADERS) as client:
        read = '/api/v3/settings/retries'
        write = '/api/v3/settings/oko/global'
        assert client.get(read).json() == {'initial_seconds':60, 'max_seconds':3600}
        response = client.patch(write, json={'retry_initial_seconds':2, 'retry_max_seconds':8})
        assert response.status_code == 200, response.text
        assert client.get(read).json() == {'initial_seconds':2, 'max_seconds':8}
        assert client.patch(write, json={'retry_initial_seconds':9}).status_code == 422
        assert client.get(read).json() == {'initial_seconds':2, 'max_seconds':8}
    with TestClient(create_app(root, token='test', api_only=True), headers=HEADERS) as client:
        assert client.get(read).json() == {'initial_seconds':2, 'max_seconds':8}
        assert client.patch(write, json={'retry_initial_seconds':None, 'retry_max_seconds':None}).status_code == 200
        assert client.get(read).json() == {'initial_seconds':60, 'max_seconds':3600}


def test_one_file_failure_keeps_other_files_and_backoff_survives_restart(tmp_path):
    import time
    root = migrated_root(tmp_path)
    offset = [0.]
    calls = {'bad':0, 'good':0}
    def response(request):
        if request.url.host == 'cdn.example':
            name = 'bad' if request.url.path == '/bad.jpg' else 'good'
            calls[name] += 1
            if name=='bad' and calls[name]<=2:
                return httpx.Response(503)
            return httpx.Response(200, content=name.encode(), headers={'content-type':'image/jpeg'})
        photos = ''.join('<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+name+'.jpg\')"></a>' for name in ('bad','good'))
        return httpx.Response(200, text=page([(11, 'Readable text'+photos)]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response),
                   clock=lambda:time.monotonic()+offset[0], wall_clock=lambda:time.time()+offset[0])
    with TestClient(create_app(root, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global', json={'retry_initial_seconds':60, 'retry_max_seconds':240})
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        item = wait_for(client, lambda: (cards[0]['id'] if (cards := client.get('/api/v3/cards').json()) else None))
        failed = wait_for(client, lambda: (m if len((m := client.get('/api/v3/materials/'+item).json())['content']['attachments'])==1 and 'Не удалось загрузить вложение' in m['content']['completeness'].get('message','') else None))
        assert failed['content']['text']=='Readable text'
        assert calls == {'bad':1, 'good':1}
        problems = client.get('/api/v3/status').json()['problems']['current']
        assert all(affected['material_id']==item for problem in problems for affected in problem['affected'] if affected['kind']=='material')
    with TestClient(create_app(root, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Readable text'
        assert calls == {'bad':1, 'good':1}
        offset[0] += 61
        wait_for(client, lambda: calls['bad']==2)
        assert calls['good']==1
        offset[0] += 61
        assert client.get('/api/v3/materials/'+item).json()['content']['completeness']['state']=='incomplete'
        assert calls['bad']==2
        offset[0] += 60
        loaded = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/'+item).json())['content']['completeness']['state']=='complete' else None))
        assert len(loaded['content']['attachments'])==2
        assert calls == {'bad':3, 'good':1}


def test_permanent_file_failure_waits_for_manual_retry(tmp_path):
    import time
    root = migrated_root(tmp_path)
    offset = [0.]
    available = [False]
    calls = []
    def response(request):
        if request.url.host == 'cdn.example':
            calls.append(str(request.url))
            return httpx.Response(200,content=b'manually-retried',headers={'content-type':'image/jpeg'}) if available[0] else httpx.Response(404)
        photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Saved text'+photo)]))
    options = dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response),clock=lambda:time.monotonic()+offset[0])
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item = wait_for(client,lambda:(cards[0]['id'] if (cards:=client.get('/api/v3/cards').json()) else None))
        wait_for(client,lambda:'Не удалось загрузить вложение' in client.get('/api/v3/materials/'+item).json()['content']['completeness'].get('message',''))
        assert len(calls)==1
        problems = client.get('/api/v3/status').json()['problems']['current']
        assert any({'kind':'material','material_id':item} in problem['affected'] for problem in problems)
        available[0]=True
        offset[0]+=600
        assert client.get('/api/v3/materials/'+item).json()['content']['attachments']==[]
        assert len(calls)==1
        client.post('/api/v3/materials/'+item+'/retry')
        ready = wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b'manually-retried'
        assert len(calls)==2
