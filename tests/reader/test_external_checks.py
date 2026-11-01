"""Owned external checks through HTTP, including adapters ignoring cancellation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from oko.collection.contracts import SourceError
from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


class HeldReply:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.enabled = False

    async def wait(self):
        if not self.enabled:
            return
        self.started.set()
        while not self.release.is_set():
            try:
                await asyncio.sleep(.005)
            except asyncio.CancelledError:
                self.cancelled.set()


@pytest.mark.parametrize('interrupted', [False, True])
def test_failed_identity_check_changes_profile_only_in_its_original_context(tmp_path, interrupted):
    held = HeldReply()

    class Client(Telegram):
        async def identity(self, account):
            if held.enabled:
                await held.wait()
                raise SourceError('Synthetic identity failure')
            return 123

    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Client()), headers=HEADERS) as client:
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'fixture'}).status_code == 200
        before = client.get('/api/v3/telegram/profiles').json()['personal']
        held.enabled = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/v3/telegram/connect', json={
                'source_locator': '@fixture', 'folder': 'News', 'access': {'kind': 'client', 'profile': 'personal'},
            })
            try:
                assert held.started.wait(2)
                if interrupted:
                    assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
                    assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            finally:
                held.release.set()
            assert pending.result(timeout=3).status_code == 503
        after = client.get('/api/v3/telegram/profiles').json()['personal']
        if interrupted:
            assert after == before
        else:
            assert after['state'] == 'unavailable'
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []
