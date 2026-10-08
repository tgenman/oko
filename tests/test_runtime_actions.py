"""Reader actions through the agreed HTTP interface and external-source seam."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
from oko.app import create_app
from oko.rss import SourceError
from tests.test_telegram import Telegram, connect

HEADERS = {'Authorization': 'Bearer test-token'}


def test_viewed_and_transport_change_wait_for_one_cleanup(tmp_path: Path):
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
        client.post('/api/tabs/a/heartbeat')
        client.post('/api/tabs/b/heartbeat')
        feed = connect(client)
        assert downloading.wait(2)
        item = client.get('/api/cards').json()[0]['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            viewed = pool.submit(client.post, '/api/materials/' + item + '/viewed')
            try:
                assert cleaning.wait(2)
                switched = pool.submit(client.patch, '/api/feeds/' + feed['id'], json={'transport': None})
                # Queries remain usable while cleanup is waiting.
                assert client.get('/api/materials/' + item).status_code == 404
                assert client.get('/api/subscriptions').status_code == 200
                time.sleep(.05)
            finally:
                release.set()
            assert viewed.result(timeout=2).status_code == 200
            assert switched.result(timeout=2).status_code == 200
        assert client.get('/api/subscriptions').json()['feeds'][0]['transport'] is None
        assert client.get('/api/cards').json() == []
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
        client.post('/api/tabs/a/heartbeat')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/telegram/connect', json=dict(account='fixture', peer='@fixture', folder='Новости', auto_read=False))
            try:
                assert connecting.wait(2)
                started = time.monotonic()
                assert client.post('/api/stop').status_code == 200
                assert time.monotonic()-started < .8
            finally:
                release.set()
            assert pending.result(timeout=2).status_code == 409
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        assert client.get('/api/subscriptions').json()['feeds'] == []


def test_repeated_close_does_not_extend_shutdown_grace(tmp_path: Path):
    from tests.test_lifecycle import Clock
    clock = Clock()
    with TestClient(create_app(tmp_path, token='test-token', clock=clock), headers=HEADERS) as client:
        client.post('/api/tabs/a/heartbeat')
        client.post('/api/tabs/a/close')
        clock.now += 10
        client.post('/api/tabs/a/close')
        clock.now += 5
        for _ in range(100):
            if client.get('/api/status').json()['stopped']:
                break
            time.sleep(.01)
        assert client.get('/api/status').json()['stopped'] is True
