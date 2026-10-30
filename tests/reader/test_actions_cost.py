from tests.support.cards import read_all_cards
from tests.support.storage import analyze
from tests.support.postgres_work import measure_reads
"""Scoped actions and status keep bounded work through the reader HTTP interface."""
from fastapi.testclient import TestClient
import pytest

from tests.support.reading_cost import library, traced_app, tracing
from tests.support.reader import HEADERS


def test_completion_preview_ignores_other_folders(tmp_path):
    costs = []
    for other in (26, 100):
        root = tmp_path / str(other)
        feeds, _ = library(root, [26, other, other, other])
        app = traced_app(root)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            nav = client.get('/api/v3/reading-composition').json()
            folder = next(f['feed']['folder_id'] for f in nav['feeds'] if f['feed']['id'] == feeds[0])
            response = client.post('/api/v3/folders/' + folder + '/completion/preview')
            assert response.status_code == 200
            assert response.json()['publications_count'] == 26
            assert response.json()['cards_count'] == 26
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1] <= costs[0] + 2, costs
    assert costs[0] < 400, costs


@pytest.mark.parametrize('count', [26, 100])
def test_status_and_viewed_work_budgets(tmp_path, count):
    _, items = library(tmp_path, [count] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        first = client.get('/api/v3/status')
        assert first.status_code == 200
        assert int(first.headers['X-Test-SQL']) < 100
        second = client.get('/api/v3/status')
        assert second.status_code == 200
        assert int(second.headers['X-Test-SQL']) < 100
        response = client.post('/api/v3/materials/' + items[0] + '/viewed')
        assert response.status_code == 200
        # PostgreSQL tracing counts statements issued by the application.
        assert int(response.headers['X-Test-SQL']) < 240
        assert client.get('/api/v3/publication/' + items[0]).status_code == 404
        assert len(read_all_cards(client, '/api/v3/cards')) == count * 4 - 1


def test_unchanged_status_does_not_rewrite_problems(tmp_path):
    _, items = library(tmp_path, [26] * 4)
    from tests.support.storage import Store
    seed = Store(tmp_path, lambda _: None)
    with seed.db:
        seed.db.execute("UPDATE publications SET content = jsonb_set(jsonb_set(content::jsonb,'{extra,_reader,full}','false'::jsonb),'{extra,_reader,reason}',to_jsonb('Fixture incomplete'::text))::text, incomplete_class=0 WHERE id = %s",(items[0],))
    with seed.db:
        seed.db.execute("UPDATE jobs SET status = 'done' WHERE kind = 'fetch' AND item = %s", (items[0],))
    seed.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        first = client.get('/api/v3/status/details')
        assert first.status_code == 200
        second = client.get('/api/v3/status/details')
        assert second.status_code == 200
        assert not any(q.startswith('UPDATE reader_problems') for q in app.state.last_sql)
        assert first.json()['problems']['current'] == second.json()['problems']['current']
        original_problem = next(p for p in first.json()['problems']['current'] if p['code'] == 'material_incomplete')

        def change_fixture():
            store = app.state.host.require_store()
            with store.db:
                store.db.execute("UPDATE publications SET content = jsonb_set(jsonb_set(content::jsonb,'{extra,_reader,full}','true'::jsonb),'{extra,_reader,reason}',to_jsonb(''::text))::text WHERE id = %s",(items[0],))
                store.db.execute("UPDATE jobs SET status = 'done' WHERE kind = 'fetch' AND item = %s", (items[0],))
        client.portal.call(change_fixture)
        response = client.get('/api/v3/status/details')
        assert response.status_code == 200
        assert not any(p['code'] == 'material_incomplete' for p in response.json()['problems']['current'])
        assert any(p['id'] == original_problem['id'] and p['outcome'] == 'resolved' for p in response.json()['problems']['recent'])


def test_latest_outcomes_scale_and_survive_restart(tmp_path):
    import json
    from tests.support.storage import Store
    from tests.support.reading_cost import request_sql
    from oko.reader.commands_v3 import context_record
    from oko.reader.generated.models import CommandContext

    store = Store(tmp_path, lambda _: None)
    rows = []
    for n in range(3000):
        context = context_record(CommandContext.model_validate({'operation':'retry_material','material_id':f'fixture-{n}'}))
        identity = context['identity']
        encoded = json.dumps(context)
        rows.append((f'old-{n:032x}', identity, encoded, store.wall_clock(), 'failed'))
        rows.append((f'new-{n:032x}', identity, encoded, store.wall_clock(), {0: 'succeeded', 1: 'unknown', 2: 'pending'}.get(n, 'failed')))
    with store.db:
        store.db.executemany('INSERT INTO reader_action_results ( id , identity , body , started , result ) VALUES ( %s , %s , %s , %s , %s )', rows)
        # Model the pre-migration state: the v2 recovery marker does not yet exist.
        store.db.execute("DELETE FROM state WHERE key IN ('action-problems-recovered-v1','action-problems-recovered-v2')")
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='reader_action_identity_sequence'").fetchone()
    store.set_work_enabled(False)
    store.db.close()
    for _ in range(2):
        app = traced_app(tmp_path)
        with TestClient(app, headers=HEADERS) as client:
            tracing(client, app)
            assert app.state.host.require_store().db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'action:%%' AND finished IS NULL").fetchone()[0]==2999
            with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
                response = client.get('/api/v3/status/details')
            assert response.status_code == 200
            assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)
            problems = response.json()['problems']['current']
            assert len(problems) == 2999
            assert all(p['command_id'].startswith('new-') for p in problems)
            assert sum(p['title'] == 'Результат действия пока неизвестен' for p in problems) == 2
            assert work.rows < 60000, work
            again = client.get('/api/v3/status/details')
            assert len(again.json()['problems']['current']) == 2999
            assert not any(q.startswith('UPDATE reader_problems') for q in app.state.last_sql)


