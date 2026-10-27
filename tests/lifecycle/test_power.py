"""Persistent work power, distinct from terminating the reader process."""
from tests.support.cards import read_all_cards
from fastapi.testclient import TestClient

from oko.collection.demo import DemoSource
from tests.support.app import create_app
from tests.support.reader import setup_reader

HEADERS = {'Authorization': 'Bearer test'}


def test_disabled_oko_keeps_reading_and_local_actions_and_survives_restart(tmp_path):
    exits = []
    options = dict(token='test', source=DemoSource(), shutdown=lambda: exits.append(True))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        cards = read_all_cards(client, '/api/v3/cards')
        response = client.put('/api/v3/power', json={'enabled': False})
        assert response.status_code == 200, response.text
        state = client.get('/api/v3/status').json()
        assert state['runtime_state'] == 'disabled'
        assert state['work_allowed'] is False
        assert exits == []
        assert read_all_cards(client, '/api/v3/cards') == cards
        assert client.get('/api/v3/materials/' + item).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters': 'later'}).status_code == 200
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        assert client.get('/api/v3/materials/' + item).status_code == 404
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        assert client.get('/api/v3/status').json()['runtime_state'] == 'running'


def test_disabled_oko_rejects_every_external_command_but_keeps_local_settings(tmp_path):
    with TestClient(create_app(tmp_path, token='test', source=DemoSource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        config = {'filter': {'model': 'fixture', 'reasoning_effort': 'low'}, 'theses': {'model': 'fixture', 'reasoning_effort': 'low'}}
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        commands = [
            ('POST', '/api/v3/collect', None),
            ('POST', '/api/v3/collect', {'scope': 'feed', 'feed_id': 'demo'}),
            ('POST', f'/api/v3/materials/{item}/retry', None),
            ('POST', f'/api/v3/materials/{item}/theses/retry', None),
            ('POST', f'/api/v3/materials/{item}/filter/retry', None),
            ('POST', f'/api/v3/materials/{item}/regenerate', None),
            ('POST', '/api/v3/feeds/verify', {'url': 'https://synthetic.test/rss'}),
            ('POST', '/api/v3/feeds', {'url': 'https://synthetic.test/rss', 'verification': 'old', 'title': 'Feed', 'folder': 'News'}),
            ('GET', '/api/v3/processors/codex/models', None),
            ('PUT', '/api/v3/processors/codex', config),
            ('POST', '/api/v3/processors/codex/check', None),
            ('POST', '/api/v3/processors/llamacpp/check', None),
            ('PUT', '/api/v3/telegram/profiles/personal', {'configuration_ref': 'synthetic'}),
            ('POST', '/api/v3/telegram/connect', {'source_locator': '@fixture', 'access': {'kind': 'web'}, 'folder': 'News'}),
            ('POST', '/api/v3/feeds/demo/history', {'mode': 'count', 'count': '10'}),
            ('PATCH', '/api/v3/feeds/demo/history', {'paused': False}),
        ]
        for method, path, body in commands:
            response = client.request(method, path, **({'json': body} if body is not None else {}))
            assert response.status_code == 409, (path, response.text)
            assert response.json()['code'] == 'runtime_disabled', (path, response.text)
        assert client.patch('/api/v3/settings/oko/global', json={'theses': True}).status_code == 200
        assert client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'fixture', 'local_execution': True}).status_code == 200


