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
        assert claimed and store.jobs.delete_files(claimed) is True
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
