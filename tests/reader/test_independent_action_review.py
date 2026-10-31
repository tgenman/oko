"""Synthetic malformed legacy action records must not prevent Store startup."""
import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store


@pytest.mark.parametrize('result,body',[
    ('failed',{}),('pending',{}),('unknown',None),
    ('failed',{'action':None,'title':'x','objects':[None]}),
])
def test_malformed_action_receipt_is_preserved_and_visible(tmp_path,postgres_database_url,result,body):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','command',%s,1,%s)",(json.dumps(body),result))
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        result_state=store.problems.snapshot(store.wall_clock())
        assert any(p['code'] in ('action-failed','action-unknown') for p in result_state['current'])
        assert json.loads(store.db.execute("SELECT body FROM reader_action_results WHERE id='receipt'").fetchone()[0]) == body
    finally:store.db.close()


@pytest.mark.parametrize('body',[{}, {'objects':[None]}])
def test_malformed_existing_action_problem_preserves_body_and_identity(tmp_path,postgres_database_url,body):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem','action:command','action',%s,1)",(json.dumps(body),))
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        result_state=store.problems.snapshot(store.wall_clock())
        assert any(p['id']=='problem' for p in result_state['current'])
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='problem'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_pending_malformed_receipt_does_not_crash_even_after_recovery_marker(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO state(key,value) VALUES ('action-problems-recovered-v1','true') ON CONFLICT (key) DO UPDATE SET value=excluded.value")
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','command','{}',1,'pending')")
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        result_state=store.problems.snapshot(store.wall_clock())
        assert any(p['code']=='action-unknown' for p in result_state['current'])
        assert store.db.execute("SELECT body FROM reader_action_results WHERE id='receipt'").fetchone()[0]=='{}'
    finally:store.db.close()


def test_http_status_shows_safe_generic_error_and_rejects_abandon(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        body={'secret':'synthetic-do-not-expose','objects':[None]}
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem','action:command','action',%s,1)",(json.dumps(body),))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        problems=response.json()['problems']['current']
        problem=next(p for p in problems if p['id']=='problem')
        assert problem['code']=='command_outcome_unknown'
        assert problem['recovery']==[] and problem['affected']==[]
        assert 'synthetic-do-not-expose' not in response.text
        assert client.post('/api/v3/problems/problem/abandon').status_code in (400,404,409)
        with psycopg.connect(postgres_database_url,autocommit=True) as db:
            row=db.execute("SELECT body,finished FROM reader_problems WHERE id='problem'").fetchone()
            assert json.loads(row[0])==body and row[1] is None


def test_action_recovery_resumes_with_mixed_good_and_bad_receipts(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        normal=json.dumps(dict(identity='valid',title='Command',objects=[],action=None,discardable=True))
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) SELECT 'good-'||n,'valid-'||n,%s,1,'failed' FROM generate_series(1,100) n",(normal,))
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('bad','bad','{}',1,'failed')")
    def fault(point):
        if point=='action_problem_recovery_batch':raise RuntimeError('recovery-interrupted')
    with pytest.raises(RuntimeError,match='recovery-interrupted'):
        Store(tmp_path,fault,database_url=postgres_database_url)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        state=store.problems.snapshot(store.wall_clock())
        assert len([p for p in state['current'] if p['code']=='action-failed'])==101
        assert store.db.execute("SELECT body FROM reader_action_results WHERE id='bad'").fetchone()[0]=='{}'
        assert store.db.execute("SELECT 1 FROM state WHERE key='action-problems-recovered-v2'").fetchone()
    finally:store.db.close()


def test_http_rejects_replay_of_malformed_receipt(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','command','{}',1,'failed')")
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        problem=next(p for p in response.json()['problems']['current'] if p['code']=='command_failed')
        assert problem['command_id']=='receipt' and problem['recovery']==[] and problem['affected']==[]
        retried=client.patch('/api/v3/settings/oko/global',json={'theses':False},headers={'X-Oko-Action':'receipt'})
        assert retried.status_code==409,retried.text
        assert client.post('/api/v3/problems/'+problem['id']+'/abandon').status_code in (400,404,409)
        with psycopg.connect(postgres_database_url,autocommit=True) as db:
            assert db.execute("SELECT body,result FROM reader_action_results WHERE id='receipt'").fetchone()==('{}','failed')


def test_http_empty_problem_id_surrogate_is_stable_and_unactionable(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.storage.problems import _legacy_problem_id
    identity='action:missing';collision='legacy-unaddressable-valid'
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('',%s,'action','{}',1),(%s,'action:other','action','{}',2)",(identity,collision))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        first=client.get('/api/v3/status/details');second=client.get('/api/v3/status/details')
        assert first.status_code==second.status_code==200
        first_ids={p['id'] for p in first.json()['problems']['current']}
        second_ids={p['id'] for p in second.json()['problems']['current']}
        assert first_ids==second_ids=={collision,_legacy_problem_id(identity)}
        assert all(p['recovery']==[] for p in first.json()['problems']['current'])
        assert client.post('/api/v3/problems/'+_legacy_problem_id(identity).replace('\0','%00')+'/abandon').status_code in (400,404,409)
        with psycopg.connect(postgres_database_url,autocommit=True) as db:
            assert db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL").fetchone()[0]==2


def test_http_keeps_normal_action_visible_with_corrupt_sibling(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        context=dict(identity='{"operation":"codex_save"}',title='Normal command',objects=[dict(kind='settings',id='global')],action=None,discardable=True,command_context={'operation':'codex_save'})
        store.problems.report_action('valid',context,'failed',1)
        with store.db:store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','action:bad','action','{}',2)")
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        problems=response.json()['problems']['current']
        good=next(p for p in problems if p.get('command_id')=='valid')
        assert good['code']=='command_failed' and good['title']=='Normal command'
        bad=next(p for p in problems if p['id']=='bad')
        assert bad['code']=='command_outcome_unknown' and bad['recovery']==[]


@pytest.mark.parametrize('created',[float('inf'),1e30])
def test_http_malformed_action_with_invalid_created_time_is_visible(tmp_path,postgres_database_url,created):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','action:bad','action','{}',%s)",(created,))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        p=next(p for p in response.json()['problems']['current'] if p['id']=='bad')
        assert p['code']=='command_outcome_unknown' and p['recovery']==[]
        assert 'время проверки' in p['consequence']
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        assert db.execute("SELECT created FROM reader_problems WHERE id='bad'").fetchone()[0]==created


@pytest.mark.parametrize('finished,outcome',[(float('inf'),'resolved'),(2000000000,'unknown'),(None,'resolved')])
def test_http_malformed_action_with_bad_completion_remains_visible(tmp_path,postgres_database_url,finished,outcome):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('bad','action:bad','action','{}',1,%s,%s)",(finished,outcome))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        p=next(p for p in response.json()['problems']['current'] if p['id']=='bad')
        assert p['code']=='command_outcome_unknown' and p['recovery']==[]
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        assert db.execute("SELECT finished,outcome FROM reader_problems WHERE id='bad'").fetchone()==(finished,outcome)


def test_corrupt_action_lookup_ignores_unrelated_receipts(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) SELECT 'foreign-'||n,'foreign-'||n,'{}',1,'succeeded' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) SELECT 'same-'||n,'bad','{}',1,'succeeded' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('bad','bad','{}',1,'failed')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem','action:bad','action','{}',1)")
            store.db._db.execute('ANALYZE reader_action_results')
        sql='SELECT r.identity,r.id,r.body,r.result,r.started FROM unnest(%s::text[]) target(identity) CROSS JOIN LATERAL (SELECT identity,id,body,result,started FROM reader_action_results WHERE identity=target.identity ORDER BY sequence DESC LIMIT 1) r'
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(['bad'],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
        with store.db:
            store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) SELECT 'selected-'||n,'selected-'||n,'{}',1,'failed' FROM generate_series(1,400) n")
            store.db._db.execute('ANALYZE reader_action_results')
        selected=[f'selected-{n}' for n in range(1,401)]
        batch_plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(selected,)).fetchone()[0][0]['Plan']
        assert scan_rows(batch_plan)<3000,batch_plan
        assert any(p['id']=='problem' for p in store.problems.snapshot(store.wall_clock())['current'])
    finally:store.db.close()


def test_material_cleanup_does_not_overwrite_or_fail_on_malformed_action(tmp_path,postgres_database_url):
    body={'objects':[None,{'kind':'material','id':'item'}], 'secret':'synthetic'}
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','command',%s,1,'failed')",(json.dumps(body),))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem','action:command','action',%s,1)",(json.dumps(body),))
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        with store.db:store.problems.forget_materials({'item'},2)
        assert json.loads(store.db.execute("SELECT body FROM reader_action_results WHERE id='receipt'").fetchone()[0])==body
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='problem'").fetchone()[0])==body
        current=store.problems.snapshot(store.wall_clock())['current']
        p=next(p for p in current if p['id']=='problem')
        assert p['objects']==[] and 'synthetic' not in json.dumps(p)
    finally:store.db.close()


def test_http_viewed_succeeds_with_malformed_action_without_unsafe_recovery(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.presence import presence
    from tests.support.worker_library import worker_library
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    body={'objects':[None,{'kind':'material','id':item}], 'secret':'synthetic-retained'}
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('bad-receipt','bad-command',%s,1,'failed')",(json.dumps(body),))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad-problem','action:bad-command','action',%s,1)",(json.dumps(body),))
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        presence(client,'reader')
        response=client.post('/api/v3/materials/'+item+'/viewed')
        assert response.status_code==200,response.text
        status=client.get('/api/v3/status/details')
        assert status.status_code==200,status.text
        p=next(p for p in status.json()['problems']['current'] if p['id']=='bad-problem')
        assert p['affected']==[] and p['recovery']==[]
        assert item not in status.text and 'synthetic-retained' not in status.text
        assert client.post('/api/v3/problems/bad-problem/abandon').status_code in (400,404,409)
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert json.loads(db.execute("SELECT body FROM reader_problems WHERE id='bad-problem'").fetchone()[0])==body
            assert json.loads(db.execute("SELECT body FROM reader_action_results WHERE id='bad-receipt'").fetchone()[0])==body
            assert db.execute('SELECT count(*) FROM feed_publications WHERE publication_id=%s',(item,)).fetchone()[0]==0
        import time
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with psycopg.connect(postgres_url,autocommit=True) as db:
                if not db.execute('SELECT 1 FROM publications WHERE id=%s',(item,)).fetchone():
                    break
            time.sleep(.05)
        else:
            pytest.fail('Viewed publication was not physically removed')


def test_malformed_action_with_legacy_origin_mismatch_is_visible(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','action:bad','background','{}',1)")
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        problem=next(p for p in response.json()['problems']['current'] if p['id']=='bad')
        assert problem['code']=='command_outcome_unknown' and problem['recovery']==[]


def test_http_unrecognized_legacy_context_is_generic_without_raw_body(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    body=dict(identity='legacy',title='synthetic-secret-title',objects=[],action=None,discardable=True,command_context={'unknown_operation':'secret'})
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','legacy',%s,1,'failed')",(json.dumps(body),))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        p=next(p for p in response.json()['problems']['current'] if p.get('command_id')=='receipt')
        assert p['recovery']==[] and p['affected']==[]
        assert 'synthetic-secret-title' not in response.text and 'unknown_operation' not in response.text
        assert client.post('/api/v3/problems/'+p['id']+'/abandon').status_code in (400,404,409)


def test_http_abandon_accepts_equivalent_noncanonical_saved_identity(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        context=dict(identity='{ "operation" : "codex_save" }',title='Normal',objects=[dict(kind='settings',id='global')],action=None,discardable=True,command_context={'operation':'codex_save'})
        store.problems.report_action('valid',context,'failed',1)
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity LIKE 'action:%' AND finished IS NULL").fetchone()[0]
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.post('/api/v3/problems/'+problem+'/abandon')
        assert response.status_code==200,response.text


def test_http_rejects_abandon_when_saved_identity_conflicts_with_context(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        context=dict(identity='{"operation":"processor_save"}',title='Conflicting',objects=[dict(kind='settings',id='global')],action=None,discardable=True,command_context={'operation':'codex_save'})
        store.problems.report_action('conflict',context,'failed',1)
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity LIKE 'action:%' AND finished IS NULL").fetchone()[0]
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        status=client.get('/api/v3/status/details')
        assert status.status_code==200,status.text
        projected=next(p for p in status.json()['problems']['current'] if p['id']==problem)
        assert projected['code']=='command_failed' and projected['recovery']==[] and projected['affected']==[]
        assert projected['title']!='Conflicting'
        response=client.post('/api/v3/problems/'+problem+'/abandon')
        assert response.status_code in (400,404,409),response.text
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute("SELECT result FROM reader_action_results WHERE id='conflict'").fetchone()[0]=='failed'


def test_http_rejects_unknown_operation_even_when_identity_matches(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    unknown={'operation':'not_a_supported_operation'}
    body=dict(identity=json.dumps(unknown,separators=(',',':')),title='synthetic-unknown-secret',objects=[],action=None,discardable=True,command_context=unknown)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt',%s,%s,1,'failed')",(body['identity'],json.dumps(body)))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        p=next(p for p in response.json()['problems']['current'] if p.get('command_id')=='receipt')
        assert p['recovery']==[] and p['affected']==[]
        assert 'synthetic-unknown-secret' not in response.text
        assert client.post('/api/v3/problems/'+p['id']+'/abandon').status_code in (400,404,409)


def test_http_accepts_equivalent_settings_keys_in_different_order(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        saved={'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses','originals']}
        identity=json.dumps({'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['originals','theses']},separators=(',',':'))
        context=dict(identity=identity,title='Normal settings',objects=[dict(kind='settings',id='global')],action=None,discardable=True,command_context=saved)
        store.problems.report_action('valid',context,'failed',1)
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('action:'+identity,)).fetchone()[0]
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        status=client.get('/api/v3/status/details')
        assert status.status_code==200,status.text
        projected=next(p for p in status.json()['problems']['current'] if p['id']==problem)
        assert projected['title']=='Normal settings' and projected['recovery']
        response=client.post('/api/v3/problems/'+problem+'/abandon')
        assert response.status_code==200,response.text


def test_material_cleanup_preserves_valid_problem_when_receipt_is_corrupt(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.storage.transaction import transaction
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        raw={'objects':[None,{'kind':'material','id':item}],'secret':'retained'}
        body=dict(id='action:bad',code='action-failed',section='object',title='Previous context',consequence='Saved',next_step='Check',objects=[dict(kind='material',id=item)],action=None,operation='{"operation":"codex_save"}',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        with store.db:
            store.problems.sync_candidate(body['id'],body,1)
            original=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='action:bad'").fetchone()
            store.db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','bad',%s,1,'failed')",(json.dumps(raw),))
        with transaction(store.db):store.viewed(item)
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(original['id'],)).fetchone()
        assert row['body']==original['body'] and row['finished'] is None
        assert json.loads(store.db.execute("SELECT body FROM reader_action_results WHERE id='receipt'").fetchone()[0])==raw
        current=store.problems.snapshot(store.wall_clock())['current']
        projected=next(p for p in current if p['id']==original['id'])
        assert projected['objects']==[] and projected['operation']=='legacy-action'
    finally:store.db.close()


def test_valid_action_problem_without_receipt_is_kept_as_unknown(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    body=dict(code='action-failed',section='action',title='Original',consequence='Original consequence',next_step='Original action',objects=[dict(kind='material',id='missing')],action=None,operation='{"operation":"codex_save"}',state='current',next_at=None,request_id='missing')
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem','action:missing','action',%s,1)",(json.dumps(body),))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        problem=next(p for p in response.json()['problems']['current'] if p['id']=='problem')
        assert problem['code']=='command_outcome_unknown' and problem['affected']==[] and problem['recovery']==[]
        with psycopg.connect(postgres_database_url,autocommit=True) as db:
            saved=db.execute("SELECT body,finished FROM reader_problems WHERE id='problem'").fetchone()
            assert json.loads(saved[0])==body and saved[1] is None


def test_unrelated_legacy_prefix_id_remains_abandonable(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        context=dict(identity='{"operation":"codex_save"}',title='Normal',objects=[],action=None,discardable=True,command_context={'operation':'codex_save'})
        store.problems.report_action('valid',context,'failed',1)
        with store.db:
            store.db.execute("UPDATE reader_problems SET id='legacy-unaddressable-unrelated' WHERE identity='action:'||%s",(context['identity'],))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','action:damaged','action','{}',1)")
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.post('/api/v3/problems/legacy-unaddressable-unrelated/abandon')
        assert response.status_code==200,response.text


@pytest.mark.parametrize('bad_error',['not-json','{}','{"code":"unknown-code","message":"secret","details":null,"diagnostic_id":null,"command_id":null}'])
def test_corrupt_command_error_is_unknown_and_never_replayed(tmp_path,postgres_database_url,bad_error):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    context={'operation':'codex_save'}
    body=dict(title='synthetic-secret',objects=[],action=None,discardable=True,command_context=context,command_error=bad_error)
    identity=json.dumps(context,separators=(',',':'))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES ('receipt',%s,%s,1,2,'failed')",(identity,json.dumps(body)))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        reply=client.get('/api/v3/commands/receipt')
        assert reply.status_code==200,reply.text
        assert reply.json()['outcome']=='unknown'
        status=client.get('/api/v3/status/details')
        assert status.status_code==200,status.text
        p=next(p for p in status.json()['problems']['current'] if p.get('command_id')=='receipt')
        assert p['recovery']==[] and p['affected']==[] and 'synthetic-secret' not in status.text
        assert client.post('/api/v3/problems/'+p['id']+'/abandon').status_code in (400,404,409)
        config={kind:dict(model='synthetic',reasoning_effort='low',fast=False) for kind in ('filter','theses')}
        replay=client.put('/api/v3/processors/codex',json=config,headers={'X-Oko-Action':'receipt'})
        assert replay.status_code==409,replay.text
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        assert json.loads(db.execute("SELECT body FROM reader_action_results WHERE id='receipt'").fetchone()[0])==body
        assert db.execute("SELECT 1 FROM state WHERE key='processor:codex'").fetchone() is None


def test_recovery_preserves_existing_problem_when_error_json_has_wrong_shape(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    context={'operation':'codex_save'};identity=json.dumps(context,separators=(',',':'))
    raw_receipt=dict(title='receipt-title',objects=[],action=None,discardable=True,command_context=context,command_error='{}')
    raw_problem=dict(code='action-failed',section='action',title='original-problem-title',consequence='original',next_step='check',objects=[],action=None,operation='{"operation":"codex_save"}',state='current',next_at=None,request_id='receipt')
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt',%s,%s,1,'failed')",(identity,json.dumps(raw_receipt)))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem',%s,'action',%s,1)",('action:'+identity,json.dumps(raw_problem)))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        p=next(p for p in response.json()['problems']['current'] if p['id']=='problem')
        assert p['affected']==[] and p['recovery']==[]
        assert 'receipt-title' not in response.text and 'original-problem-title' not in response.text
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        assert json.loads(db.execute("SELECT body FROM reader_problems WHERE id='problem'").fetchone()[0])==raw_problem
        assert json.loads(db.execute("SELECT body FROM reader_action_results WHERE id='receipt'").fetchone()[0])==raw_receipt
