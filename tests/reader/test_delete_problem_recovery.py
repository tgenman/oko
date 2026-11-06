from tests.support.job_claim import claim_job, retry_job
import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_delete_problem_event_get_is_read_only_and_finish_rollback(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'delete','item',1)
        retry_job(store, job,1,'error',code='delete')
        identity='job:delete:item:delete:item'
        before=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert before and not any(p['id']==identity for p in store.problems.candidates(2))
        assert any(p['id']==before['id'] for p in store.problems.snapshot(2)['current'])
        assert store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()==before
        with store.db:store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=claim_job(store, 1,deleting_only=True)
        assert claimed and store.publication_files.delete(claimed.item) is True
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.finish_delete(claimed)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]==before['id']
    finally:store.db.close()


def test_restarted_running_deletion_updates_retry_state(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db:
        job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('feed-delete','feed','running','error','delete') RETURNING id").fetchone()[0]
        store.problems.sync_deletion(job,[],1)
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE identity='job:delete:feed:feed-delete:feed'").fetchone()[0])['state']=='retrying'
    store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE identity='job:delete:feed:feed-delete:feed' AND finished IS NULL").fetchone()[0])['state']=='current'
    finally:store.db.close()
def test_cleanup_failure_backoff_survives_restart_and_increases(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,clock=lambda:clock[0],wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            identity=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','retired','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(identity,'delete','retired',1),clock[0],'cleanup failed',code='delete',cleanup_backoff=True)
        assert tuple(store.db.execute('SELECT retry_delay,retry_at,next FROM jobs WHERE id=%s',(identity,)).fetchone())==(60.,160.,160.)
    finally:
        store.db.close()
    clock[0]=110.
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,clock=lambda:clock[0],wall_clock=lambda:clock[0])
    try:
        row=store.db.execute('SELECT retry_delay,retry_at,next FROM jobs WHERE id=%s',(identity,)).fetchone()
        assert tuple(row)==(60.,160.,160.)
        assert claim_job(store,159.,deleting_only=True) is None
        job=claim_job(store,160.,deleting_only=True)
        assert job is not None
        clock[0]=160.
        with transaction(store.db):
            store.jobs.retry(job,clock[0],'cleanup failed again',code='delete',cleanup_backoff=True)
        assert tuple(store.db.execute('SELECT retry_delay,retry_at,next FROM jobs WHERE id=%s',(identity,)).fetchone())==(120.,280.,280.)
        for expected in (240,480,960,1920,3600,3600):
            clock[0]=store.db.execute('SELECT retry_at FROM jobs WHERE id=%s',(identity,)).fetchone()[0]
            job=claim_job(store,clock[0],deleting_only=True)
            assert job is not None
            with transaction(store.db):
                store.jobs.retry(job,clock[0],'cleanup still failing',code='delete',cleanup_backoff=True)
            delay,deadline=store.db.execute('SELECT retry_delay,retry_at FROM jobs WHERE id=%s',(identity,)).fetchone()
            assert (delay,deadline)==(expected,clock[0]+expected)
    finally:
        store.db.close()