def test_power_rejects_late_download_and_resumes_interrupted_material(tmp_path):
    import asyncio
    import threading
    import time
    from oko.collection.contracts import DiscoveryResult, FetchResult, Material, Publication
    from tests.support.presence import presence
    from tests.support.reader import wait_for

    started, release = threading.Event(), threading.Event()

    class Source:
        changes = None
        attempts = 0

        async def discover(self, cursor):
            return DiscoveryResult((Publication('article', 'https://synthetic.test/article', time.time()),), 'done')

        async def fetch(self, publication):
            self.attempts += 1
            if self.attempts == 1:
                started.set()
                while not release.is_set():
                    try:
                        await asyncio.sleep(.005)
                    except asyncio.CancelledError:
                        continue
                return FetchResult(Material('Late result', 'Must never be accepted'))
            return FetchResult(Material('Fresh result', 'Resumed successfully'))

    source = Source()
    with TestClient(create_app(tmp_path, token='test', source=source), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
        presence(client, 'reader')
        try:
            assert started.wait(2)
            started_at = time.monotonic()
            assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
            assert time.monotonic() - started_at < 1
        finally:
            release.set()
        time.sleep(.08)
        cards = read_all_cards(client, '/api/v3/cards')
        assert source.attempts == 1
        assert all(client.get('/api/v3/materials/' + card['id']).json()['availability'] != 'available' for card in cards)
        assert 'Must never be accepted' not in client.get('/api/v3/materials/' + cards[0]['id']).text
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        client.post('/api/v3/collect')  # Explicit retry advances the interrupted fetch deadline.
        ready = wait_for(client, lambda: [c for c in read_all_cards(client, '/api/v3/cards') if client.get('/api/v3/materials/' + c['id']).json()['availability'] == 'available'])
        assert ready[0]['title'] == 'Fresh result'


def test_viewed_while_disabled_defers_telegram_acknowledgement_until_start(tmp_path):
    import time
    from tests.support.presence import presence
    from tests.support.reader import wait_for
    from tests.support.telegram import Telegram, connect, settled

    class Client(Telegram):
        acknowledgements: list[int] = []

        async def acknowledge(self, account, peer, max_id):
            self.acknowledgements.append(max_id)

    telegram = Client()
    with TestClient(create_app(tmp_path, token='test', telegram_client=telegram), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
        presence(client, 'reader')
        connect(client)
        item = settled(client)[0]['id']
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        assert read_all_cards(client, '/api/v3/cards') == []
        time.sleep(.08)
        assert telegram.acknowledgements == []
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        assert wait_for(client, lambda: telegram.acknowledgements) == [11]


def test_disabling_during_telegram_identity_check_keeps_verified_profile(tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from tests.support.telegram import Telegram, configure

    checking = threading.Event()

    class Client(Telegram):
        hold = False

        async def identity(self, account):
            if self.hold:
                checking.set()
                await asyncio.sleep(60)
            return await super().identity(account)

    telegram = Client()
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=telegram), headers=HEADERS) as client:
        assert configure(client).status_code == 200
        before = client.get('/api/v3/telegram/profiles').json()['personal']
        telegram.hold = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(client.post, '/api/v3/telegram/connect', json={
                'source_locator': '@fixture', 'folder': 'News', 'access': {'kind': 'client', 'profile': 'personal'},
            })
            assert checking.wait(2)
            assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
            assert request.result(timeout=2).status_code != 200
        assert client.get('/api/v3/telegram/profiles').json()['personal'] == before
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []


def test_queued_web_connection_cannot_start_preview_after_power_off(tmp_path):
    import asyncio
    import httpx
    from tests.support.telegram import Telegram

    entered, release = asyncio.Event(), asyncio.Event()
    previews = []

    class Client(Telegram):
        hold_close = True

        async def close(self):
            if self.hold_close:
                self.hold_close = False
                entered.set()
                await release.wait()

    async def preview(request):
        previews.append(str(request.url))
        return httpx.Response(503)

    app = create_app(tmp_path, token='test', api_only=True, telegram_client=Client(), rss_transport=httpx.MockTransport(preview))
    with TestClient(app, headers=HEADERS) as client:
        async def queued_actions():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', headers=HEADERS) as http:
                configuration = asyncio.create_task(http.put('/api/v3/processors/llamacpp', json={
                    'endpoint': 'http://127.0.0.1:8080', 'model': 'fixture', 'local_execution': True,
                }))
                await entered.wait()
                connect = asyncio.create_task(http.post('/api/v3/telegram/connect', json={
                    'source_locator': '@fixture', 'folder': 'News', 'access': {'kind': 'web'},
                }))
                await asyncio.sleep(.02)
                power = asyncio.create_task(http.put('/api/v3/power', json={'enabled': False}))
                await asyncio.sleep(.02)
                release.set()
                configured, connected, disabled = await asyncio.gather(configuration, connect, power)
                assert configured.status_code == disabled.status_code == 200
                assert connected.status_code != 200
        client.portal.call(queued_actions)
        assert previews == []
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []
