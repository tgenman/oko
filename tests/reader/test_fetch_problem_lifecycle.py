import json
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_fetch_failure_retry_and_finish_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','article','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'fetch','article',1),1,'failure',code='article-limit')
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
        assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
        with store.db: store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        job=store.jobs.next_job(1,feeds=['feed'])
        assert job and job.id==job_id
        assert json.loads(store.db.execute('SELECT body FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0])['state']=='retrying'
        with transaction(store.db):store.jobs.finish(job)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_fetch_owner_change_preserves_occurrence(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            prior=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()[0]
            before=managed_candidates(store,job,2)
            store.db.execute("UPDATE feed_publications SET is_primary=false WHERE feed_id='a' AND publication_id='article'")
            store.db.execute("UPDATE feed_publications SET is_primary=true WHERE feed_id='b' AND publication_id='article'")
            store.problems.sync_fetch(job,before,2)
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
        assert after and after[0]==prior
    finally:store.db.close()


def test_fetch_failure_is_recovered_at_startup(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_cancelled_fetch_closes_occurrence_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','article','running',1,'failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job_id,[],1)
        with transaction(store.db):store.jobs.cancel(Job(job_id,'fetch','article',1))
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_removing_feed_makes_fetch_occurrence_irrelevant(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job_id,[],1)
        store.remove_feed('feed')
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_removing_primary_feed_reassigns_fetch_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job_id,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
        assert after and after[0]==before
    finally:store.db.close()


def test_owner_transfer_and_problem_change_rollback_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job_id,[],1)
            before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('a')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        after=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
        assert store.db.execute("SELECT is_primary FROM feed_publications WHERE feed_id='a' AND publication_id='article'").fetchone()[0]
    finally:store.db.close()


def test_adding_link_updates_fetch_problem_within_graph_transaction(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey, ExternalReference, Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('root','rss','root','https://example.test/root'),('article','telegraph','article','https://example.test/article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','root',true),('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()[0]
            effects=CollectionEffects()
            assert publication_links.sync(store,'root',(ExternalReference(ExternalKey(Platform.telegraph,'article'),'https://example.test/article'),),effects)
            store.jobs.apply_collection(effects)
            row=store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(old,)).fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='root')]
    finally:store.db.close()


def test_restart_recovers_legacy_fetch_occurrence_without_losing_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    legacy=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','article','a','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:a:fetch:article','background',%s,1)",(json.dumps(legacy),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()


def test_manual_fetch_retry_updates_occurrence_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()[0]
            store.jobs.enqueue_fetch('article')
            row=store.db.execute('SELECT body FROM reader_problems WHERE id=%s AND finished IS NULL',(before,)).fetchone()
            assert row and json.loads(row[0])['state']=='retrying'
    finally:store.db.close()


def test_restart_closes_stale_legacy_fetch_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    legacy=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:article','background',%s,1)",(json.dumps(legacy),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_linked_fetch_failure_updates_all_owners_and_resolves_together(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-x'),('root-y'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','article','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'fetch','article',1),1,'failure',code='article-limit')
        rows=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL ORDER BY identity",(str(job_id),)).fetchall()
        assert [row[0] for row in rows]==['job:article-limit:x:fetch:article','job:article-limit:y:fetch:article']
        with store.db:store.db.execute('UPDATE jobs SET status=\'running\' WHERE id=%s',(job_id,))
        with transaction(store.db):store.jobs.finish(Job(job_id,'fetch','article',1))
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND outcome='resolved'",(str(job_id),)).fetchone()[0]==2
    finally:store.db.close()


def test_status_does_not_rewrite_managed_fetch_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
        assert not [c for c in store.problems.candidates(2) if c.get('code')=='article-limit']
        store.problems.snapshot(2)
        after=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_ambiguous_legacy_fetch_record_survives_recovery_and_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:item:read:part','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        store.problems.snapshot(2)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is None
    finally:store.db.close()


