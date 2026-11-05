"""Persistent work power, distinct from terminating the reader process."""
from tests.support.cards import read_all_cards
from fastapi.testclient import TestClient

from tests.support.synthetic_source import SyntheticSource
from tests.support.app import create_app
from tests.support.reader import setup_reader

HEADERS = {'Authorization': 'Bearer test'}


def test_disabled_oko_keeps_reading_and_local_actions_and_survives_restart(tmp_path):
    exits = []
    options = dict(token='test', source=SyntheticSource(), shutdown=lambda: exits.append(True))
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
        assert client.get('/api/v3/publication/' + item).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters': 'later'}).status_code == 200
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        assert client.get('/api/v3/publication/' + item).status_code == 404
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        assert client.get('/api/v3/status').json()['runtime_state'] == 'running'


def test_disabled_oko_rejects_every_external_command_but_keeps_local_settings(tmp_path):
    with TestClient(create_app(tmp_path, token='test', source=SyntheticSource()), headers=HEADERS) as client:
        item = setup_reader(client, theses=False)
        config = {'filter': {'model': 'fixture', 'reasoning_effort': 'low'}, 'theses': {'model': 'fixture', 'reasoning_effort': 'low'}}
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        commands = [
            ('POST', '/api/v3/collect', None),
            ('POST', '/api/v3/collect', {'scope': 'feed', 'feed_id': 'fixture'}),
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
            ('POST', '/api/v3/telegram/connect', {'source_locator': '@fixture', 'access': {'kind': 'web'}, 'folder': 'News'}),
            ('POST', '/api/v3/feeds/fixture/history', {'mode': 'count', 'count': '10'}),
            ('PATCH', '/api/v3/feeds/fixture/history', {'paused': False}),
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
    from oko.collection.contracts import DiscoveryResult
    from tests.support.legacy_source import FetchResult, Publication
    from tests.support.synthetic_material import material as Material
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
        assert all(client.get('/api/v3/publication/' + card['id']).json()['availability'] != 'available' for card in cards)
        assert 'Must never be accepted' not in client.get('/api/v3/publication/' + cards[0]['id']).text
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        client.post('/api/v3/collect')  # Explicit retry advances the interrupted fetch deadline.
        # Card and publication are separate HTTP snapshots: the fetch may finish
        # between them. Assert title and readiness from the same publication.
        ready = wait_for(client, lambda: [publication for c in read_all_cards(client, '/api/v3/cards')
            if (publication := client.get('/api/v3/publication/' + c['id']).json())['availability'] == 'available'])
        assert ready[0]['title'] == 'Fresh result'
        assert ready[0]['text'] == 'Resumed successfully'






def test_web_connection_does_not_start_preview_when_power_is_off(tmp_path):
    import httpx
    previews = []
    def preview(request):
        previews.append(str(request.url))
        return httpx.Response(503)
    app=create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(preview))
    with TestClient(app,headers=HEADERS) as client:
        assert client.put('/api/v3/power',json={'enabled':False}).status_code==200
        result=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'News'})
        assert result.status_code==409
        assert previews == []
        assert client.get('/api/v3/subscriptions').json()['feeds'] == []
