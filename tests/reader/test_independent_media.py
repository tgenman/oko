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
