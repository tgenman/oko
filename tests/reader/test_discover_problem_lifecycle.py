import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_discover_failure_and_completion_are_saved_without_status_read(tmp_path, postgres_url):
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id = store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','feed','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'discover','feed',1)
        identity='job:source-connection:feed:discover'
        def current():
            return store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(job,1,'source failed',code='source-connection')
                assert current()
                raise ValueError('rollback')
        assert current() is None
        store.jobs.retry(job,1,'source failed',code='source-connection')
        prior=current(); assert prior
        with store.db: store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=store.jobs.next_job(1,feeds=['feed'])
        assert claimed and claimed.id==job_id
        assert json.loads(current()['body'])['state']=='retrying'
        with transaction(store.db): store.jobs.finish(claimed)
        assert current() is None
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(prior['id'],)).fetchone()[0]=='resolved'
    finally: store.db.close()


def test_restart_recovers_existing_discover_failure_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('old','old','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','old','old','failed','failure','source-connection')")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:old:discover' AND finished IS NULL").fetchone()
        assert row
        assert store.db.execute("SELECT 1 FROM state WHERE key='discover-problems-recovered-v1'").fetchone()
    finally:store.db.close()


def test_profile_problem_suppresses_discovery_failure_without_status_read(tmp_path,postgres_url):
    from tests.support.read_problem import setup
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','feed','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='identity-unconfirmed')
        identity='job:identity-unconfirmed:feed:discover:feed'
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert not store.check_telegram_profile('personal',1,202,2)
        assert not store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert store.check_telegram_profile('personal',1,101,3)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_discover_does_not_overwrite_other_job_with_colliding_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','feed','feed','running',1) RETURNING id").fetchone()[0]
            body=dict(code='source-connection',operation='producer:fetch',objects=[dict(kind='feed',id='feed')],title='other')
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('other','job:source-connection:feed:discover','background',%s,1)",(json.dumps(body),))
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='other'").fetchone()[0])==body
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_discover_event_preserves_other_source_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[]),('first','first','First','default','rss','rss','connected',ARRAY[]::text[]),('second','second','Second','default','rss','rss','connected',ARRAY[]::text[])")
            first=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','first','feed','running',1) RETURNING id").fetchone()[0]
            second=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','second','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(first,'discover','first',1),1,'failure',code='custom')
        store.jobs.retry(Job(second,'discover','second',1),1,'failure',code='custom')
        rows=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL ORDER BY identity").fetchall()
        assert [r[0] for r in rows]==['job:custom:first:discover:first','job:custom:second:discover:second']
    finally:store.db.close()


def test_status_does_not_rewrite_discovery_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','feed','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        before=store.db.execute("SELECT id,xmin::text,body,finished FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()
        assert not [c for c in store.problems.candidates(2) if c.get('operation')=='producer:discover']
        store.problems.snapshot(2)
        after=store.db.execute('SELECT id,xmin::text,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_restart_reuses_legacy_problem_identity_and_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='feed')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','feed','feed','failed','failure','source-connection')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:feed:discover','background',%s,1)",(json.dumps(body),))
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        rows=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND (identity='job:source-connection:feed:discover' OR body::jsonb->>'operation'='producer:discover')").fetchall()
        assert [r[0] for r in rows]==['old']
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0])['operation']=='producer:discover'
    finally:store.db.close()


def test_interrupt_updates_retry_state_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation,error,problem_code) VALUES ('discover','feed','feed','running',1,'failure','source-connection') RETURNING id").fetchone()[0]
            store.problems.sync_discover(job_id,1)
        store.jobs.interrupted({'feed'})
        row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()
        assert json.loads(row[0])['state']=='current'
    finally:store.db.close()


