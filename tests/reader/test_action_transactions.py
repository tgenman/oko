"""HTTP failures must not expose half of a settings/job transition."""
from tests.support.storage import database
import pytest
from fastapi.testclient import TestClient

from oko.collection.demo import DemoSource
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.storage import database


@pytest.mark.parametrize('point', ['settings_after_values', 'settings_after_invalidation', 'settings_after_jobs'])
def test_failed_settings_change_rolls_back_values_versions_and_jobs(tmp_path, point):
    armed = False

    def fault(stage):
        if armed and stage == point:
            raise RuntimeError('synthetic settings failure')

    def saved():
        with database(tmp_path) as db:
            return (
                [tuple(r) for r in db.execute('SELECT scope,owner,field,value FROM settings ORDER BY scope,owner,field')],
                [tuple(r) for r in db.execute("SELECT key,value FROM state WHERE key LIKE 'settings-%' ORDER BY key")],
                [tuple(r) for r in db.execute("SELECT id,status,generation FROM jobs ORDER BY id")],
            )

    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource(), fault=fault), headers=HEADERS) as client:
        cards = wait_for(client, lambda: client.get('/api/v3/cards').json() or None)
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        identity = cards[0]['id']
        with database(tmp_path) as db:
            db.execute("UPDATE jobs SET status = 'running' , generation = 42 WHERE kind = 'fetch' AND item = %s", (identity,))
        before = saved()
        armed = True
        failed = client.patch('/api/v3/settings/feed/demo', json={'originals': False})
        assert failed.status_code == 500, failed.text
        assert saved() == before
        assert client.get('/api/v3/settings/feed/demo').json()['effective']['originals']['value'] is True
        armed = False
        response = client.patch('/api/v3/settings/feed/demo', json={'originals': False})
        assert response.status_code == 200, response.text
        assert response.json()['effective']['originals']['value'] is False
        with database(tmp_path) as db:
            status, generation = db.execute("SELECT status , generation FROM jobs WHERE kind = 'fetch' AND item = %s", (identity,)).fetchone()
        assert status == 'pending' and generation > 42


def test_nested_repository_commit_cannot_escape_action_transaction(tmp_path):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction, TransactionAborted

    store = Store(tmp_path, lambda _: None)
    try:
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                store.set_work_enabled(False)
                try:
                    with store.db:
                        raise ValueError('repository failure caught by caller')
                except ValueError:
                    pass
        with database(tmp_path) as db:
            assert db.execute("SELECT value FROM state WHERE key='work-enabled'").fetchone()[0] == 'true'
        with transaction(store.db):
            store.set_work_enabled(False)
        assert store.work_enabled() is False
    finally:
        store.db.close()