def test_viewed_uses_point_updates_in_large_service_archive(tmp_path):
    from tests.support.storage import Store
    from tests.support.reading_cost import request_sql

    feeds, items = library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.executemany('INSERT INTO history_members ( feed , item ) VALUES ( %s , %s )', [(feeds[0], f'old-{n}') for n in range(20000)] + [(feeds[0], items[0])])
        store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )", [(f'old-{n}',) for n in range(20000)])
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='history_members_item'").fetchone()
        assert store.db.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='jobs_item'").fetchone()
    store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
            response = client.post('/api/v3/materials/' + items[0] + '/viewed')
        assert response.status_code == 200
        assert client.get('/api/v3/publication/' + items[0]).status_code == 404
        assert work.rows < 5000, work


def test_status_does_not_scan_completed_job_archive(tmp_path):
    from tests.support.storage import Store
    from tests.support.reading_cost import request_sql

    library(tmp_path, [1])
    store = Store(tmp_path, lambda _: None)
    with store.db:
        store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )", [(f'old-{n}',) for n in range(20000)])
        analyze(store.db)
    store.db.close()
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        with measure_reads(app.state.host.require_store().db, enabled=lambda: request_sql.get() is not None) as work:
            response = client.get('/api/v3/status')
        assert response.status_code == 200
        assert work.rows < 10000, work