def test_deleting_feed_makes_discovery_problem_irrelevant_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','feed','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()[0]
        store.remove_feed('feed')
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_restart_finishes_stale_legacy_discovery_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='feed')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:feed:discover','background',%s,1)",(json.dumps(body),))
        store.db.close(); store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_discover_sync_ignores_unrelated_jobs_and_problems(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','feed','feed','failed','failure','source-connection') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'discover','foreign-'||n,'foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            body=json.dumps(dict(code='other',operation='producer:fetch',objects=[dict(kind='feed',id='feed')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p'||n,'job:other:'||n,'background',%s,1 FROM generate_series(1,10000) n",(body,))
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        with store.db:store.problems.sync_discover(job_id,1)
        store.db.set_trace_callback(None)
        for query in queries:
            if query.startswith("SELECT * FROM jobs WHERE kind='discover'"):
                plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(['feed'],0)).fetchone()[0][0]
                assert scan_rows(plan['Plan'])<1000,plan
            if query.startswith("SELECT identity,body FROM reader_problems WHERE finished IS NULL"):
                plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(json.dumps([dict(kind='feed',id='feed')]),)).fetchone()[0][0]
                assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_status_query_does_not_scan_managed_discover_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'discover','foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        store.problems.candidates(1)
        store.db.set_trace_callback(None)
        query=next(q for q in queries if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_recovery_resumes_after_batch_and_includes_nonpositive_job_ids(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT 'f-'||n,'f-'||n,'Feed','default','rss','rss','connected',ARRAY[]::text[] FROM generate_series(-1,100) n")
            store.db.execute("INSERT INTO jobs(id,kind,item,feed_id,status,error,problem_code) SELECT n,'discover','f-'||n,'f-'||n,'failed','failure','source-connection' FROM generate_series(-1,100) n")
        store.db.close()
        def fault(point):
            if point=='discover_problem_recovery_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):
            Store(tmp_path,fault,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()[0]==102
        assert store.db.execute("SELECT 1 FROM state WHERE key='discover-problems-recovered-v1'").fetchone()
    finally:store.db.close()


def test_default_discovery_empty_item_has_problem_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','',1),1,'failure',code='source-connection')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:demo:discover' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_discover_owner_is_feed_even_when_id_matches_other_publication(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('same','same','Feed','default','rss','rss','connected',ARRAY[]::text[]),('other','other','Other','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('same')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','same',true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','same','same','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','same',1),1,'failure',code='source-connection')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:same:discover' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_recovery_does_not_scan_unrelated_problems(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='other',operation='producer:fetch',objects=[]))
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p'||n,'job:other:'||n,'background',%s,1 FROM generate_series(1,10000) n",(body,))
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        store.problems.recover_discover_problems()
        store.db.set_trace_callback(None)
        query=next(q for q in queries if q.startswith('SELECT id,identity,body FROM reader_problems WHERE finished IS NULL'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,('',)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_recovery_reuses_problem_previously_attributed_to_publication_owner(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='other')],action=None,operation='producer:discover',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('same','same','Feed','default','rss','rss','connected',ARRAY[]::text[]),('other','other','Other','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('same')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','same',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','same','same','failed','failure','source-connection')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:other:discover','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:same:discover' AND finished IS NULL").fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()


def test_discover_owner_uses_source_item_instead_of_stale_attempt_feed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[]),('old','old','Old','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('discover','feed','old','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_discover_collision_with_another_discover_source_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','feed','running',1) RETURNING id").fetchone()[0]
            body=dict(code='source-connection',operation='producer:discover',objects=[dict(kind='feed',id='other')],title='other')
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('other','job:source-connection:feed:discover','background',%s,1)",(json.dumps(body),))
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='other'").fetchone()[0])==body
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()[0]==2
    finally:store.db.close()


def test_unidentifiable_legacy_discover_problem_survives_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:feed:discover','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        store.problems.snapshot(2)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is None
    finally:store.db.close()


def test_malformed_colliding_problem_does_not_block_source_failure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','feed','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:feed:discover','background','[]',1)")
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0]=='[]'
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_http_reports_recovered_source_failure_without_get_writes(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    import psycopg
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','failed','failure','source-connection')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']==before[0] for p in response.json()['problems']['current'])
        etag=response.headers.get('etag')
        if etag: assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:source-connection:feed:discover' AND finished IS NULL").fetchone()==before


def test_recovery_closes_old_discovery_code_when_same_source_has_new_failure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='feed')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','failed','other failure','other-code')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:feed:discover','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_recovery_suppresses_legacy_discovery_error_when_profile_is_unavailable(tmp_path,postgres_url):
    from tests.support.read_problem import setup
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='identity-unconfirmed',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='feed')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        setup(store)
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','failed','failure','identity-unconfirmed')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:identity-unconfirmed:feed:discover:feed','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='resolved'
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_discover_collision_fallback_stays_stable_after_restart_and_foreign_closure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','feed','running',1) RETURNING id").fetchone()[0]
            foreign=dict(code='source-connection',operation='producer:fetch',objects=[dict(kind='feed',id='feed')],title='other')
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('other','job:source-connection:feed:discover','background',%s,1)",(json.dumps(foreign),))
        store.jobs.retry(Job(job_id,'discover','feed',1),1,'failure',code='source-connection')
        original=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()
        with store.db:store.db.execute("UPDATE reader_problems SET finished=1,outcome='resolved' WHERE id='other'")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        with store.db:store.problems.sync_discover(job_id,2)
        after=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(original)
    finally:store.db.close()


def test_discover_guard_does_not_preserve_unrelated_finished_job(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='other',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='discover')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:other:feed:fetch:discover','background',%s,1)",(json.dumps(body),))
        store.problems.snapshot(2)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is not None
    finally:store.db.close()


def test_review_recovery_preserves_legacy_status_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='other')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('same','same','Feed','default','rss','rss','connected',ARRAY[]::text[]),('other','other','Other','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('same')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','same',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','same','same','failed','failure','source-connection')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-connection:other:discover','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT finished,outcome FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None, (row['finished'],row['outcome'])
    finally:store.db.close()


def test_viewed_publication_id_does_not_cancel_discover_job(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('same','same','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('same',1)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover','same','running',1) RETURNING id").fetchone()[0]
            store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job_id,)).fetchone()[0]=='running'
    finally:store.db.close()


def test_review_shared_legacy_owner_not_stolen_by_first_discover(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='other')],action=None,operation='producer:discover',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT n,n,n,'default','rss','rss','connected',ARRAY[]::text[] FROM unnest(ARRAY['a','b','other']) n")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','a',true),('other','b',true)")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','a','a','failed','failure','source-connection'),('discover','b','b','failed','failure','source-connection') RETURNING id").fetchall()
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('shared','job:source-connection:other:discover','background',%s,1)",(json.dumps(body),))
            store.problems.sync_discover(jobs[0][0],2)
        original=store.db.execute("SELECT identity,finished FROM reader_problems WHERE id='shared'").fetchone()
        assert original['identity']=='job:source-connection:other:discover' and original['finished'] is None
    finally:store.db.close()

def test_review_recovery_batches_legacy_lookups(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'legacy-'||n,'job:work-discover:feed-'||n||':discover:feed-'||n,'background',
                jsonb_build_object('code','work-discover','section','object','title','T','consequence','C','next_step','N','state','current','operation','producer:discover','objects',jsonb_build_array(jsonb_build_object('kind','feed','id','feed-'||n)))::text,1 FROM generate_series(1,1000) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_discover_problems()
        finally:store.db.set_trace_callback(None)
        queries=[q for q in sql if q.startswith('SELECT 1 FROM jobs WHERE kind=')]
        assert len(queries)<20,len(queries)
    finally:store.db.close()


def test_recovery_preserves_shared_legacy_occurrence_until_other_discover_resolved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='other')],action=None,operation='producer:discover',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT n,n,n,'default','rss','rss','connected',ARRAY[]::text[] FROM unnest(ARRAY['a','b','other']) n")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','a',true),('other','b',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','a','a','failed','failure','source-connection'),('discover','b','b','failed','failure','source-connection')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('shared','job:source-connection:other:discover','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT identity,finished FROM reader_problems WHERE id='shared'").fetchone()
        assert row['identity']=='job:source-connection:other:discover' and row['finished'] is None
    finally:store.db.close()


