"""Scoped actions and status keep bounded work through the reader HTTP interface."""
from fastapi.testclient import TestClient
import pytest

from tests.support.reading_cost import traced_app, tracing
from tests.support.reader import HEADERS


def test_failed_command_problem_is_saved_before_status_read(tmp_path):
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        tracing(client,app)
        response=client.post('/api/v3/command-observations',json={'id':'synthetic-failed-setting','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses']},'result':'failed'})
        assert response.status_code==200,response.text
        status=client.get('/api/v3/status/details')
        assert status.status_code==200
        problem=next(p for p in status.json()['problems']['current'] if p['code']=='command_failed')
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)
        assert client.patch('/api/v3/settings/oko/global',json={'theses':False}).status_code==200
        status=client.get('/api/v3/status/details')
        assert not any(p['code']=='command_failed' for p in status.json()['problems']['current'])
        assert any(p['id']==problem['id'] and p['outcome']=='resolved' for p in status.json()['problems']['recent'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)
    restarted=traced_app(tmp_path)
    with TestClient(restarted,headers=HEADERS) as client:
        tracing(client,restarted)
        status=client.get('/api/v3/status/details')
        assert any(p['id']==problem['id'] and p['outcome']=='resolved' for p in status.json()['problems']['recent'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in restarted.state.last_sql)


def test_command_problem_and_outcome_roll_back_together(tmp_path):
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None)
    context=dict(identity='synthetic-operation',title='Synthetic failure',objects=[],action=None,discardable=True,
                 command_context={'operation':'collect','target':{'scope':'all'}})
    try:
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                assert store.problems.report_action('synthetic-failed',context,'failed',store.wall_clock())=='failed'
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='action:synthetic-operation' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='synthetic-failed'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='action:synthetic-operation'").fetchone() is None
        store.problems.action_started('synthetic-pending',context,store.wall_clock())
        store.db.close()
        store=Store(tmp_path,lambda _:None)
        assert 'synthetic-pending' in store.problems._startup_pending
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.problems.action_started('synthetic-pending',context,store.wall_clock(),restart=True)
                assert 'synthetic-pending' not in store.problems._startup_pending
                raise ValueError('rollback')
        assert 'synthetic-pending' in store.problems._startup_pending
        assert store.problems.command_record('synthetic-pending').result=='unknown'
    finally:
        store.db.close()
