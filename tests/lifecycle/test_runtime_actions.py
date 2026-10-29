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
from oko.collection.contracts import SourceError
from tests.support.telegram import Telegram, connect, configure

HEADERS = {'Authorization': 'Bearer test-token'}


def test_viewed_and_feed_deletion_wait_for_one_cleanup(tmp_path: Path):
    downloading, cleaning, release = threading.Event(), threading.Event(), threading.Event()
    class Client(Telegram):
        closing = False
        rows = [dict(id=11, date=time.time()-60, text='Видео', group=None,
                     media=dict(kind='video', mime='video/mp4', extension='mp4'))]
        async def page(self, account, peer, after, since):
            return dict(messages=self.rows, more=False)
        async def messages(self, account, peer, ids):
            return self.rows
        async def download(self, account, peer, id):
            downloading.set()
            await asyncio.sleep(60)
        async def close(self):
            if not downloading.is_set() or release.is_set():
                return
            if self.closing:
                raise SourceError('Очистка уже выполняется')
            self.closing = True
            cleaning.set()
            while not release.is_set():
                await asyncio.sleep(.005)
            self.closing = False
    app = create_app(tmp_path, token='test-token', telegram_client=Client())
    with TestClient(app, headers=HEADERS, raise_server_exceptions=False) as client:
        presence(client, 'a')
        presence(client, 'b')
        feed = connect(client)
        assert downloading.wait(2)
        item = read_all_cards(client, '/api/v3/cards')[0]['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            viewed = pool.submit(client.post, '/api/v3/materials/' + item + '/viewed')
            try:
                assert cleaning.wait(2)
                switched = pool.submit(client.delete, '/api/v3/feeds/' + feed['id'])
                # Queries remain usable while cleanup is waiting.
                assert client.get('/api/v3/publication/' + item).status_code == 404
                assert client.get('/api/v3/subscriptions').status_code == 200
                time.sleep(.05)
            finally:
                release.set()
            assert viewed.result(timeout=2).status_code == 200
            assert switched.result(timeout=2).status_code == 200
        for _ in range(100):
            if not client.get('/api/v3/subscriptions').json()['feeds']: break
            time.sleep(.02)
        assert not client.get('/api/v3/subscriptions').json()['feeds']
        assert read_all_cards(client, '/api/v3/cards') == []
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_stop_does_not_wait_for_connect_and_rejects_late_metadata(tmp_path: Path):
    connecting, release = threading.Event(), threading.Event()
    class Client(Telegram):
        async def info(self, account, peer):
            connecting.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    continue  # External client replies after cancellation.
            return await super().info(account, peer)
    kwargs = dict(token='test-token', telegram_client=Client())
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'a')
        configure(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/v3/telegram/connect', json=dict(source_locator='@fixture', access={'kind':'client','profile':'personal'},folder='Новости',auto_read=False))
            try:
                assert connecting.wait(2)
                started = time.monotonic()
                assert client.post('/api/v3/stop').status_code == 200
                assert time.monotonic()-started < .8
            finally:
                release.set()
            assert pending.result(timeout=2).status_code == 409
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []


def test_repeated_close_does_not_extend_shutdown_grace(tmp_path: Path):
    from tests.support.reader import Clock
    clock = Clock()
    with TestClient(create_app(tmp_path, token='test-token', clock=clock), headers=HEADERS) as client:
        presence(client, 'a')
        close_presence(client, 'a')
        clock.now += 10
        close_presence(client, 'a')
        clock.now += 5
        for _ in range(100):
            if client.get('/api/v3/status').json()['runtime_state'] == 'stopped':
                break
            time.sleep(.01)
        assert client.get('/api/v3/status').json()['runtime_state'] == 'stopped'


def test_reload_restores_missing_local_media_without_stopping_reader(tmp_path: Path):
    from tests.support.telegram import settled
    class Client(Telegram):
        rows = [dict(id=11, date=time.time()-60, text='Альбом', group=777,
                     media=dict(kind='image', mime='image/jpeg', extension='jpg'))]
        async def page(self, account, peer, after, since):
            return dict(messages=self.rows, more=False)
        async def messages(self, account, peer, ids):
            return self.rows
    source = Client()
    with TestClient(create_app(tmp_path, token='test-token', telegram_client=source), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        connect(client)
        card = settled(client)[0]
        # External loss of a stored file must not turn a preparation failure into
        # an unhandled task exception which closes the whole reader.
        from tests.support.storage import database
        with database(tmp_path) as db:
            storage_key=db.execute("SELECT storage_key FROM media_files WHERE publication_id = %s AND status = 'ready'",(card['id'],)).fetchone()[0]
        (tmp_path/storage_key).unlink()
        source.rows = source.rows + [dict(id=12, date=time.time()-60, text='', group=777, media=None)]
        client.post('/api/v3/materials/' + card['id'] + '/retry')
        from tests.support.reader import wait_for
        value=wait_for(client,lambda: value if (value := client.get('/api/v3/publication/'+card['id']).json()) and any(media['role'] == 'attachment' for media in value['media']) and not value['loading'] else None)
        assert client.get('/api/v3/status').json()['runtime_state']=='running'
        assert client.get([media for media in value['media'] if media['role'] == 'attachment'][0]['url']).content==b'media'
        assert client.get('/api/v3/cards').status_code == 200
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