def test_shared_legacy_occurrence_closes_when_last_discover_finishes(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='other')],action=None,operation='producer:discover',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT n,n,n,'default','rss','rss','connected',ARRAY[]::text[] FROM unnest(ARRAY['a','b','other']) n")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','a',true),('other','b',true)")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation,error,problem_code) VALUES ('discover','a','a','running',1,'failure','source-connection'),('discover','b','b','running',1,'failure','source-connection') RETURNING id,item").fetchall()
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('shared','job:source-connection:other:discover','background',%s,1)",(json.dumps(body),))
            for row in jobs:store.problems.sync_discover(row['id'],1)
        with transaction(store.db):store.jobs.finish(Job(jobs[0]['id'],'discover',jobs[0]['item'],1))
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='shared'").fetchone()[0] is None
        with transaction(store.db):store.jobs.finish(Job(jobs[1]['id'],'discover',jobs[1]['item'],1))
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='shared'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_shared_legacy_closes_after_suppressed_discover_jobs_finish(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='identity-unconfirmed',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='other')],action=None,operation='producer:discover',state='current',next_at=None)
    profile=dict(code='identity-unconfirmed',section='action',title='profile',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='a'),dict(kind='feed',id='b')],action=None,operation='producer:profile',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT n,n,n,'default','telegram','telegram','connected',ARRAY[]::text[] FROM unnest(ARRAY['a','b','other']) n")
            store.db.execute("INSERT INTO telegram_bindings(feed,binding,indexed_role) VALUES ('a','{\"kind\":\"client\",\"role\":\"personal\"}','personal'),('b','{\"kind\":\"client\",\"role\":\"personal\"}','personal')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('profile','profile:personal','background',%s,1)",(json.dumps(profile),))
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','a',true),('other','b',true)")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation,error,problem_code) VALUES ('discover','a','a','running',1,'failure','identity-unconfirmed'),('discover','b','b','running',1,'failure','identity-unconfirmed') RETURNING id,item").fetchall()
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('shared','job:identity-unconfirmed:other:discover:a','background',%s,1)",(json.dumps(body),))
            for row in jobs:store.problems.sync_discover(row['id'],1)
        with transaction(store.db):store.jobs.finish(Job(jobs[0]['id'],'discover',jobs[0]['item'],1))
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='shared'").fetchone()[0] is not None
    finally:store.db.close()


