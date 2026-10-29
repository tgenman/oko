from tests.support.cards import read_all_cards
import time
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS, Clock, setup_reader, wait_for
from tests.support.codex import CodexService, configure_codex
from oko.collection.demo import DemoSource


def test_forbidden_processing_waits_for_settings_without_errors_or_timed_retry(tmp_path):
    clock = Clock()
    model = CodexService()
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', api_only=True, clock=clock, codex_client=model), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        configure_codex(client)
        clock.now+=1
        items = [card['id'] for card in read_all_cards(client, '/api/v3/cards')]

        def blocked_materials():
            materials = [client.get('/api/v3/publication/'+identity).json() for identity in items]
            return materials if all('Запрещена публичная' in (m['theses']['error'] or '') for m in materials) else None

        # The counter below covers every Demo publication, not just the first.
        blocked = wait_for(client, blocked_materials)
        assert all(m['theses']['state'] == 'error' for m in blocked)
        assert client.get('/api/v3/status/details').json()['diagnostic_count'] == 0
        waiting = [w for w in client.get('/api/v3/status/details').json()['work'] if w.get('reason') == 'public_ai_not_allowed']
        assert waiting and all(w.get('retry_at') is None for w in waiting)
        assert not any(p['code'] == 'public_ai_not_allowed' for p in client.get('/api/v3/status/details').json()['problems']['current'])
        from tests.support.storage import database
        with database(tmp_path) as db:
            before = db.execute("SELECT sum(attempt) FROM jobs WHERE kind='theses'").fetchone()[0]
            clock.now += 1000
            time.sleep(.15)
            assert db.execute("SELECT sum(attempt) FROM jobs WHERE kind='theses'").fetchone()[0] == before
        assert not model.inputs
        client.patch('/api/v3/feeds/demo', json={'public_ai': True})
        clock.now+=1
        wait_for(client, lambda: client.get('/api/v3/publication/'+item).json()['theses']['result'])
        assert model.inputs