def test_codex_configuration_problem_changes_without_status_writes(tmp_path):
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        assert client.patch('/api/v3/settings/oko/global', json={'processor':'codex','theses':True}).status_code == 200
        response = client.get('/api/v3/status/details')
        assert response.status_code == 200
        assert any(p['code']=='processor_not_configured' for p in response.json()['problems']['current'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)
        assert client.patch('/api/v3/settings/oko/global', json={'theses':False}).status_code == 200
        response = client.get('/api/v3/status/details')
        assert not any(p['code']=='processor_not_configured' for p in response.json()['problems']['current'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)


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


def test_interrupted_command_problem_is_recovered_before_status_read(tmp_path):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None)
    try:
        from oko.reader.commands_v3 import context_record
        from oko.reader.generated.models import CommandContext
        context=context_record(CommandContext.model_validate({'operation':'collect','target':{'scope':'all'}}))
        store.problems.action_started('synthetic-pending',context,store.wall_clock())
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s',('action:'+context['identity'],)).fetchone() is None
    finally:
        store.db.close()
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        tracing(client,app)
        status=client.get('/api/v3/status/details')
        assert any(p['code']=='command_outcome_unknown' and p['command_id']=='synthetic-pending' for p in status.json()['problems']['current'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)


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


def test_telegram_profile_problem_is_saved_before_status_read(tmp_path):
    from tests.support.telegram import Telegram
    class Account(Telegram):
        user=101
        async def accounts(self):
            return ['one']
        async def identity(self,account):
            return self.user
        async def history_page(self,account,peer,upper,before,since):
            return dict(messages=(await self.page(account,peer,0,since))['messages'],upper=11,next_before=11,more=False)
    network=Account()
    app=traced_app(tmp_path,telegram_client=network)
    with TestClient(app,headers=HEADERS) as client:
        tracing(client,app)
        assert client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'one'}).status_code==200
        body=dict(source_locator='@fixture',folder='Inbox',access={'kind':'client','profile':'personal'})
        assert client.post('/api/v3/telegram/connect',json=body).status_code==200
        network.user=202
        assert client.post('/api/v3/telegram/connect',json=dict(body,source_locator='@different')).status_code==409
        def profile_row():
            row=app.state.host.require_store().db.execute("SELECT id,finished,outcome,xmin::text FROM reader_problems WHERE identity='profile:personal' ORDER BY created DESC LIMIT 1").fetchone()
            return row[:] if row else None
        before=client.portal.call(profile_row)
        assert before and before[1] is None
        status=client.get('/api/v3/status/details')
        assert status.status_code==200
        assert any(p['id']==before[0] and p['code']=='account_identity_mismatch' for p in status.json()['problems']['current'])
        assert client.portal.call(profile_row)==before
        network.user=101
        assert client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'one'}).status_code==200
        resolved=client.portal.call(profile_row)
        assert resolved and resolved[0]==before[0] and resolved[2]=='resolved'
        status=client.get('/api/v3/status/details')
        assert not any(p['id']==before[0] for p in status.json()['problems']['current'])
        assert client.portal.call(profile_row)==resolved


def test_telegram_profile_restart_problem_is_saved_before_status_read(tmp_path):
    from tests.support.telegram import Telegram
    class Account(Telegram):
        async def accounts(self):
            return ['one']
        async def identity(self,account):
            return 101
        async def history_page(self,account,peer,upper,before,since):
            return dict(messages=(await self.page(account,peer,0,since))['messages'],upper=11,next_before=11,more=False)
    options=dict(telegram_client=Account())
    app=traced_app(tmp_path,**options)
    with TestClient(app,headers=HEADERS) as client:
        assert client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'one'}).status_code==200
        connected=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox','access':{'kind':'client','profile':'personal'}})
        assert connected.status_code==200
        feed_id=connected.json()['feed_id']
        assert client.put('/api/v3/power',json={'enabled':False}).status_code==200
    restarted=traced_app(tmp_path,**options)
    with TestClient(restarted,headers=HEADERS) as client:
        tracing(client,restarted)
        status=client.get('/api/v3/status/details')
        assert status.status_code==200
        problem=next(p for p in status.json()['problems']['current'] if p['code']=='profile_unavailable')
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in restarted.state.last_sql)
        assert client.delete('/api/v3/feeds/'+feed_id).status_code==200
        status=client.get('/api/v3/status/details')
        assert not any(p['id']==problem['id'] for p in status.json()['problems']['current'])
        assert any(p['id']==problem['id'] and p['outcome']=='irrelevant' for p in status.json()['problems']['recent'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in restarted.state.last_sql)
