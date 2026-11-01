import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store
from tests.support.read_problem import setup


def test_read_failure_retry_and_finish_are_atomic_without_status_request(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'read','feed',1)
        def current():
            return store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(job,1,'failure',code='source-connection')
                assert current() is not None
                raise ValueError('rollback')
        assert current() is None
        store.jobs.retry(job,1,'failure',code='source-connection')
        old=current();assert old
        assert not [candidate for candidate in store.problems.candidates(2) if candidate.get('operation')=='producer:read']
        before=store.db.execute('SELECT xmin::text,body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
        state=store.problems.snapshot(2)
        assert [p['id'] for p in state['current'] if p['code']=='source-connection']==[old['id']]
        assert store.db.execute('SELECT xmin::text,body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()==before
        with store.db:store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=store.jobs.next_job(1,feeds=['feed'])
        assert claimed and claimed.id==job_id
        assert json.loads(current()['body'])['state']=='retrying'
        with transaction(store.db):store.jobs.finish(claimed)
        assert current() is None
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_profile_problem_suppresses_and_releases_read_error_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'read','feed',1),1,'failure',code='identity-unconfirmed')
        identity='job:identity-unconfirmed:feed:read:feed'
        original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                assert not store.check_telegram_profile('personal',1,202,2)
                assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
                raise ValueError('rollback')
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]==original
        assert not store.check_telegram_profile('personal',1,202,2)
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert store.check_telegram_profile('personal',1,101,3)
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_restart_recovers_running_read_problem_as_pending(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('read','feed','running',1,'failure','source-connection') RETURNING id").fetchone()[0]
            store.problems.sync_read_feed('feed',1)
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()
        assert json.loads(before['body'])['state']=='retrying'
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        after=store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone()
        assert after and json.loads(after['body'])['state']=='current'
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job_id,)).fetchone()[0]=='pending'
    finally:store.db.close()


def test_http_status_does_not_write_read_problem(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        def arrange():
            store=app.state.host.require_store()
            setup(store)
            with store.db:
                job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job_id,'read','feed',1),1,'failure',code='source-connection')
            row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
            return row[0]
        identity=client.portal.call(arrange)
        statements=[]
        client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(statements.append))
        try: response=client.get('/api/v3/status/details')
        finally:client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(None))
        assert response.status_code==200
        assert any(p['id']==identity for p in response.json()['problems']['current'])
        assert not any('UPDATE reader_problems' in sql or 'INSERT INTO reader_problems' in sql for sql in statements),statements


def test_stale_read_finish_does_not_publish_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('read','feed','running',2,'failure','source-connection') RETURNING id").fetchone()[0]
        with transaction(store.db):
            store.jobs.finish_read(Job(job_id,'read','feed',1),'waiting',1)
        assert store.db.execute("SELECT status,generation FROM jobs WHERE id=%s",(job_id,)).fetchone() == ('running',2)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:read'").fetchone()
    finally:store.db.close()


def test_saved_backfill_suppresses_then_restores_read_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','waiting')")
            store.problems.sync_read_feed('feed',1)
        identity='job:work-read:feed:read:feed'
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        with transaction(store.db):store.read_sync.schedule_saved_backfill('feed')
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        list(store.jobs.sync_reads_steps())
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()