def test_unidentifiable_legacy_fetch_record_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:article','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        store.problems.snapshot(2)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is None
    finally:store.db.close()


def test_fetch_collision_with_other_job_preserves_both_occurrences(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    foreign=dict(code='article-limit',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='foreign')],action=None,operation='producer:theses',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:article-limit:feed:fetch:article','background',%s,1)",(json.dumps(foreign),))
            store.problems.sync_fetch(job,[],1)
            assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
            row=store.db.execute("SELECT identity,id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            assert row and row['identity'].startswith('job:fetch-isolated:')
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        with store.db:
            store.db.execute("UPDATE reader_problems SET finished=2,outcome='resolved' WHERE id='foreign'")
            store.problems.sync_fetch(job,[],3)
        again=store.db.execute("SELECT identity,id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert tuple(again)==tuple(row)
    finally:store.db.close()


def test_recovery_active_fetch_jobs_has_bounded_round_trips(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','f-'||n,'failed','failure','article-limit' FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        print(f'recovery statements {len(sql)}')
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_recovery_batch_isolates_ambiguous_fetch_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('item:read:part')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','item:read:part',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item:read:part','failed','failure','article-limit') RETURNING id").fetchone()[0]
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row[0].startswith('job:fetch-isolated:')
    finally:store.db.close()


def test_status_query_ignores_ten_thousand_managed_fetch_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','article-limit' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_restart_recovers_old_owner_when_job_has_no_owner_snapshot(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:a:fetch:article','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()


def test_restart_merges_legacy_owners_by_first_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]'),('c','c','C','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('c','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('a-new','job:article-limit:a:fetch:article','background',%s,2),('z-old','job:article-limit:b:fetch:article','background',%s,1)",(json.dumps(body),json.dumps(body)))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:c:fetch:article' AND finished IS NULL").fetchone()
        assert row and row[0]=='z-old'
    finally:store.db.close()


def test_recovery_stale_fetch_problems_batches_updates(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'job:article-limit:feed:fetch:item-'||n,'background',jsonb_build_object('code','article-limit','section','object','title','x','consequence','c','next_step','n','operation','producer:fetch','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','item-'||n)))::text,1 FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NOT NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_fetch_recovery_first_page_ignores_unrelated_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'discover','foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.startswith("SELECT id FROM jobs WHERE kind='fetch'"))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_recovery_mixed_fetch_jobs_has_bounded_round_trips(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','f-'||n,'failed','failure','article-limit' FROM generate_series(1,1000) n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:article-limit:f-'||n||':fetch:f-'||n,'background',jsonb_build_object('code','article-limit','section','object','title','x','consequence','c','next_step','n','operation','producer:fetch','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','f-'||n)))::text,1 FROM generate_series(1,1000,100) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        print(f'mixed fetch recovery statements {len(sql)}')
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()[0]==1000
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==10
    finally:store.db.close()


def test_fetch_cancellation_after_source_deletion_is_irrelevant(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE publications SET source_deleted=true WHERE id='article'")
            store.db.execute("UPDATE jobs SET status='cancelled' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,2)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_remove_one_root_preserves_other_linked_fetch_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('root-x','rss','x'),('root-y','rss','y'),('article','telegraph','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:y:fetch:article' AND finished IS NULL").fetchone()[0]
        store.remove_feed('x')
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:y:fetch:article' AND finished IS NULL").fetchone()
        assert after and after[0]==before
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:article-limit:x:fetch:article'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_owner_transfer_and_reenqueue_preserves_existing_occurrence(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:a:fetch:article' AND finished IS NULL").fetchone()[0]
            effects=CollectionEffects();publication_links.capture_fetch_problems(store,effects,{'article'})
            store.db.execute("UPDATE feed_publications SET is_primary=false WHERE feed_id='a' AND publication_id='article'")
            store.db.execute("UPDATE feed_publications SET is_primary=true WHERE feed_id='b' AND publication_id='article'")
            effects.fetch['article']=True
            store.jobs.apply_collection(effects)
            after=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
            assert after and after[0]==before
    finally:store.db.close()


def test_http_status_reads_recovered_fetch_without_mutation(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
        db.execute("INSERT INTO publications(id) VALUES ('article')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']==before[0] for p in response.json()['problems']['current'])
        etag=response.headers.get('etag')
        if etag:assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()==before


def test_recovery_existing_fetch_jobs_has_bounded_round_trips(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','f-'||n,'failed','failure','article-limit' FROM generate_series(1,1000) n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:article-limit:f-'||n||':fetch:f-'||n,'background',jsonb_build_object('code','article-limit','section','object','title','x','consequence','c','next_step','n','operation','producer:fetch','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','f-'||n)))::text,1 FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        print(f'existing fetch recovery statements {len(sql)}')
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_recovery_keeps_oldest_occurrence_when_current_identity_exists(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:a:fetch:article','background',%s,1),('new','job:article-limit:b:fetch:article','background',%s,2)",(json.dumps(body),json.dumps(body)))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:b:fetch:article' AND finished IS NULL").fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()


def test_regular_discovery_page_does_not_query_fetch_problem_per_publication(tmp_path,postgres_url):
    from oko.collection.contracts import Publication
    from tests.support.source import publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        page=[publication_packet(Publication(str(n),f'https://example.test/{n}',n)) for n in range(50)]
        statements=[];store.db.set_trace_callback(statements.append)
        try:
            with transaction(store.db):
                effects=store.collect_discovery(page,'{}')
                store.jobs.apply_collection(effects)
        finally:store.db.set_trace_callback(None)
        problem_queries=[q for q in statements if q.lstrip().startswith('SELECT j.id,j.kind') or q.startswith('SELECT id FROM jobs j WHERE kind=\'fetch\' AND item=ANY')]
        assert len(problem_queries)<5,len(problem_queries)
    finally:store.db.close()


def test_regular_discovery_page_cost_ignores_unrelated_fetch_errors_and_links(tmp_path,postgres_url):
    from oko.collection.contracts import Publication
    from tests.support.source import publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        page=[publication_packet(Publication(str(n),f'https://example.test/{n}',n)) for n in range(50)]
        def accept():
            with transaction(store.db):
                effects=store.collect_discovery(page,'{}')
                store.jobs.apply_collection(effects)
        accept()
        def measured():
            sql=[];store.db.set_trace_callback(sql.append)
            try:accept()
            finally:store.db.set_trace_callback(None)
            return len(sql)
        before=measured()
        with store.db:
            store.db.execute("INSERT INTO publications(id) SELECT 'unrelated-root-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO publications(id) SELECT 'unrelated-child-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'unrelated-root-'||n,'unrelated-child-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','unrelated-child-'||n,'failed','failure','article-limit' FROM generate_series(1,10000) n")
        after=measured()
        print(f'discovery 50: {before} SQL without unrelated history, {after} SQL with 10k unrelated links/jobs')
        assert after<=before+3,(before,after)
    finally:store.db.close()


def test_restart_viewed_fetch_problem_is_irrelevant(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('article',1)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:article','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_adapter_change_closes_old_fetch_error_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            store.jobs.change_adapter('feed')
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_cancel_history_updates_fetch_failure_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO history_members(feed,item) VALUES ('feed','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','pending','failure','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            store.jobs.cancel_history('feed')
        row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:article-limit:feed:fetch:article' AND finished IS NULL").fetchone()
        assert row and json.loads(row[0])['next_step']=='Повторите загрузку вручную.'
    finally:store.db.close()


def test_fetch_record_recovery_ignores_unrelated_problems(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            body=json.dumps(dict(code='x',operation='producer:read',objects=[]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'job:x:'||n,'background',%s,1 FROM generate_series(1,10000) n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.startswith('SELECT id,identity,body,created FROM reader_problems'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(float('-inf'),'',)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()
