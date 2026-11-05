"""Reader actions through the agreed HTTP interface and external-source seam."""
from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from fastapi.testclient import TestClient
from tests.support.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}




def test_stop_does_not_wait_for_connect_and_rejects_late_metadata(tmp_path: Path):
    import httpx
    from tests.support.telegram_web import page
    connecting, release = threading.Event(), threading.Event()

    async def preview(request):
        connecting.set()
        while not release.is_set():
            try:
                await asyncio.sleep(.005)
            except asyncio.CancelledError:
                continue
        return httpx.Response(200, text=page([(11, 'Public post')]))

    kwargs = dict(token='test-token', rss_transport=httpx.MockTransport(preview))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'a')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Новости'))
            try:
                assert connecting.wait(2)
                started = time.monotonic()
                assert client.post('/api/v3/stop').status_code == 200
                assert time.monotonic()-started < .8
            finally:
                release.set()
            assert pending.result(timeout=2).status_code != 200
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []


def test_retired_close_cannot_stop_background_work(tmp_path: Path):
    from tests.support.reader import Clock
    clock = Clock()
    with TestClient(create_app(tmp_path, token='test-token', clock=clock), headers=HEADERS) as client:
        assert close_presence(client, 'a').status_code == 409
        clock.now += 100
        assert close_presence(client, 'a').status_code == 409
        assert client.get('/api/v3/status').json()['runtime_state'] == 'running'


def test_reload_restores_missing_local_media_without_stopping_reader(tmp_path: Path):
    import httpx
    from tests.support.telegram import settled, configure_fast_web
    from tests.support.telegram_web import page
    from tests.support.reader import wait_for
    from tests.support.storage import database

    photo = "<a class=\"tgme_widget_message_photo_wrap\" style=\"background-image:url('https://cdn.example/photo.jpg')\"></a>"
    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, content=b'media', headers={'content-type':'image/jpeg'})
        return httpx.Response(200,text=page([(11,'Album'+photo)]))
    with TestClient(create_app(tmp_path,token='test-token',rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        presence(client, 'a')
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'News'}).status_code == 200
        card = settled(client)[0]
        with database(tmp_path) as db:
            storage_key=db.execute("SELECT storage_key FROM media_files WHERE publication_id = %s AND status = 'ready'",(card['id'],)).fetchone()[0]
        (tmp_path/storage_key).unlink()
        client.post('/api/v3/materials/' + card['id'] + '/retry')
        from tests.support.publications import loading
        value=wait_for(client,lambda: value if (value := client.get('/api/v3/publication/'+card['id']).json()) and any(media['url'] is not None for media in value['media']) and not loading(value) else None)
        assert client.get('/api/v3/status').json()['runtime_state']=='running'
        assert client.get([media for media in value['media'] if media['url'] is not None][0]['url']).content==b'media'
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
