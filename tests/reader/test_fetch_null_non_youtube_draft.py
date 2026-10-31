from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_recover_non_youtube_null_without_get_writes(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','article','f','failed','failure',NULL) RETURNING id").fetchone()[0]
        store.problems.recover_fetch_null()
        before=store.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert before and store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        store.problems.snapshot(store.wall_clock())
        assert tuple(before)==tuple(store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(before['id'],)).fetchone())
    finally:store.db.close()


def test_null_explicit_null_preserves_occurrence(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import previous_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure',NULL) RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        def occurrence():return store.db.execute("SELECT id,body::jsonb->>'code' AS code FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        old=occurrence();assert old and old['code']=='work-fetch'
        with transaction(store.db):
            previous=previous_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='article-limit' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,2)
        assert tuple(occurrence())==(old['id'],'article-limit')
        with transaction(store.db):
            previous=previous_candidates(store,job,3)
            store.db.execute("UPDATE jobs SET problem_code=NULL WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,3)
        assert tuple(occurrence())==(old['id'],'work-fetch')
    finally:store.db.close()


def test_youtube_null_projects_for_event_ownership(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('y','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure',NULL) RETURNING id").fetchone()[0]
        assert [row['fetch_owner'] for row in managed_candidates(store,job,1)]==['y']
    finally:store.db.close()


def test_null_recovery_rolls_back_and_resumes(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
        original=store.problems.recover_fetch_batch
        def fail(rows,now,**kwargs):
            original(rows,now,**kwargs)
            raise RuntimeError('rollback')
        monkeypatch.setattr(store.problems,'recover_fetch_batch',fail)
        with pytest.raises(RuntimeError,match='rollback'):store.problems.recover_fetch_null()
        assert not store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        monkeypatch.setattr(store.problems,'recover_fetch_batch',original)
        store.problems.recover_fetch_null()
        assert store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()


def test_mixed_youtube_roots_project_both_owners(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
        assert {row['fetch_owner'] for row in managed_candidates(store,job,1)}=={'r','y'}
    finally:store.db.close()


def test_recovery_does_not_scan_unrelated_ten_thousand(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','explicit' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure',NULL)")
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_fetch_null()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith("SELECT id FROM jobs WHERE kind='fetch'") and 'problem_code IS NULL' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_actual_finish_closes_null_problem_in_same_transaction(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','article','running',1,'failure',NULL) RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            problem=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(store.db):store.jobs.finish(Job(job,'fetch','article',1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(problem,)).fetchone()[0] is not None
    finally:store.db.close()


def test_recovery_preserves_unambiguous_legacy_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:work-fetch:article:fetch:article','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_null()
        current=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert current and current[0]=='legacy'
    finally:store.db.close()


def test_remove_one_root_preserves_other_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('root-x','rss','x'),('root-y','rss','y'),('article','telegraph','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure',NULL) RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:work-fetch:y:fetch:article' AND finished IS NULL").fetchone()[0]
        store.remove_feed('x')
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:work-fetch:y:fetch:article' AND finished IS NULL").fetchone()
        assert after and after[0]==before
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:work-fetch:x:fetch:article'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_status_does_not_scan_ten_thousand_owned_null_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,fetch_null_owned) SELECT 'fetch','foreign-'||n,'failed','failure',NULL,true FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.candidates(store.wall_clock())
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT jobs . *'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_root_removal_rolls_back_null_problem_and_graph(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('root','rss','root'),('article','telegraph','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        before=[tuple(r) for r in store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s",(str(job),))]
        original=store.problems.sync_fetch
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('rollback')
        monkeypatch.setattr(store.problems,'sync_fetch',fail)
        with pytest.raises(RuntimeError,match='rollback'):store.remove_feed('f')
        assert [tuple(r) for r in store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s",(str(job),))]==before
        assert store.db.execute("SELECT connection FROM feeds WHERE id='f'").fetchone()[0]=='connected'
    finally:store.db.close()


def test_removing_youtube_root_transfers_remaining_null_to_events(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','youtube','b'),('article','telegraph','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
        store.problems.snapshot(store.wall_clock())
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:work-fetch:r:fetch:article' AND finished IS NULL").fetchone()
        assert old
        store.remove_feed('y')
        current=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:work-fetch:r:fetch:article' AND finished IS NULL").fetchone()
        assert current and current[0]==old[0]
        assert store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()


def test_removing_rss_root_leaves_youtube_event_owned(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','youtube','b'),('article','telegraph','article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
        store.remove_feed('r')
        assert store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:work-fetch:y:fetch:article' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_adding_youtube_root_preserves_event_ownership_atomically(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('article','telegraph','article','https://example.test/article'),('root','youtube','root','https://example.test/root'),('rssroot','rss','rssroot','https://example.test/rssroot')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','rssroot',true),('y','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('rssroot','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:work-fetch:r:fetch:article' AND finished IS NULL").fetchone()[0]
            effects=CollectionEffects()
            assert publication_links.sync(store,'root',(ExternalReference(ExternalKey(Platform.telegraph,'article'),'https://example.test/article'),),effects)
            store.jobs.apply_collection(effects)
            assert store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:work-fetch:r:fetch:article' AND finished IS NULL").fetchone()
        assert store.db.execute('SELECT id FROM reader_problems WHERE id=%s AND finished IS NULL',(old,)).fetchone()
    finally:store.db.close()


def test_recovery_preserves_foreign_collision_and_allocates_own_problem(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='unrelated',operation='producer:delete',objects=[dict(kind='material',id='other')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-fetch:article:fetch:article','background',%s,1)",(body,))
        store.problems.recover_fetch_null()
        assert store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()[0]==body
        row=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row['id']!='foreign' and row['identity'].startswith('job:fetch-isolated:')
    finally:store.db.close()


def test_conflicting_isolated_records_are_bounded_and_preserved(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','err',NULL) RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:p:fetch:p',fetch_implicit_code=True,objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:fetch-isolated:'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):store.problems.sync_fetch(job,[],1)
        finally:store.db.set_trace_callback(None)
        assert not store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL").fetchone()[0]==10000
        query=next(q for q in trace if q.startswith('SELECT r.*,target.canonical') and "fetch_job_identity" in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['job:work-fetch:p:fetch:p'],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<500
    finally:store.db.close()


def test_recovery_leaves_oversized_isolated_group_untouched(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:p:fetch:p',fetch_implicit_code=True,objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:fetch-isolated:'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
        store.problems.recover_fetch_null()
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished IS NULL').fetchone()[0]==10000
        assert not store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()


def test_oversized_legacy_group_remains_untouched_during_recovery(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-fetch',section='object',title='Failed',consequence='Saved',next_step='Retry',action=None,state='current',next_at=None,operation='producer:status',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-fetch:foreign-'||n||':fetch:p','background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('canonical','job:work-fetch:p:fetch:p','background',%s,1)",(body,))
        import time
        trace=[];store.db.set_trace_callback(trace.append)
        started=time.perf_counter()
        try:store.problems.recover_fetch_null()
        finally:store.db.set_trace_callback(None)
        print('protected 10001 occurrences SQL:',len(trace),'seconds:',round(time.perf_counter()-started,3))
        assert not store.db.execute('SELECT fetch_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        store.problems.snapshot(store.wall_clock())
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='canonical'").fetchone()[0]==body
        with transaction(store.db):store.db.execute('DELETE FROM jobs WHERE id=%s',(job,))
        store.problems.snapshot(store.wall_clock())
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished IS NULL').fetchone()[0]==10001
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='canonical'").fetchone()[0]==body
    finally:store.db.close()


def test_foreign_protection_marker_is_not_overwritten(tmp_path,postgres_url):
    import hashlib,json,pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            key='fetch-null-protected:'+hashlib.sha256(b'r-1').hexdigest()
            store.db.execute("INSERT INTO state(key,value) VALUES (%s,'foreign')",(key,))
            body=json.dumps(dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-fetch:foreign-'||n||':fetch:p','background',%s,1 FROM generate_series(1,101)n",(body,))
        with pytest.raises(RuntimeError,match='Conflicting fetch-null protection marker'):
            with transaction(store.db):store.problems.sync_fetch(job,[],1)
        assert store.db.execute('SELECT value FROM state WHERE key=%s',(key,)).fetchone()[0]=='foreign'
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished IS NULL').fetchone()[0]==101
    finally:store.db.close()


def test_ambiguous_null_followed_by_explicit_remains_visible(tmp_path,postgres_url):
    import json
    from oko.storage.fetch_job_problems import previous_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-fetch',section='object',title='Failed',consequence='Saved',next_step='Retry',action=None,state='current',next_at=None,operation='producer:status',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-fetch:foreign-'||n||':fetch:p','background',%s,1 FROM generate_series(1,101)n",(body,))
            store.problems.sync_fetch(job,[],1)
            previous=previous_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='article-limit' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,2)
        assert any(p['code']=='article-limit' for p in store.problems.candidates(3))
        store.problems.snapshot(3)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'r-%%' AND finished IS NULL").fetchone()[0]==101
    finally:store.db.close()


def test_recovery_batches_thousand_independent_null_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-null-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'fetch','article-'||n,'failed','failure' FROM generate_series(1,1000)n")
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_fetch_null()
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM jobs WHERE fetch_null_owned").fetchone()[0]==1000
        print("null recovery 1000 jobs SQL:",len(trace))
        assert len(trace)<400,len(trace)
    finally:store.db.close()


def test_fetch_null_protection_gc_removes_only_proven_stale_and_rolls_back(tmp_path,postgres_url):
    import hashlib,json,pytest
    from oko.storage.fetch_job_problems import cleanup_null_protection
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('active','job:work-fetch:a:fetch:p','background','{}',1,NULL,NULL),('done','job:work-fetch:b:fetch:p','background','{}',1,2,'resolved'),('corrupt','job:work-fetch:c:fetch:p','background','{}',1,2,NULL)")
            for name,identity in [('active','job:work-fetch:a:fetch:p'),('done','job:work-fetch:b:fetch:p'),('corrupt','job:work-fetch:c:fetch:p'),('missing','job:work-fetch:d:fetch:p')]:
                key='fetch-null-protected:'+hashlib.sha256(name.encode()).hexdigest()
                store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-fetch-null-v2',name,identity])))
            store.db.execute("INSERT INTO state(key,value) VALUES ('fetch-null-protected:foreign','foreign')")
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                cleanup_null_protection(store,None)
                raise RuntimeError('rollback')
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==5
        with transaction(store.db):cleanup_null_protection(store,None)
        rows={row['key'] for row in store.db.execute("SELECT key FROM state WHERE key LIKE 'fetch-null-protected:%%'")}
        assert rows=={'fetch-null-protected:foreign',*('fetch-null-protected:'+hashlib.sha256(name.encode()).hexdigest() for name in ('active','corrupt'))}
    finally:store.db.close()


def test_occurrence_protection_does_not_hide_new_youtube_error(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='work-fetch',section='object',title='old',consequence='old',next_step='old',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,reason='old error'))
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','old error') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-fetch:foreign-'||n||':fetch:p','background',%s,1 FROM generate_series(1,101)n",(body,))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('canonical','job:work-fetch:f:fetch:p','background',%s,1)",(body,))
            store.problems.protect_fetch_null_legacy([job])
            store.db.execute('DELETE FROM jobs WHERE id=%s',(job,))
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','new error')")
        current=store.problems.snapshot(100)['current']
        assert any(p.get('reason')=='new error' for p in current)
        assert store.db.execute("SELECT body,finished FROM reader_problems WHERE id='canonical'").fetchone()[0]==body
    finally:store.db.close()


def test_protection_mid_batch_failure_rolls_back_and_retry_succeeds(tmp_path,postgres_url,monkeypatch):
    import json,pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='p')]))
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-fetch:foreign-'||n||':fetch:p','background',%s,1 FROM generate_series(1,1001)n",(body,))
        old_fault=store.fault
        seen=[]
        def fault(name):
            if name=='fetch_null_protection_batch':
                seen.append(name)
                if len(seen)==2:raise RuntimeError('crash')
            return old_fault(name)
        monkeypatch.setattr(store,'fault',fault)
        with pytest.raises(RuntimeError,match='crash'):
            with transaction(store.db):store.problems.sync_fetch(job,[],1)
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==0
        monkeypatch.setattr(store,'fault',old_fault)
        with transaction(store.db):store.problems.sync_fetch(job,[],1)
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==1001
    finally:store.db.close()


def test_fetch_null_protection_gc_page_ignores_ten_thousand_foreign(tmp_path,postgres_url):
    import hashlib,json
    from tests.support.postgres_work import scan_rows
    from oko.storage.fetch_job_problems import cleanup_null_protection
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO state(key,value) SELECT 'foreign-'||n,'untouched' FROM generate_series(1,10000)n")
            for i in range(250):
                item='old-'+str(i);key='fetch-null-protected:'+hashlib.sha256(item.encode()).hexdigest()
                store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-fetch-null-v2',item,'job:work-fetch:x:fetch:p'])))
            store.db._db.execute('ANALYZE state')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):cleanup_null_protection(store,None)
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==150
        query=next(q for q in trace if q.startswith("SELECT key,value FROM state WHERE key COLLATE"))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_fetch_protection_pages_ignore_ten_thousand_foreign(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-fetch:foreign-'||n||':fetch:unrelated-'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):store.problems.protect_fetch_null_legacy([job])
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT id,identity,md5(id) AS page_hash FROM reader_problems WHERE finished IS NULL') and "substring(coalesce" in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('p','p')).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_fetch_null_gc_cursor_rolls_back_with_transaction(tmp_path,postgres_url):
    import hashlib,json,pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        key='fetch-null-protected:'+hashlib.sha256(b'missing').hexdigest()
        with transaction(store.db):store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-fetch-null-v2','missing','job:work-fetch:x:fetch:p'])))
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.read_versions.advance(now=10,wall=10)
                raise RuntimeError('rollback')
        assert store.read_versions._fetch_null_gc_cursor is None
        assert store.db.execute('SELECT 1 FROM state WHERE key=%s',(key,)).fetchone()
    finally:store.db.close()


def test_empty_protection_check_ignores_ten_thousand_unrelated_state(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='work-fetch',section='object',title='old',consequence='old',next_step='old',objects=[],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(store.db):
            store.db.execute("INSERT INTO state(key,value) SELECT 'foreign-'||n,'untouched' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-fetch:x:fetch:p','background',%s,1)",(body,))
            store.db._db.execute('ANALYZE state')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.snapshot(store.wall_clock())
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT *,EXISTS ('))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
        assert not any(q.startswith('SELECT key,value FROM state WHERE key=ANY') for q in trace)
    finally:store.db.close()
