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
        blocked = wait_for(client, lambda: (m if 'Запрещена публичная' in ((m := client.get('/api/v3/materials/'+item).json())['theses']['diagnostic_message'] or '') else None))
        assert blocked['theses']['attempt']['retry_at'] is None
        assert client.get('/api/v3/status').json()['diagnostic_count'] == 0
        assert all(w.get('retry_at') is None for w in client.get('/api/v3/status').json()['work'] if w.get('reason') == 'public_ai_not_allowed')
        assert not any(p['code'] == 'public_ai_not_allowed' for p in client.get('/api/v3/status').json()['problems']['current'])
        import sqlite3
        db = sqlite3.connect(tmp_path/'state.sqlite')
        before = db.execute("SELECT sum(attempt) FROM jobs WHERE kind='theses'").fetchone()[0]
        clock.now += 1000
        time.sleep(.15)
        assert db.execute("SELECT sum(attempt) FROM jobs WHERE kind='theses'").fetchone()[0] == before
        assert not model.inputs
        client.patch('/api/v3/feeds/demo', json={'public_ai': True})
        wait_for(client, lambda: client.get('/api/v3/materials/'+item).json()['theses']['result'])
        assert model.inputs
