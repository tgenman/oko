"""Publication text and individual media jobs through the real reader HTTP."""
from tests.support.publications import body_text,reasons
from tests.support.cards import read_all_cards
import asyncio
import threading

import httpx
import pytest

from tests.support.publication_pipeline import publication_root
from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient
from tests.support.reader import wait_for

HEADERS = {'Authorization':'Bearer test'}


def test_partial_text_is_readable_while_new_photo_download_is_held(tmp_path):
    root = publication_root(tmp_path)
    phase = [0]
    import time
    from tests.support.storage import database
    offset=[0.0]
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
    with TestClient(create_app(root, token='test', api_only=True, rss_transport=httpx.MockTransport(response), clock=lambda:time.monotonic()+offset[0], wall_clock=lambda:time.time()+offset[0]), headers=HEADERS) as client:
        try:
            configure_fast_web(client)
            client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
            item = settled(client)[0]['id']
            old = client.get('/api/v3/publication/'+item).json()
            assert client.get([media for media in old['media'] if media['url'] is not None][0]['url']).content == b'old-photo'
            with database(root) as db:
                previous=db.execute("SELECT j.id,m.storage_key FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.status='ready'",(item,)).fetchone()
                assert previous
                previous_job,previous_key=previous['id'],previous['storage_key']
            phase[0] = 1
            with database(root) as db:
                db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
            client.post('/api/v3/materials/'+item+'/retry')
            assert started.wait(3)
            updated = wait_for(client, lambda: (m if body_text(m := client.get('/api/v3/publication/'+item).json())=='Edited text' else None))
            assert any(media['status']=='pending_download' and media['url'] is None for media in updated['media'])
            assert [media for media in updated['media'] if media['url'] is not None] == []
            assert any(job['operation']=='download_media' and job['status']=='running' for job in updated['jobs'])
            release.set()
            complete = wait_for(client, lambda: (m if (m := client.get('/api/v3/publication/'+item).json()) and any(media['url'] is not None for media in m['media']) else None))
            assert client.get([media for media in complete['media'] if media['url'] is not None][0]['url']).content == b'new-photo'
            assert all(media['status']=='ready' for media in complete['media'])
            assert [media for media in complete['media'] if media['url'] is not None][0]['url'] != [media for media in old['media'] if media['url'] is not None][0]['url']
            assert client.get([media for media in old['media'] if media['url'] is not None][0]['url']).status_code == 404
            assert 'previous_versions' not in complete
            with database(root) as db:
                assert db.execute('SELECT id FROM jobs WHERE id=%s',(previous_job,)).fetchone() is not None
            assert (root/previous_key).exists()
            offset[0]+=3601
            def cleaned():
                with database(root) as db:
                    return db.execute('SELECT id FROM jobs WHERE id=%s',(previous_job,)).fetchone() is None
            wait_for(client,cleaned)
            assert not (root/previous_key).exists()
        finally:
            release.set()


def test_viewed_cancels_a_media_job_and_rejects_its_late_file(tmp_path):
    root = publication_root(tmp_path)
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
            cards = read_all_cards(client, '/api/v3/cards')
            assert len(cards)==1
            item = cards[0]['id']
            assert client.get('/api/v3/status/details').status_code == 200
            assert client.post('/api/v3/materials/'+item+'/viewed').status_code == 200
            release.set()
            wait_for(client, lambda: client.get('/api/v3/status/details').json()['work']==[{'phase':'idle'}])
            assert client.get('/api/v3/publication/'+item).status_code == 404
            assert not any(b'late-private-photo' in path.read_bytes() for path in root.rglob('*') if path.is_file())
        finally:
            release.set()


def test_one_file_failure_keeps_other_files_and_backoff_survives_restart(tmp_path):
    import time
    root = publication_root(tmp_path)
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
        item = wait_for(client, lambda: (cards[0]['id'] if (cards := read_all_cards(client, '/api/v3/cards')) else None))
        failed = wait_for(client, lambda: (m if (m := client.get('/api/v3/publication/'+item).json()) and sum(media['url'] is not None for media in m['media'])==1 and any('Не удалось загрузить вложение' in reason for reason in reasons(m)) else None))
        assert body_text(failed)=='Readable text'
        from tests.support.storage import database
        with database(root) as db:
            result=db.execute("""SELECT j.reason_message FROM media_files m
                JOIN jobs j ON j.kind='download_media' AND j.item=m.id
                WHERE m.publication_id=%s AND m.source_locator LIKE '%%/bad.jpg'""",(item,)).fetchone()
            assert result and result['reason_message']
            raw=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            assert '_collection_reason' not in raw['extra']
            assert '_reader' not in raw['extra']
            db.execute("UPDATE jobs SET reason_code=NULL,reason_message=NULL WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND source_locator LIKE '%%/bad.jpg')",(item,))
        assert any(job['operation']=='download_media' and job['status']=='pending' and job['reason_message'] is None for job in client.get('/api/v3/publication/'+item).json()['jobs'])
        with database(root) as db:
            db.execute("UPDATE jobs SET reason_message=%s WHERE kind='download_media' AND item=(SELECT id FROM media_files WHERE publication_id=%s AND source_locator LIKE '%%/bad.jpg')",(result['reason_message'],item))
        assert calls == {'bad':1, 'good':1}
        problems = client.get('/api/v3/status/details').json()['problems']['current']
        assert all(affected['material_id']==item for problem in problems for affected in problem['affected'] if affected['kind']=='material')
    with TestClient(create_app(root, **options), headers=HEADERS) as client:
        assert body_text(client.get('/api/v3/publication/'+item).json())=='Readable text'
        assert calls == {'bad':1, 'good':1}
        offset[0] += 61
        wait_for(client, lambda: calls['bad']==2)
        # The provider call starts before its failure is committed. Keep the
        # clock fixed until the doubled delay is visible through HTTP.
        from datetime import datetime
        def doubled_delay():
            return any(datetime.fromisoformat(w['retry_at'].replace('Z','+00:00')).timestamp()>time.time()+offset[0]+119
                for w in client.get('/api/v3/status/details').json()['work'] if w['phase']=='waiting' and w.get('retry_at'))
        wait_for(client,doubled_delay)
        assert calls['good']==1
        offset[0] += 61
        assert any(media['status']!='ready' for media in client.get('/api/v3/publication/'+item).json()['media'])
        assert calls['bad']==2
        offset[0] += 60
        def all_media_ready():
            value=client.get('/api/v3/publication/'+item).json()
            return value if all(media['status']=='ready' for media in value['media']) else None
        loaded = wait_for(client,all_media_ready)
        assert len([media for media in loaded['media'] if media['url'] is not None])==2
        assert calls == {'bad':3, 'good':1}


def test_permanent_file_failure_waits_for_manual_retry(tmp_path):
    import time
    root = publication_root(tmp_path)
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
        item = wait_for(client,lambda:(cards[0]['id'] if (cards:=read_all_cards(client, '/api/v3/cards')) else None))
        wait_for(client,lambda:any('Не удалось загрузить вложение' in reason for reason in reasons(client.get('/api/v3/publication/'+item).json())))
        assert len(calls)==1
        problems = client.get('/api/v3/status/details').json()['problems']['current']
        assert any({'kind':'material','material_id':item} in problem['affected'] for problem in problems)
        available[0]=True
        offset[0]+=600
        assert [media for media in client.get('/api/v3/publication/'+item).json()['media'] if media['url'] is not None]==[]
        assert len(calls)==1
        client.post('/api/v3/materials/'+item+'/retry')
        ready = wait_for(client,lambda:(m if (m := client.get('/api/v3/publication/'+item).json()) and any(media['url'] is not None for media in m['media']) else None))
        assert client.get([media for media in ready['media'] if media['url'] is not None][0]['url']).content==b'manually-retried'
        assert len(calls)==2


def test_manual_fetch_retry_preserves_a_source_wait_and_saved_text(tmp_path):
    from datetime import datetime
    root=publication_root(tmp_path)
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
        from tests.support.storage import database
        with database(root) as db:
            db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
        client.post('/api/v3/materials/'+item+'/retry')
        def deadline():
            state=client.get('/api/v3/status/details').json()
            dates=[datetime.fromisoformat(w['retry_at'].replace('Z','+00:00')).timestamp() for w in state['work'] if w['phase']=='waiting' and w.get('retry_at')]
            return max(dates) if dates else None
        before=wait_for(client,deadline)
        assert len(denied)==1
        client.post('/api/v3/materials/'+item+'/retry')
        assert deadline() is not None and deadline()>=before-.1
        assert body_text(client.get('/api/v3/publication/'+item).json())=='Saved text'
        assert len(denied)==1


@pytest.mark.parametrize('point,committed',[('after_media_move',False),('before_media_commit',False),('after_media_commit',True)])
def test_process_death_at_media_acceptance_never_exposes_uncommitted_bytes(tmp_path,point,committed):
    from tests.support.storage import database
    from tests.support.processes import launch,connect,cleanup
    root=publication_root(tmp_path)
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
        item=read_all_cards(client, '/api/v3/cards')[0]['id']
    with database(root) as db:
        media_id=db.execute('SELECT id FROM media_files WHERE publication_id = %s',(item,)).fetchone()[0]
    url='/api/v3/materials/'+item+'/media/'+media_id
    process=launch(root,point,api_only=True,web_media=True)
    try:
        try:
            with connect(root,process) as client:
                try:
                    client.post('/api/v3/materials/'+item+'/retry')
                except httpx.TransportError:
                    pass
        except AssertionError:
            # Current pending media can resume and reach the fault before readiness.
            assert process.wait(timeout=5)==79
        assert process.wait(timeout=5)==79
    finally:
        cleanup(process)
    if committed:
        with database(root) as db:
            attempt = db.execute("SELECT collection_attempt FROM jobs WHERE kind='download_media' AND item=%s", (media_id,)).fetchone()
            assert attempt is not None and attempt[0] is None
    def available(request):
        if request.url.host=='cdn.example':
            return httpx.Response(200,content=b'process-media',headers={'content-type':'image/jpeg'})
        photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
        return httpx.Response(200,text=page([(11,'Process text'+photo)]))
    with TestClient(create_app(root,token='test',api_only=True,rss_transport=httpx.MockTransport(available)),headers=HEADERS) as client:
        assert client.get(url).status_code==(200 if committed else 404)
        if not committed:
            client.post('/api/v3/materials/'+item+'/retry')
        ready=wait_for(client,lambda:(m if (m := client.get('/api/v3/publication/'+item).json()) and any(media['url'] is not None for media in m['media']) else None))
        assert client.get([media for media in ready['media'] if media['url'] is not None][0]['url']).content==b'process-media'


def test_cleanup_preserves_active_rebuild_staging(tmp_path,postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        staging=tmp_path/'staging'/'publication'/'rebuild-1-1'
        staging.mkdir(parents=True)
        (staging/'partial').write_bytes(b'partial')
        store.media.active_paths.add(staging)
        store.media.cleanup()
        assert (staging/'partial').exists()
        store.media.active_paths.remove(staging)
        store.media.cleanup()
        assert not staging.exists()
    finally:
        store.db.close()
