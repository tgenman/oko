"""Synthetic malformed legacy action records must not prevent Store startup."""
import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store


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


def test_http_rejects_replay_of_malformed_receipt(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('receipt','command','{}',1,'failed')")
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    with store.db:store.problems.sync_action('command',store.wall_clock())
    store.db.close()
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
        assert 'synthetic-retained' not in status.text
        assert client.post('/api/v3/problems/bad-problem/abandon').status_code in (400,404,409)
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert json.loads(db.execute("SELECT body FROM reader_problems WHERE id='bad-problem'").fetchone()[0])==body
            assert json.loads(db.execute("SELECT body FROM reader_action_results WHERE id='bad-receipt'").fetchone()[0])==body
            assert db.execute('SELECT viewed FROM publications WHERE id=%s',(item,)).fetchone()[0] is True
