"""Settings reads and writes retain database transaction boundaries."""
import pytest

from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS


@pytest.fixture
def settings_database(request):
    return request.getfixturevalue('postgres_url')


def test_settings_edit_rollback_and_restart_keep_one_effective_value(tmp_path, settings_database):
    armed = False

    def fault(point):
        if armed and point == 'settings_after_values':
            raise RuntimeError('synthetic interrupted settings action')

    def app():
        return create_app(tmp_path, token='test', api_only=True,
                          database_url=settings_database, fault=fault)

    path = '/api/v3/settings/oko/global'
    with TestClient(app(), headers=HEADERS) as client:
        saved = client.patch(path, json={'theses': True, 'prompt_filters': 'Keep useful text'})
        assert saved.status_code == 200, saved.text
        assert saved.json()['effective']['theses']['value'] is True
        armed = True
        failed = client.patch(path, json={'theses': False, 'prompt_filters': 'Discard'})
        assert failed.status_code == 500
        current = client.get(path).json()['effective']
        assert current['theses']['value'] is True
        assert current['prompt_filters']['value'] == 'Keep useful text'
        armed = False
    with TestClient(app(), headers=HEADERS) as client:
        current = client.get(path).json()['effective']
        assert current['theses']['value'] is True
        assert current['prompt_filters']['value'] == 'Keep useful text'
        reset = client.patch(path, json={'theses': None, 'prompt_filters': None})
        assert reset.status_code == 200
        assert reset.json()['effective']['theses']['origin'] == {'scope': 'default'}
    with TestClient(app(), headers=HEADERS) as client:
        assert client.get(path).json()['effective']['theses']['origin'] == {'scope': 'default'}


def test_http_inherited_settings_use_one_consistent_database_snapshot(tmp_path, settings_database):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from tests.support.demo_source import DemoSource

    app = create_app(tmp_path, token='test', api_only=True, source=DemoSource(), database_url=settings_database)
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(settings_database) as writer:
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        folder = client.get('/api/v3/subscriptions').json()['feeds'][0]['folder_id']
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}).status_code == 200
        path = '/api/v3/settings/folder/' + folder
        assert client.patch(path, json={'prompt_filters': 'before'}).status_code == 200
        entered, release = threading.Event(), threading.Event()
        def trace(query):
            if 'SELECT field, value FROM settings' in query and not entered.is_set():
                entered.set()
                assert release.wait(5)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(trace))
        try:
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(client.get, path)
                assert entered.wait(5)
                writer.execute("UPDATE settings SET value='true' WHERE folder_id IS NULL AND feed_id IS NULL AND field='theses'")
                writer.execute("UPDATE settings SET value=%s WHERE folder_id=%s AND feed_id IS NULL AND field='prompt_filters'", ('\"after\"', folder))
                writer.commit()
                release.set()
                result = pending.result(5)
            assert result.status_code == 200, result.text
            assert result.json()['effective']['theses']['value'] is False
            assert result.json()['effective']['prompt_filters']['value'] == 'before'
        finally:
            release.set()
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))


def test_codex_configuration_problem_rolls_back_with_settings(tmp_path, settings_database):
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import Overrides
    from oko.services.subscriptions import SubscriptionService
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        service=SubscriptionService(lambda:store)
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                service.update_settings('oko','global',Overrides(processor='codex',theses=True))
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:codex-choice' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:codex-choice'").fetchone() is None
        with transaction(store.db):
            service.update_settings('oko','global',Overrides(processor='codex',theses=True))
        identity=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:codex-choice' AND finished IS NULL").fetchone()[0]
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=settings_database)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:codex-choice' AND finished IS NULL").fetchone()[0]==identity
        with transaction(store.db):
            service=SubscriptionService(lambda:store)
            service.update_settings('oko','global',Overrides(theses=False))
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id=%s",(identity,)).fetchone()[0]=='irrelevant'
    finally:
        store.db.close()
