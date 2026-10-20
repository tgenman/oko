"""Owned external checks through HTTP, including adapters ignoring cancellation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from oko.collection.contracts import SourceError
from tests.support.app import create_app
from tests.support.codex import CodexService
from tests.support.feed_url import RSS
from tests.support.telegram import Telegram
from tests.support.telegram_web import page
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


@pytest.mark.parametrize('kind', [
    'rss', 'llamacpp', 'codex_check', 'codex_catalog', 'codex_save',
    'telegram_web', 'telegram_profile', 'telegram_connect',
])
def test_late_external_reply_is_rejected_even_after_oko_is_enabled_again(tmp_path, kind):
    held = HeldReply()

    class Codex(CodexService):
        async def check(self):
            if kind == 'codex_check':
                await held.wait()
            return await super().check()

        async def catalog(self):
            if kind in ('codex_catalog', 'codex_save'):
                await held.wait()
            return await super().catalog()

    class Client(Telegram):
        async def identity(self, account):
            if kind == 'telegram_profile':
                await held.wait()
            return 123

        async def info(self, account, peer):
            if kind == 'telegram_connect':
                await held.wait()
            return await super().info(account, peer)

    async def response(request):
        await held.wait()
        if kind == 'llamacpp':
            return httpx.Response(200, json={'data': [{'id': 'fixture'}]} if request.url.path == '/v1/models' else {})
        return httpx.Response(200, text=page([(11, 'Late post')]) if kind == 'telegram_web' else RSS)

    transport = httpx.MockTransport(response)
    with TestClient(create_app(tmp_path, token='test', api_only=True, codex_client=Codex(),
                               telegram_client=Client(), rss_transport=transport, ai_transport=transport), headers=HEADERS) as client:
        if kind == 'llamacpp':
            assert client.put('/api/v3/processors/llamacpp', json={
                'endpoint': 'http://127.0.0.1:8080', 'model': 'fixture',
            }).status_code == 200
        if kind == 'telegram_connect':
            assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': 'fixture'}).status_code == 200
        choices = {name: {'model': 'synthetic', 'reasoning_effort': 'low', 'fast': False} for name in ('filter', 'theses')}
        method, path, body = {
            'rss': ('POST', '/api/v3/feeds/verify', {'url': 'https://synthetic.test/rss'}),
            'llamacpp': ('POST', '/api/v3/processors/llamacpp/check', None),
            'codex_check': ('POST', '/api/v3/processors/codex/check', None),
            'codex_catalog': ('GET', '/api/v3/processors/codex/models', None),
            'codex_save': ('PUT', '/api/v3/processors/codex', choices),
            'telegram_web': ('POST', '/api/v3/telegram/connect', {'source_locator': '@fixture', 'folder': 'News', 'access': {'kind': 'web'}}),
            'telegram_profile': ('PUT', '/api/v3/telegram/profiles/personal', {'configuration_ref': 'fixture'}),
            'telegram_connect': ('POST', '/api/v3/telegram/connect', {'source_locator': '@fixture', 'folder': 'News', 'access': {'kind': 'client', 'profile': 'personal'}}),
        }[kind]
        held.enabled = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.request, method, path, **({'json': body} if body is not None else {}))
            try:
                assert held.started.wait(2)
                before_codex = client.get('/api/v3/processors/codex').json()
                before_profiles = client.get('/api/v3/telegram/profiles').json()
                assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
                assert held.cancelled.wait(2)
                # _live() alone is insufficient: an old reply cannot become
                # eligible again when the user starts Oko before it arrives.
                assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            finally:
                held.release.set()
            result = pending.result(timeout=3)
            # Preserve the pre-refactor statuses and subject-specific codes;
            # the strict client also validates the now-complete Codex schema.
            assert result.status_code == (400 if kind == 'rss' else 503), result.text
            assert result.json()['code'] == (
                'source_connection_invalid' if kind == 'rss' else
                'processor_unavailable' if kind in ('llamacpp', 'codex_check', 'codex_catalog', 'codex_save') else
                'profile_unavailable' if kind == 'telegram_profile' else 'source_unavailable'
            )
        assert client.get('/api/v3/subscriptions').json() == {'folders': [], 'feeds': []}
        assert client.get('/api/v3/processors/codex').json() == before_codex
        assert client.get('/api/v3/telegram/profiles').json() == before_profiles


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
