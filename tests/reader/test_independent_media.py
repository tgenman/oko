"""Publication text and individual media jobs through the real reader HTTP."""
import asyncio
import threading

import httpx
import pytest

from tests.support.publication_pipeline import migrated_root
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient
from tests.support.reader import wait_for

HEADERS = {'Authorization':'Bearer test'}



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
            assert complete['content']['attachments'][0]['url'] != old['content']['attachments'][0]['url']
            assert client.get(old['content']['attachments'][0]['url']).status_code == 404
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
        # The provider call starts before its failure is committed. Keep the
        # clock fixed until the doubled delay is visible through HTTP.
        from datetime import datetime
        def doubled_delay():
            return any(datetime.fromisoformat(w['retry_at'].replace('Z','+00:00')).timestamp()>time.time()+offset[0]+119
                for w in client.get('/api/v3/status').json()['work'] if w['phase']=='waiting' and w.get('retry_at'))
        wait_for(client,doubled_delay)
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


def test_publication_fetch_uses_configured_retry_delay_and_keeps_saved_text(tmp_path):
    import time
    from datetime import datetime
    root = migrated_root(tmp_path)
    fail = [False]
    def response(request):
        return httpx.Response(503) if fail[0] else httpx.Response(200,text=page([(11,'Saved text')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':600,'retry_max_seconds':2400})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        fail[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        def waiting():
            state = client.get('/api/v3/status').json()
            return state if any(w['phase']=='waiting' and w.get('retry_at') for w in state['work']) else None
        state=wait_for(client,waiting)
        deadline=next(datetime.fromisoformat(w['retry_at'].replace('Z','+00:00')).timestamp() for w in state['work'] if w['phase']=='waiting' and w.get('retry_at'))
        assert deadline-time.time() > 550
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Saved text'


def test_manual_fetch_retry_preserves_a_source_wait_and_saved_text(tmp_path):
    from datetime import datetime
    root=migrated_root(tmp_path)
    limited=[False]
    denied=[]
    def response(request):
        if limited[0]:
            denied.append(str(request.url))
            return httpx.Response(429,headers={'Retry-After':'120'})
        return httpx.Response(200,text=page([(11,'Saved text')]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':1,'retry_max_seconds':2})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        limited[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        def deadline():
            state=client.get('/api/v3/status').json()
            dates=[datetime.fromisoformat(w['retry_at'].replace('Z','+00:00')).timestamp() for w in state['work'] if w['phase']=='waiting' and w.get('retry_at')]
            return max(dates) if dates else None
        before=wait_for(client,deadline)
        assert len(denied)==1
        client.post('/api/v3/materials/'+item+'/retry')
        assert deadline() is not None and deadline()>=before-.1
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Saved text'
        assert len(denied)==1


def test_media_urls_and_publication_media_ids_follow_source_order(tmp_path):
    import json
    import sqlite3
    root=migrated_root(tmp_path)
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=request.url.path.strip('/').encode(),headers={'content-type':'image/jpeg'})
        photos=''.join('<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+name+'\')"></a>' for name in ('one','two','three'))
        return httpx.Response(200,text=page([(11,'Ordered photos'+photos)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        attachments=client.get('/api/v3/materials/'+item).json()['content']['attachments']
        assert [client.get(value['url']).content for value in attachments]==[b'one',b'two',b'three']
        with sqlite3.connect(root/'state.sqlite') as db:
            media_ids=json.loads(db.execute('SELECT media_ids FROM items WHERE id=?',(item,)).fetchone()[0])
        assert [value['url'].rsplit('/',1)[1] for value in attachments]==media_ids


def test_changed_resource_refreshes_description_before_downloading_new_bytes(tmp_path):
    root=migrated_root(tmp_path)
    bounded=[0]
    downloads=[]
    def response(request):
        if request.url.host=='cdn.example':
            downloads.append(request.url.path)
            return httpx.Response(200,content=request.url.path.encode(),headers={'content-type':'image/jpeg'})
        if request.url.params.get('before'):
            bounded[0]+=1
        # Resolve/discover observes A; the media precheck sees B.
        current='b' if bounded[0]>=1 else 'a'
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+current+'.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Text '+current+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=wait_for(client,lambda:(c[0]['id'] if (c:=client.get('/api/v3/cards').json()) else None))
        ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['text']=='Text b' and m['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b'/b.jpg'
        assert downloads==['/b.jpg']


def test_refreshed_signature_keeps_the_media_id_and_existing_bytes(tmp_path):
    root=migrated_root(tmp_path)
    signature=['first']
    downloads=[]
    def response(request):
        if request.url.host=='cdn.example':
            downloads.append(str(request.url))
            return httpx.Response(200,content=b'unchanged',headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg?signature='+signature[0]+'\')"></a>'
        return httpx.Response(200,text=page([(11,'Unchanged text'+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        before=client.get('/api/v3/materials/'+item).json()['content']['attachments'][0]['url']
        signature[0]='second'
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client)
        after=client.get('/api/v3/materials/'+item).json()['content']['attachments'][0]['url']
        assert before==after
        assert client.get(after).content==b'unchanged'
        assert len(downloads)==1


def test_a_real_empty_attachment_is_ready_with_size_zero(tmp_path):
    import sqlite3
    root=migrated_root(tmp_path)
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'',headers={'content-type':'application/octet-stream'})
        document='<a class="tgme_widget_message_document_wrap" download="empty.txt" href="https://cdn.example/empty.txt"><div class="tgme_widget_message_document_title">empty.txt</div></a>'
        return httpx.Response(200,text=page([(11,'Empty attachment'+document)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=wait_for(client,lambda:(cards[0]['id'] if (cards:=client.get('/api/v3/cards').json()) else None))
        ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b''
        with sqlite3.connect(root/'state.sqlite') as db:
            assert db.execute('SELECT name,size_bytes,status FROM media_files WHERE publication_id=?',(item,)).fetchone()==('empty.txt',0,'ready')


@pytest.mark.parametrize('refresh',['retry','collect'])
def test_replacement_starts_while_cancel_resistant_old_media_is_still_waiting(tmp_path,refresh):
    root=migrated_root(tmp_path)
    phase=['a']
    old_started, old_release, old_finished = threading.Event(), threading.Event(), threading.Event()
    async def response(request):
        if request.url.host=='cdn.example':
            if request.url.path=='/a.jpg':
                old_started.set()
                while not old_release.is_set():
                    try:
                        await asyncio.sleep(.01)
                    except asyncio.CancelledError:
                        pass
                old_finished.set()
            return httpx.Response(200,content=request.url.path.encode(),headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/'+phase[0]+'.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Text '+phase[0]+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
            assert old_started.wait(3)
            item=client.get('/api/v3/cards').json()[0]['id']
            phase[0]='b'
            client.post('/api/v3/materials/'+item+'/retry' if refresh=='retry' else '/api/v3/collect')
            ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['text']=='Text b' and m['content']['attachments'] else None))
            assert not old_finished.is_set()
            url=ready['content']['attachments'][0]['url']
            assert client.get(url).content==b'/b.jpg'
            old_release.set()
            assert old_finished.wait(3)
            assert client.get(url).content==b'/b.jpg'
        finally:
            old_release.set()


def test_periodic_cleanup_removes_orphans_but_keeps_an_active_download(tmp_path):
    import time
    root=migrated_root(tmp_path)
    offset=[0.]
    started,release=threading.Event(),threading.Event()
    class Held(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'first-'
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            yield b'last'
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,stream=Held(),headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Text before media'+photo)]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response),clock=lambda:time.monotonic()+offset[0])
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
            assert started.wait(3)
            item=client.get('/api/v3/cards').json()[0]['id']
            orphan=root/'media'/'unreferenced'/'file'
            orphan.parent.mkdir(parents=True)
            orphan.write_bytes(b'orphan')
            abandoned=root/'staging'/'_media'/'abandoned'
            abandoned.write_bytes(b'abandoned')
            offset[0]+=3601
            wait_for(client,lambda:not orphan.exists() and not abandoned.exists())
            release.set()
            ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
            assert client.get(ready['content']['attachments'][0]['url']).content==b'first-last'
        finally:
            release.set()


def test_cleanup_between_move_and_commit_keeps_the_admitted_final_file(tmp_path):
    """Public Store file boundary: the critical interval is synchronous, not HTTP."""
    from dataclasses import replace
    from oko.storage.store import Store
    root=migrated_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    async def response(request):
        if request.url.host=='cdn.example':
            started.set()
            await asyncio.Event().wait()
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Text'+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        connected=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()
        assert started.wait(3)
        item=client.get('/api/v3/cards').json()[0]['id']
    def fault(point):
        if point=='after_media_move':
            store.media.cleanup()
    store=Store(root,fault)
    try:
        job=store.next_job(store.clock()+3600,feeds=[connected['feed_id']],media_only=True)
        assert job is not None
        download=store.media.begin(job)
        assert download is not None
        download.temporary.write_bytes(b'complete-file')
        assert store.media.complete(job,download,replace(download.media,size_bytes=13))
        stored=store.media.file(item,job.item)
        assert stored is not None and stored[0].read_bytes()==b'complete-file'
        store.media.release(download)
    finally:
        store.db.close()


@pytest.mark.parametrize('point,committed',[('after_media_move',False),('before_media_commit',False),('after_media_commit',True)])
def test_process_death_at_media_acceptance_never_exposes_uncommitted_bytes(tmp_path,point,committed):
    import sqlite3
    from tests.support.processes import launch,connect,cleanup
    root=migrated_root(tmp_path)
    started=threading.Event()
    async def held(request):
        if request.url.host=='cdn.example':
            started.set()
            await asyncio.Event().wait()
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Process text'+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(held)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        assert started.wait(3)
        item=client.get('/api/v3/cards').json()[0]['id']
    with sqlite3.connect(root/'state.sqlite') as db:
        media_id=db.execute('SELECT id FROM media_files WHERE publication_id=?',(item,)).fetchone()[0]
    url='/api/v3/materials/'+item+'/media/'+media_id
    process=launch(root,point,api_only=True,web_media=True)
    try:
        with connect(root,process) as client:
            try:
                client.post('/api/v3/materials/'+item+'/retry')
            except httpx.TransportError:
                pass
        assert process.wait(timeout=5)==79
    finally:
        cleanup(process)
    def available(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'process-media',headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Process text'+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(available)),headers=HEADERS) as client:
        assert client.get(url).status_code==(200 if committed else 404)
        if not committed:
            client.post('/api/v3/materials/'+item+'/retry')
        ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b'process-media'


def test_telegraph_text_is_kept_while_its_image_download_waits(tmp_path):
    root=migrated_root(tmp_path)
    started,release=threading.Event(),threading.Event()
    async def response(request):
        if request.url.host=='api.telegra.ph':
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Article text']},{'tag':'img','attrs':{'src':'https://cdn.example/article.png'}}]}})
        if request.url.host=='cdn.example':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,content=b'article-image',headers={'content-type':'image/png'})
        return httpx.Response(200,text=page([(11,'Post <a href="https://telegra.ph/Fixture">article</a>')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
            assert started.wait(3)
            item=client.get('/api/v3/cards').json()[0]['id']
            content=client.get('/api/v3/materials/'+item).json()['content']
            assert 'Article text' in content['text']
            selective=client.get('/api/v3/reading/materials/'+item)
            assert selective.status_code==200,selective.text
            assert 'Article text' in content['linked_content']['articles'][0]['text']
            assert 'Не удалось' not in content['linked_content']['articles'][0]['text']
            release.set()
            ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
            assert client.get(ready['content']['linked_content']['articles'][0]['media_urls'][0]).content==b'article-image'
        finally:
            release.set()


def test_unavailable_telegraph_keeps_the_last_article_with_new_post_text(tmp_path):
    root=migrated_root(tmp_path)
    broken=[False]
    def response(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'saved-article-image',headers={'content-type':'image/png'})
        if request.url.host=='api.telegra.ph':
            if broken[0]:
                return httpx.Response(503)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Saved article text']},{'tag':'img','attrs':{'src':'https://cdn.example/article.png'}}]}})
        return httpx.Response(200,text=page([(11,('New post' if broken[0] else 'Old post')+' <a href="https://telegra.ph/Fixture">article</a>')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        initial=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert 'Saved article text' in initial['content']['text']
        old_url=initial['content']['linked_content']['articles'][0]['media_urls'][0]
        broken[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client)
        content=wait_for(client,lambda:(m['content'] if 'New post' in (m:=client.get('/api/v3/materials/'+item).json())['content']['text'] and m['content']['linked_content']['articles'][0]['error'] else None))
        assert 'New post' in content['text']
        assert 'Saved article text' in content['text']
        assert content['linked_content']['articles'][0]['error']
        new_url=content['linked_content']['articles'][0]['media_urls'][0]
        assert new_url==old_url
        assert client.get(new_url).content==b'saved-article-image'


def test_explicit_feed_refresh_rechecks_unavailable_media(tmp_path):
    root=migrated_root(tmp_path)
    unavailable=[True]
    files=[]
    def response(request):
        if request.url.host=='cdn.example':
            files.append(str(request.url))
            return httpx.Response(200,content=b'available-again',headers={'content-type':'image/jpeg'})
        if unavailable[0] and request.url.params.get('before'):
            return httpx.Response(200,text=page([]))
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Saved text'+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        connected=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()
        item=wait_for(client,lambda:(c[0]['id'] if (c:=client.get('/api/v3/cards').json()) else None))
        wait_for(client,lambda:'удалена или недоступна' in client.get('/api/v3/materials/'+item).json()['content']['completeness'].get('message',''))
        assert files==[]
        unavailable[0]=False
        client.post('/api/v3/collect',json={'scope':'feed','feed_id':connected['feed_id']})
        ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(ready['content']['attachments'][0]['url']).content==b'available-again'
        assert len(files)==1


def test_article_metadata_error_keeps_its_pending_media_identity(tmp_path):
    import sqlite3
    root=migrated_root(tmp_path)
    broken=[False]
    started,release=threading.Event(),threading.Event()
    async def response(request):
        if request.url.host=='api.telegra.ph':
            if broken[0]:
                return httpx.Response(503)
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Saved article']},{'tag':'img','attrs':{'src':'https://cdn.example/article.png'}}]}})
        if request.url.host=='cdn.example':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200,content=b'article-image',headers={'content-type':'image/png'})
        return httpx.Response(200,text=page([(11,('New post' if broken[0] else 'Old post')+' <a href="https://telegra.ph/Fixture">article</a>')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
            assert started.wait(3)
            item=client.get('/api/v3/cards').json()[0]['id']
            with sqlite3.connect(root/'state.sqlite') as db:
                article=db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=?',(item,)).fetchone()[0]
                before=db.execute('SELECT id FROM media_files WHERE publication_id=?',(article,)).fetchone()[0]
            broken[0]=True
            client.post('/api/v3/materials/'+item+'/retry')
            wait_for(client,lambda:'New post' in client.get('/api/v3/materials/'+item).json()['content']['text'])
            with sqlite3.connect(root/'state.sqlite') as db:
                assert db.execute('SELECT id FROM media_files WHERE publication_id=?',(article,)).fetchone()[0]==before
            broken[0]=False
            release.set()
            client.post('/api/v3/materials/'+item+'/retry')
            ready=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
            assert client.get(ready['content']['attachments'][0]['url']).content==b'article-image'
        finally:
            release.set()


def test_media_source_wait_survives_restart_and_does_not_pause_another_host(tmp_path):
    import time
    root=migrated_root(tmp_path)
    calls={'paused':0,'other':0}
    def response(request):
        if request.url.host=='paused.example':
            calls['paused']+=1
            return httpx.Response(429,headers={'Retry-After':'120'})
        if request.url.host=='other.example':
            calls['other']+=1
            return httpx.Response(200,content=b'other-image',headers={'content-type':'image/jpeg'})
        photos=''.join('<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://'+host+'.example/photo.jpg\')"></a>' for host in ('paused','other'))
        return httpx.Response(200,text=page([(11,'Saved text'+photos)]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':1,'retry_max_seconds':2})
        connected=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()
        item=wait_for(client,lambda:(c[0]['id'] if (c:=client.get('/api/v3/cards').json()) else None))
        content=wait_for(client,lambda:(m if (m:=client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        assert client.get(content['content']['attachments'][0]['url']).content==b'other-image'
        assert calls=={'paused':1,'other':1}
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Saved text'
        client.post('/api/v3/collect',json={'scope':'feed','feed_id':connected['feed_id']})
        status=client.get('/api/v3/status').json()
        from datetime import datetime
        waits=[datetime.fromisoformat(w['retry_at'].replace('Z','+00:00')).timestamp() for w in status['work'] if w['phase']=='waiting' and w.get('retry_at')]
        assert waits and max(waits)>time.time()+100
        assert calls=={'paused':1,'other':1}


def test_missing_publication_stops_fetch_as_unavailable_and_keeps_last_copy(tmp_path):
    import sqlite3
    root=migrated_root(tmp_path)
    missing=[False]
    def response(request):
        return httpx.Response(200,text=page([] if missing[0] else [(11,'Saved text')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=settled(client)[0]['id']
        missing[0]=True
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:client.get('/api/v3/status').json()['sources'][0]['state']=='unavailable')
        with sqlite3.connect(root/'state.sqlite') as db:
            assert db.execute("SELECT status FROM jobs WHERE kind='fetch' AND item=?",(item,)).fetchone()[0]=='unavailable'
        assert client.get('/api/v3/materials/'+item).json()['content']['text']=='Saved text'
        missing[0]=False
        client.post('/api/v3/materials/'+item+'/retry')
        wait_for(client,lambda:client.get('/api/v3/status').json()['sources'][0]['state']=='ready')


@pytest.mark.parametrize('extra', ['', '<iframe src="https://unsupported.example/embed"></iframe>', '<a href="https://telegra.ph/Missing">missing</a>'])
def test_partial_article_fetch_retries_automatically_after_restart(tmp_path,extra):
    import sqlite3
    import time
    root=migrated_root(tmp_path)
    broken=[True]
    calls=[]
    def response(request):
        if request.url.host=='api.telegra.ph':
            if 'Missing' in str(request.url):
                return httpx.Response(404)
            calls.append(str(request.url))
            return httpx.Response(503) if broken[0] else httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Recovered article']}]}})
        return httpx.Response(200,text=page([(11,'Useful post '+extra+' <a href="https://telegra.ph/Fixture">article</a>')]))
    options=dict(token='test',api_only=True,rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        configure_fast_web(client)
        client.patch('/api/v3/settings/oko/global',json={'retry_initial_seconds':1,'retry_max_seconds':1})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=wait_for(client,lambda:(c[0]['id'] if (c:=client.get('/api/v3/cards').json()) else None))
        wait_for(client,lambda:any(a.get('error') and a['source_url']=='https://telegra.ph/Fixture' for a in (client.get('/api/v3/materials/'+item).json()['content'].get('linked_content') or {}).get('articles',[])))
        with sqlite3.connect(root/'state.sqlite') as db:
            article=db.execute("SELECT i.id FROM publication_links l JOIN items i ON i.id=l.target_publication_id WHERE l.source_publication_id=? AND i.url='https://telegra.ph/Fixture'",(item,)).fetchone()[0]
            job=db.execute("SELECT status,retry_at,error FROM jobs WHERE kind='fetch' AND item=?",(article,)).fetchone()
        assert job[0]=='pending' and job[1]>time.time() and job[2]
        assert 'Useful post' in client.get('/api/v3/materials/'+item).json()['content']['text']
    broken[0]=False
    with TestClient(create_app(root,**options),headers=HEADERS) as client:
        wait_for(client,lambda:'Recovered article' in client.get('/api/v3/materials/'+item).json()['content']['text'])
        with sqlite3.connect(root/'state.sqlite') as db:
            assert db.execute("SELECT status,retry_at FROM jobs WHERE kind='fetch' AND item=?",(article,)).fetchone()==('done',None)
            if 'Missing' in extra:
                assert db.execute("SELECT j.status FROM jobs j JOIN items i ON i.id=j.item WHERE j.kind='fetch' AND i.url='https://telegra.ph/Missing'").fetchone()==('unavailable',)
    assert len(calls)>=2


def test_manual_fetch_retry_is_immediate_after_restart_without_resetting_backoff(tmp_path):
    import sqlite3
    from oko.storage.store import Store
    root=migrated_root(tmp_path)
    def response(request):
        if request.url.host=='api.telegra.ph':
            return httpx.Response(503)
        return httpx.Response(200,text=page([(11,'Useful post <a href="https://telegra.ph/Fixture">article</a>')]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        configure_fast_web(client)
        feed=client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox')).json()['feed_id']
        item=wait_for(client,lambda:(c[0]['id'] if (c:=client.get('/api/v3/cards').json()) else None))
        def failed():
            with sqlite3.connect(root/'state.sqlite') as db:
                return db.execute("SELECT j.retry_delay,j.item FROM jobs j JOIN publication_links l ON l.target_publication_id=j.item WHERE j.kind='fetch' AND l.source_publication_id=? AND j.status='pending' AND j.error IS NOT NULL",(item,)).fetchone()
        delay,article=wait_for(client,failed)
    store=Store(root,lambda _:None)
    assert store.retry_material(item)
    store.db.close()
    store=Store(root,lambda _:None)
    try:
        job=store.next_job(store.clock(),feeds=[feed],include_deletions=False)
        assert job is not None and job.kind=='fetch' and job.item==article
        assert store.db.execute('SELECT retry_delay FROM jobs WHERE id=?',(job.id,)).fetchone()[0]==delay
    finally:
        store.db.close()
