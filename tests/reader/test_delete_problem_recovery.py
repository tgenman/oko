import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_delete_problem_event_get_is_read_only_and_finish_rollback(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',1)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'delete','item',1)
        store.jobs.retry(job,1,'error',code='delete')
        identity='job:delete:item:delete:item'
        before=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert before and not any(p['id']==identity for p in store.problems.candidates(2))
        assert any(p['id']==before['id'] for p in store.problems.snapshot(2)['current'])
        assert store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()==before
        with store.db:store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=store.jobs.next_job(1,deleting_only=True)
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.delete(claimed)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]==before['id']
    finally:store.db.close()


def test_recovery_resumes_and_keeps_legacy_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='delete-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(id,kind,item,status,error,problem_code) SELECT n,'delete','item-'||n,'failed','error','delete' FROM generate_series(-1,99) n")
            body=dict(id='job:delete:item--1:delete:item--1',code='delete',objects=[],operation='producer:status',state='current')
            store.problems.sync_candidate(body['id'],body,1)
            old=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(body['id'],)).fetchone()[0]
        store.db.close()
        def fault(point):
            if point=='delete_problem_recovery_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):
            Store(tmp_path,fault,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:delete'").fetchone()[0]==101
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(body['id'],)).fetchone()[0]==old
    finally:store.db.close()


def test_other_codes_stay_in_get_and_legacy_unknown_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','x','failed','error','custom')")
            body=dict(id='job:delete:unidentifiable:delete:',code='delete',objects=[],operation='producer:status',state='current')
            store.problems.sync_candidate(body['id'],body,1)
        assert any(p['id']=='job:custom:x:delete:x' for p in store.problems.candidates(2))
        store.problems.snapshot(2)
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(body['id'],)).fetchone()
    finally:store.db.close()


def test_foreign_collision_does_not_overwrite_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        canonical='job:delete:a:delete:b:fetch:c:delete:a:delete:b:fetch:c'
        with store.db:
            body=dict(id=canonical,code='delete',objects=[],operation='producer:fetch',state='current')
            store.problems.sync_candidate(canonical,body,1)
            original=store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s',(canonical,)).fetchone()
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','a:delete:b:fetch:c','failed','error','delete') RETURNING id").fetchone()[0]
            store.problems.sync_deletion(job,[],2)
        assert store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s',(canonical,)).fetchone()==original
        found=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:delete' AND finished IS NULL").fetchone()
        assert found and found['identity']!=canonical
        with store.db:store.problems.sync_deletion(job,[],3)
        assert store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:delete' AND finished IS NULL").fetchone()==found
    finally:store.db.close()


def test_restarted_running_deletion_updates_retry_state(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db:
        job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('feed-delete','feed','running','error','delete') RETURNING id").fetchone()[0]
        store.problems.sync_deletion(job,[],1)
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE identity='job:delete:feed:feed-delete:feed'").fetchone()[0])['state']=='retrying'
    store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE identity='job:delete:feed:feed-delete:feed' AND finished IS NULL").fetchone()[0])['state']=='current'
    finally:store.db.close()


def test_delete_queries_ignore_10000_foreign_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'delete','item-'||n,'failed','error','delete' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        statements=[];store.db.set_trace_callback(statements.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in statements if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
        query="SELECT id FROM jobs WHERE kind IN ('delete','feed-delete') AND (error IS NOT NULL OR problem_code IS NOT NULL) AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete') AND id>%s ORDER BY id LIMIT 100"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(9000,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<=100,plan
    finally:store.db.close()


def test_recovery_finishes_identifiable_stale_but_preserves_ambiguous_legacy(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            for identity in ('job:delete:feed:delete:gone','job:delete:feed:fetch:other:delete:ambiguous'):
                store.problems.sync_candidate(identity,dict(id=identity,code='delete',objects=[],operation='producer:status',state='current'),1)
            store.db.execute("DELETE FROM state WHERE key='delete-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:delete:feed:delete:gone'").fetchone()[0]=='resolved'
        assert store.db.execute("SELECT finished FROM reader_problems WHERE identity='job:delete:feed:fetch:other:delete:ambiguous'").fetchone()[0] is None
        store.problems.snapshot(2)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE identity='job:delete:feed:fetch:other:delete:ambiguous'").fetchone()[0] is None
    finally:store.db.close()


def test_two_delete_jobs_with_same_text_key_keep_distinct_occurrences(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            first=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('delete','b:delete:c','a','failed','error','delete') RETURNING id").fetchone()[0]
            second=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('delete','c','a:delete:b','failed','error','delete') RETURNING id").fetchone()[0]
            store.problems.sync_deletion(first,[],1)
            store.problems.sync_deletion(second,[],1)
        rows=store.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:delete' AND finished IS NULL ORDER BY id").fetchall()
        assert len(rows)==2 and len({row['identity'] for row in rows})==2
        assert {json.loads(row['body'])['deletion_job_id'] for row in rows}=={str(first),str(second)}
        with store.db:
            previous=store.problems.deletion_candidates(first,2)
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE id=%s",(first,))
            store.problems.sync_deletion(first,previous,2)
        remaining=store.db.execute("SELECT body FROM reader_problems WHERE body::jsonb->>'operation'='producer:delete' AND finished IS NULL").fetchall()
        assert len(remaining)==1 and json.loads(remaining[0]['body'])['deletion_job_id']==str(second)
    finally:store.db.close()


def test_legacy_record_recovery_resumes_after_committed_batch(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            for index in range(101):
                identity=f'job:delete:feed:delete:item-{index}'
                store.problems.sync_candidate(identity,dict(id=identity,code='delete',objects=[],operation='producer:status',state='current'),1)
            store.db.execute("DELETE FROM state WHERE key='delete-problems-recovered-v1'")
        store.db.close()
        def fault(point):
            if point=='delete_problem_record_recovery_batch':raise RuntimeError('interrupted record recovery')
        with pytest.raises(RuntimeError,match='interrupted record recovery'):
            Store(tmp_path,fault,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='delete' AND finished IS NULL").fetchone()[0]==0
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='delete' AND outcome='resolved'").fetchone()[0]==101
    finally:store.db.close()


def test_legacy_recovery_index_does_not_scan_foreign_history(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'job:other:feed:fetch:'||n,'background',%s,1 FROM generate_series(1,10000) n",(json.dumps(dict(code='other',operation='producer:fetch',objects=[])),))
            store.db._db.execute('ANALYZE reader_problems')
        query="""SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%'
            AND body::jsonb->>'code' IN ('delete','feed-delete','work-delete','work-feed-delete')
            AND (body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete') OR body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status') ORDER BY id LIMIT 100"""
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_restart_running_lookup_ignores_pending_delete_history(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'delete','item-'||n,'pending','failure','delete' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        query="SELECT id FROM jobs WHERE kind IN ('delete','feed-delete') AND status='running' AND (error IS NOT NULL OR problem_code IS NOT NULL) AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete')"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_later_other_kind_collision_keeps_both_problems(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','a:delete:b:fetch:c','failed','error','delete') RETURNING id").fetchone()[0]
            store.problems.sync_deletion(job,[],1)
            deletion=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:delete' AND finished IS NULL").fetchone()
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','c:delete:a:delete:b:fetch:c','a:delete:b','failed','error','delete')")
        store.problems.snapshot(2)
        assert store.db.execute('SELECT id,identity FROM reader_problems WHERE id=%s AND finished IS NULL',(deletion['id'],)).fetchone()==deletion
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()
    finally:store.db.close()