def test_review_all_linked_legacy_owners_finish(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT n,n,n,'default','rss','rss','connected',ARRAY[]::text[] FROM unnest(ARRAY['a','x','y']) n")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('a',0),('root-x',0),('root-y',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','a'),('root-y','a')")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation,error,problem_code) VALUES ('discover','a','a','running',1,'failure','source-connection') RETURNING id").fetchone()[0]
            for feed in ('x','y'):
                body=dict(code='source-connection',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='feed',id=feed)],action=None,operation='producer:discover',state='current',next_at=None)
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",('legacy-'+feed,'job:source-connection:'+feed+':discover',json.dumps(body)))
        store.problems.recover_discover_problems()
        with transaction(store.db):store.jobs.finish(Job(job,'discover','a',1))
        assert store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND id IN ('legacy-x','legacy-y')").fetchall()==[]
    finally:store.db.close()

def test_review_null_item_legacy_job_does_not_block_recovery(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover',NULL,'failed','failure','custom-code')")
        store.problems.recover_discover_problems()
    finally:store.db.close()

def test_review_recovery_batches_active_discover_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'discover','f-'||n,'f-'||n,'failed','failure','source-connection' FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_discover_problems()
        finally:store.db.set_trace_callback(None)
        calls=[q for q in sql if q.startswith("SELECT * FROM jobs WHERE id=")]
        assert len(calls)<20,len(calls)
    finally:store.db.close()


def test_review_fast_batch_duplicate_canonical_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','c','c','failed','failure','source-a:b'),('discover','b:c','b:c','failed','failure','source-a')")
        store.problems.recover_discover_problems()
        rows=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:discover'").fetchall()
        assert len(rows)==2
    finally:store.db.close()

def test_review_null_item_failure_remains_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover',NULL,'failed','failure','custom-code')")
        store.problems.recover_discover_problems()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='custom-code'").fetchone()
    finally:store.db.close()


def test_batch_identity_collision_keeps_second_problem_after_first_finished(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation,error,problem_code) VALUES ('discover','c','c','running',1,'failure','source-a:b'),('discover','b:c','b:c','running',1,'failure','source-a') RETURNING id,item").fetchall()
        store.problems.recover_discover_problems()
        with transaction(store.db):store.jobs.finish(Job(jobs[0]['id'],'discover',jobs[0]['item'],1))
        rows=store.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:discover'").fetchall()
        assert len(rows)==1 and json.loads(rows[0][0])['code']=='source-a'
    finally:store.db.close()


def test_review_mixed_batch_keeps_recovery_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='discover-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'discover','f-'||n,'f-'||n,'failed','failure','source-connection' FROM generate_series(1,1000) n")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'existing-'||n,'job:source-connection:f-'||n||':discover','background',
                jsonb_build_object('code','source-connection','section','object','title','T','consequence','C','next_step','N','state','current','operation','producer:discover','objects',jsonb_build_array(jsonb_build_object('kind','feed','id','f-'||n)))::text,1 FROM generate_series(1,1000,100) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_discover_problems()
        finally:store.db.set_trace_callback(None)
        calls=[q for q in sql if q.startswith("SELECT * FROM jobs WHERE id=")]
        print(f'mixed recovery: {len(calls)} targeted job lookups; {len(sql)} total statements')
        assert len(calls)<100,len(calls)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:discover'").fetchone()[0]==1000
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'existing-%%' AND finished IS NULL").fetchone()[0]==10
    finally:store.db.close()
