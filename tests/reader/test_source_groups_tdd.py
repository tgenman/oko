import pytest
from tests.support.storage import Store

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_group_projection_matches_current_status_for_single_source(kind,tmp_path,postgres_url):
    from oko.storage.source_groups import job_keys,project
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','failure','source-network') RETURNING id",(kind,item)).fetchone()[0]
        keys=job_keys(store,job)
        assert len(keys)==1
        actual=project(store,next(iter(keys)),kind,1)
        existing=[p for p in store.problems.candidates(1) if p['id'] in keys and p.get('operation')=='producer:'+kind]
        assert len(existing)==1
        actual.pop('_legacy_ids',None);existing[0].pop('_legacy_ids',None)
        assert actual==existing[0]
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_projection_ignores_unrelated_source_jobs(kind,tmp_path,postgres_url):
    from oko.storage.source_groups import project
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT %s,'foreign-'||n,'foreign-'||n,'failed','failure','source-network' FROM generate_series(1,10000)n",(kind,))
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES (%s,'target','target','failed','failure','source-network')",(kind,))
            store.db._db.execute("CREATE INDEX source_group_probe_feed ON jobs(kind,feed_id,problem_code) WHERE problem_code LIKE 'source-%' AND octet_length(problem_code)<=1000 AND coalesce(octet_length(feed_id),0)<=1000")
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:assert project(store,'job:source-network:target:'+kind,kind,1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('WITH source_groups AS MATERIALIZED'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['source-network'],['target'],['target'],kind,kind,kind)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_linked_root_and_code_owner_collision_match_group_members(kind,tmp_path,postgres_url):
    from oko.storage.source_groups import project
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('b:c','b:c','F','default','rss','rss','connected','[]'),('c','c','G','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-a'),('root-b'),('p-a'),('p-b')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b:c','root-a',true),('c','root-b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-a','p-a'),('root-b','p-b')")
            if kind=='download_media':
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m-a','p-a','image','x','file','unavailable'),('m-b','p-b','image','x','file','unavailable')")
            items=('m-a','m-b') if kind=='download_media' else ('p-a','p-b')
            for item,code in zip(items,('source-a','source-a:b')):
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','failure',%s)",(kind,item,code))
        identity='job:source-a:b:c:'+kind
        actual=project(store,identity,kind,1)
        expected=[p for p in store.problems.candidates(1) if p['id']==identity and p.get('operation')=='producer:'+kind]
        assert len(expected)==1
        assert actual is not None
        assert {(obj['kind'],obj['id']) for obj in actual['objects']}=={(obj['kind'],obj['id']) for obj in expected[0]['objects']}
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_job_events_create_and_finish_source_occurrence_without_get(kind,tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,'running',1) RETURNING id",(kind,item)).fetchone()[0]
        store.jobs.retry(Job(job,kind,item,1),1,'failure',code='source-network')
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:f:'+kind,)).fetchone()
        assert row
        with transaction(store.db):store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with transaction(store.db):store.jobs.finish(Job(job,kind,item,1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0] is not None
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_status_read_does_not_rewrite_source_event_record(kind,tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,'running',1) RETURNING id",(kind,item)).fetchone()[0]
        store.jobs.retry(Job(job,kind,item,1),1,'failure',code='source-network')
        before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:f:'+kind,)).fetchone()
        assert before
        store.problems.snapshot(2)
        after=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(before)==tuple(after)
    finally:store.db.close()

def test_status_does_not_scan_owned_source_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,source_event_owned) SELECT 'fetch','p-'||n,'failed','failure','source-network',true FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()

def test_source_subtitles_follow_youtube_pause_without_losing_source_group(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='source-network')
        assert any(p['code']=='source-network' for p in store.problems.snapshot(2)['current'])
        store.youtube.blocked(3,100)
        assert not any(p['code']=='source-network' for p in store.problems.snapshot(4)['current'])
        store.read_versions.advance(wall=1000)
        assert any(p['code']=='source-network' for p in store.problems.snapshot(1000)['current'])
    finally:store.db.close()

def test_source_retry_waits_for_graph_lock_before_updating(tmp_path,postgres_url):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from oko.storage import memberships
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    import psycopg
    other=psycopg.connect(postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        with ThreadPoolExecutor(max_workers=1) as pool:
            with other.transaction():
                other.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))',(0x4F4B4F4752415048,))
                future=pool.submit(store.jobs.retry,Job(job,'fetch','p',1),1,'failure',code='source-network')
                with pytest.raises(TimeoutError):future.result(timeout=0.3)
                assert other.execute('SELECT problem_code FROM jobs WHERE id=%s',(job,)).fetchone()[0] is None
            future.result(timeout=10)
        assert store.db.execute('SELECT problem_code FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='source-network'
    finally:other.close();store.db.close()

def test_source_failure_and_group_rollback_together(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='running'
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_shared_feed_owner_removal_moves_source_group_without_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        before=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        after=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()
        assert after and after[0]==before
    finally:store.db.close()

def test_removing_one_of_two_source_roots_finishes_only_its_group(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('ra','rss','ra'),('rb','rss','rb'),('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','ra',true),('b','rb',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('ra','p'),('rb','p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        a=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        b=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(a,)).fetchone()[0] is not None
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(b,)).fetchone()[0] is None
    finally:store.db.close()

def test_moving_one_group_member_does_not_steal_other_members_history(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p'),('q','rss','q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false),('b','q',true),('a','q',false)")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1),('fetch','q','running',1) RETURNING id,item").fetchall()
        for row in rows:store.jobs.retry(Job(row['id'],'fetch',row['item'],1),1,'failure',code='source-network')
        old_a=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        old_b=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old_a,)).fetchone()[0] is not None
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old_b,)).fetchone()[0] is None
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_claim_and_cancel_update_source_group(kind,tmp_path,postgres_url):
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES (%s,%s,'pending',1,'failure','source-network') RETURNING id",(kind,item)).fetchone()[0]
            running=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        record=store.db.execute("SELECT id,body::jsonb->>'state' AS state FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:f:'+kind,)).fetchone()
        assert record and record['state']=='retrying'
        with transaction(store.db):store.jobs.cancel(running)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(record['id'],)).fetchone()[0] is not None
    finally:store.db.close()

def test_adding_link_updates_source_group_before_get(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('root','rss','root','https://example.test/root'),('article','telegraph','article','https://example.test/article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true),('f','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','article','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','article',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            effects=CollectionEffects()
            assert publication_links.sync(store,'root',(ExternalReference(ExternalKey(Platform.telegraph,'article'),'https://example.test/article'),),effects)
            store.jobs.apply_collection(effects)
        row=store.db.execute('SELECT body FROM reader_problems WHERE id=%s AND finished IS NULL',(old,)).fetchone()
        assert row and json.loads(row[0])['objects']==[dict(kind='material',id='root')]
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_startup_recovers_legacy_source_job_without_get(kind,tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            item='m' if kind=='download_media' else 'f' if kind=='feed-delete' else 'p'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','failure','source-network') RETURNING id",(kind,item)).fetchone()[0]
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:f:'+kind,)).fetchone()
        assert store.db.execute('SELECT source_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()

def test_recovery_projects_large_shared_group_only_once(tmp_path,postgres_url):
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f',id,true FROM publications WHERE id LIKE 'p-%%'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed','failure','source-network' FROM generate_series(1,1000)n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:recover(store)
        finally:store.db.set_trace_callback(None)
        queries=[q for q in sql if q.startswith('WITH source_groups AS MATERIALIZED')]
        assert len(queries)==1,len(queries)
        assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='fetch' AND source_event_owned").fetchone()[0]==1000
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_source_recovery_resumes_after_interrupted_page(tmp_path,postgres_url):
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f',id,true FROM publications WHERE id LIKE 'p-%%'")
            store.db.execute("INSERT INTO jobs(id,kind,item,status,error,problem_code) SELECT n-1,'fetch','p-'||n,'failed','failure','source-network' FROM generate_series(1,101)n")
        original=store.fault
        def fail(point):
            if point=='source_problem_recovery_batch':raise RuntimeError('interrupted')
        store.fault=fail
        with pytest.raises(RuntimeError,match='interrupted'):recover(store)
        store.fault=original
        recover(store)
        assert store.db.execute("SELECT count(*) FROM jobs WHERE source_event_owned AND kind='fetch'").fetchone()[0]==101
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()[0]==1
        assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'source-recovery-seen:%%'").fetchone()
    finally:store.db.close()

def test_ambiguous_legacy_source_code_is_not_overwritten(tmp_path,postgres_url):
    import json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('b:c','b:c','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b:c','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            legacy=dict(code='source-a:b',operation='producer:status',objects=[dict(kind='material',id='p')],section='object',title='legacy',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:source-a:b:c:fetch','background',%s,1)",(json.dumps(legacy),))
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-a')
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='legacy'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==legacy
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-a:b:c:fetch' AND id!='legacy' AND finished IS NULL").fetchone()
        store.problems.snapshot(2)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='legacy'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==legacy
    finally:store.db.close()

def test_ambiguous_long_identity_falls_back_without_aborting_job(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        item='x:'+':'.join(['n']*102)
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch',%s,'running',1) RETURNING id",(item,)).fetchone()[0]
        store.jobs.retry(Job(job,'fetch',item,1),1,'failure',code='source-network')
        row=store.db.execute('SELECT source_event_owned,status FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row['source_event_owned'] is False and row['status']=='pending'
        assert any(p['code']=='source-network' for p in store.problems.candidates(2))
    finally:store.db.close()

def test_youtube_pause_batches_one_source_group_and_rolls_back(tmp_path,postgres_url):
    from oko.storage.source_groups import recover
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f',id,true FROM publications WHERE id LIKE 'p-%%'")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','file','unavailable' FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','m-'||n,'pending','failure','source-network' FROM generate_series(1,250)n")
        recover(store)
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with pytest.raises(RuntimeError,match='rollback'):
                with transaction(store.db):
                    store.youtube.blocked(1,100)
                    assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()
                    raise RuntimeError('rollback')
        finally:store.db.set_trace_callback(None)
        assert len([q for q in trace if q.startswith('WITH source_groups AS MATERIALIZED')])<=2
        after=store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone()
        assert tuple(before)==tuple(after)
        assert store.youtube._limits()['blocked_until']==0
        assert store.db.execute("SELECT 1 FROM state WHERE key LIKE 'source-youtube-timed:%%'").fetchone()
    finally:store.db.close()

@pytest.mark.parametrize('kind',['filter','theses'])
def test_reconcile_cancel_finishes_source_group_without_get(kind,tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'p','running',1) RETURNING id",(kind,)).fetchone()[0]
        store.jobs.retry(Job(job,kind,'p',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:p:'+kind,)).fetchone()[0]
        with transaction(store.db):store.jobs.reconcile(kind,[('p',None,False,'')])
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

def test_cleanup_finishes_orphan_source_media_without_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','missing','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','missing',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:missing:download_media' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses','delete','feed-delete'])
def test_projection_ignores_unrelated_memberships(kind,tmp_path,postgres_url):
    from oko.storage.source_groups import project
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id) VALUES ('foreign'),('target')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'foreign',id,true FROM publications WHERE id LIKE 'p-%%'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT %s,id,'failed','failure','source-network' FROM publications WHERE id LIKE 'p-%%'",(kind,))
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES (%s,'target','target','failed','failure','source-network')",(kind,))
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE feed_publications');store.db._db.execute('ANALYZE publications')
        trace=[];store.db.set_trace_callback(trace.append)
        try:assert project(store,'job:source-network:target:'+kind,kind,1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('WITH source_groups AS MATERIALIZED'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['source-network'],['target'],['target'],kind,kind,kind)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()

def test_owner_change_preserves_group_id_when_new_key_is_foreign(tmp_path,postgres_url):
    import json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            foreign=dict(code='source-network',operation='producer:filter',objects=[dict(kind='material',id='p')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:source-network:b:fetch','background',%s,1)",(json.dumps(foreign),))
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        before=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        after=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()
        assert after and after[0]==before
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
    finally:store.db.close()

def test_youtube_source_timer_ignores_foreign_subtitles(tmp_path,postgres_url):
    from oko.storage.source_groups import sync_youtube_timer
    from oko.storage.transaction import transaction
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','file','unavailable' FROM generate_series(1,10000)n")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m-1','pending','failure','source-network') RETURNING id").fetchone()[0]
            from oko.storage import memberships,source_groups
            memberships.lock_graph(store.db)
            source_groups.sync_jobs(store,[job],1)
            store.db._db.execute('ANALYZE media_files');store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):sync_youtube_timer(store,1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT key,value FROM state WHERE key LIKE'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,()).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,scan_rows(plan)
        group_query=next(q for q in trace if q.startswith('WITH source_groups AS MATERIALIZED'))
        group_plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+group_query,(['source-network'],['m-1'],['m-1'],'download_media','download_media','download_media')).fetchone()[0][0]['Plan']
        def walk(node):
            yield node
            for child in node.get('Plans',[]):yield from walk(child)
        print([(n.get('Node Type'),n.get('Relation Name'),n.get('Actual Rows'),n.get('Rows Removed by Filter'),n.get('Actual Loops')) for n in walk(group_plan)])
        assert scan_rows(group_plan)<1000,scan_rows(group_plan)
    finally:store.db.close()

def test_youtube_completed_immediately_reopens_source_group(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    clock=[1.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='source-network')
        clock[0]=2;store.youtube.blocked(2,100)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()
        clock[0]=3;store.youtube.completed()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()
    finally:store.db.close()

@pytest.mark.parametrize('kind',['filter','theses'])
def test_invalidation_finishes_source_group_without_get(kind,tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES (%s,'p','running',1,'{\"input_revision\":\"old\"}') RETURNING id",(kind,)).fetchone()[0]
        store.jobs.retry(Job(job,kind,'p',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:p:'+kind,)).fetchone()[0]
        with transaction(store.db):store.jobs.invalidate_processing()
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

@pytest.mark.parametrize('kind',['filter','theses'])
def test_manual_retry_updates_source_group_without_get(kind,tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,input_snapshot) VALUES (%s,'p','running',1,'{\"signature\":\"s\"}') RETURNING id",(kind,)).fetchone()[0]
        store.jobs.retry(Job(job,kind,'p',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:p:'+kind,)).fetchone()[0]
        with transaction(store.db):assert store.jobs.retry_processing('p',kind)
        row=store.db.execute("SELECT body::jsonb->>'state' AS state,body::jsonb->>'reason' AS reason FROM reader_problems WHERE id=%s AND finished IS NULL",(old,)).fetchone()
        assert row and row['state']=='retrying' and row['reason'] is None
    finally:store.db.close()

def test_manual_retry_media_finishes_source_group_without_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='source-network',terminal=True)
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:m:download_media' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):store.jobs.retry_media('p')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

def test_viewing_material_finishes_source_group_without_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()[0]
        store.viewed('p')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

def test_finishing_unproven_legacy_source_job_preserves_old_occurrence(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','p','running',1,'failure','source-network') RETURNING id").fetchone()[0]
        old=next(p['id'] for p in store.problems.snapshot(1)['current'] if p['code']=='source-network')
        with transaction(store.db):store.jobs.finish(Job(job,'fetch','p',1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
    finally:store.db.close()

def test_finish_feed_deletion_closes_source_group_before_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','deleting','[]')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete','f','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'feed-delete','f',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:feed-delete' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
            store.jobs.finish_feed_deletion(Job(job,'feed-delete','f',1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

@pytest.mark.parametrize('changed_identity',[False,True])
def test_media_accept_finishes_source_group_before_get(changed_identity,tmp_path,postgres_url):
    import json,uuid
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4()) if changed_identity else old_media
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media',%s,'running',1) RETURNING id",(old_media,)).fetchone()[0]
        store.jobs.retry(Job(job,'download_media',old_media,1),1,'failure',code='source-network')
        record=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(job,'download_media',old_media,1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(record,)).fetchone()[0] is not None
    finally:store.db.close()

def test_duplicate_source_markers_are_preserved_and_not_claimed(tmp_path,postgres_url):
    import json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            body=dict(code='source-network',operation='producer:fetch',source_group_identity='job:source-network:p:fetch',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('a','job:a','background',%s,1),('b','job:b','background',%s,2)",(json.dumps(body),json.dumps(body)))
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        assert store.db.execute('SELECT source_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0] is False
        rows=store.db.execute("SELECT body,finished FROM reader_problems WHERE id IN ('a','b') ORDER BY id").fetchall()
        assert len(rows)==2 and all(r['finished'] is None and json.loads(r['body'])==body for r in rows)
        assert any(p['id']=='job:source-network:p:fetch' for p in store.problems.candidates(2))
    finally:store.db.close()

def test_recovery_does_not_claim_ambiguous_duplicate_group(tmp_path,postgres_url):
    import json
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','source-network') RETURNING id").fetchone()[0]
            body=dict(code='source-network',operation='producer:fetch',source_group_identity='job:source-network:p:fetch',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('a','job:a','background',%s,1),('b','job:b','background',%s,2)",(json.dumps(body),json.dumps(body)))
        recover(store)
        assert store.db.execute('SELECT source_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0] is False
        rows=store.db.execute("SELECT body,finished FROM reader_problems WHERE id IN ('a','b') ORDER BY id").fetchall()
        assert len(rows)==2 and all(r['finished'] is None and json.loads(r['body'])==body for r in rows)
        assert any(p['id']=='job:source-network:p:fetch' for p in store.problems.candidates(2))
    finally:store.db.close()

def test_ambiguous_marker_unclaims_all_group_members_for_get(tmp_path,postgres_url):
    import json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true),('f','q',true)")
            first=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code,source_event_owned) VALUES ('fetch','p','pending',1,'old','source-network',true) RETURNING id").fetchone()[0]
            second=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','q','running',1) RETURNING id").fetchone()[0]
            body=dict(code='source-network',operation='producer:fetch',source_group_identity='job:source-network:f:fetch',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('a','job:a','background',%s,1),('b','job:b','background',%s,2)",(json.dumps(body),json.dumps(body)))
        store.jobs.retry(Job(second,'fetch','q',1),1,'failure',code='source-network')
        flags=store.db.execute('SELECT source_event_owned FROM jobs WHERE id=ANY(%s) ORDER BY id',([first,second],)).fetchall()
        assert [r[0] for r in flags]==[False,False]
        problem=next(p for p in store.problems.candidates(2) if p['id']=='job:source-network:f:fetch')
        assert {o['id'] for o in problem['objects']}=={'p','q'}
    finally:store.db.close()

def test_material_replacement_closes_source_media_group_before_get(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo();publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with transaction(store.db):
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media',%s,'running',1) RETURNING id",(file,)).fetchone()[0]
        store.jobs.retry(Job(job,'download_media',file,1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:demo:download_media' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            _,effects=store.collect_material(item,StructuredSource(None).describe(publication,Material('New','New text')))
            store.jobs.apply_collection(effects)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

def test_http_source_group_is_visible_and_get_does_not_write(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','source-network')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()
        assert before
        response=client.get('/api/v3/status/details')
        assert response.status_code==200 and any(p['id']==before[0] for p in response.json()['problems']['current'])
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before

def test_new_publication_id_collision_updates_source_group(tmp_path,postgres_url,monkeypatch):
    from uuid import UUID
    from oko.collection.contracts import Publication
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from oko.storage import publications
    from tests.support.source import publication_packet
    identity='00000000-0000-4000-8000-000000000001'
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        with store.db:job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete',%s,'running',1) RETURNING id",(identity,)).fetchone()[0]
        store.jobs.retry(Job(job,'feed-delete',identity,1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",('job:source-network:'+identity+':feed-delete',)).fetchone()[0]
        monkeypatch.setattr(publications,'uuid4',lambda:UUID(identity))
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(Publication('p','https://example.test/p',1))],'{}')
            store.jobs.apply_collection(effects)
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:demo:feed-delete' AND finished IS NULL").fetchone()
        assert row and row[0]==old
    finally:store.db.close()

def test_new_publication_source_collision_probe_is_indexed(tmp_path,postgres_url):
    from oko.collection.contracts import Publication
    from oko.storage.transaction import transaction
    from tests.support.source import publication_packet
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,source_event_owned) SELECT 'feed-delete','foreign-'||n,'failed','failure','source-network',true FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):store.collect_discovery([publication_packet(Publication('p','https://example.test/p',1))],'{}')
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT id,source_id,ARRAY[]::bigint[]'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('rss','p','demo:p','00000000-0000-4000-8000-000000000001')).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()

def test_captions_deferral_clears_source_ownership_and_problem(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:m:download_media' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
            store.jobs.defer_captions(Job(job,'download_media','m',1),2,1,'waiting')
        row=store.db.execute('SELECT problem_code,source_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row['problem_code'] is None and row['source_event_owned'] is False
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

def test_very_long_source_code_falls_back_without_aborting_job(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        code='source-'+('x'*2000)
        with store.db:job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code=code)
        row=store.db.execute('SELECT source_event_owned,status FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row['source_event_owned'] is False and row['status']=='pending'
        assert any(p['code']==code for p in store.problems.candidates(2))
    finally:store.db.close()

def test_job_key_capture_ignores_unrelated_publications(tmp_path,postgres_url):
    from oko.storage.source_groups import job_keys_many
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('p-2','p-1')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p-1','failed','failure','source-network') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE publications');store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:assert job_keys_many(store,[job])
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT j.id,j.kind,j.item,j.problem_code,j.feed_id'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,([job],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,scan_rows(plan)
    finally:store.db.close()

def test_previous_key_capture_ignores_unrelated_publications(tmp_path,postgres_url):
    from oko.storage.source_groups import previous_keys_many
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('p-2','p-1')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p-1','pending','failure') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE publications');store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:assert previous_keys_many(store,[(job,'source-network')])
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT j.id,j.kind,j.item,v.code AS problem_code'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,([job],['source-network'])).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,scan_rows(plan)
    finally:store.db.close()

def test_moving_thousand_members_reprojects_shared_group_once(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage import memberships,publication_links,source_groups
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'a',id,true FROM publications WHERE id LIKE 'p-%%'")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'b',id,false FROM publications WHERE id LIKE 'p-%%'")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed','failure','source-network' FROM generate_series(1,1000)n RETURNING id").fetchall()
            memberships.lock_graph(store.db)
            source_groups.sync_jobs(store,[r[0] for r in jobs],1)
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):
                memberships.lock_graph(store.db)
                effects=CollectionEffects()
                publication_links.capture_source_groups(store,effects,{'p-'+str(n) for n in range(1,1001)})
                memberships.remove(store.db,feed='a')
                store.jobs.sync_captured_source_groups(effects)
        finally:store.db.set_trace_callback(None)
        projections=[q for q in trace if q.startswith('WITH source_groups AS MATERIALIZED')]
        assert len(projections)<=4,len(projections)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_recovery_marker_cleanup_ignores_unrelated_state(tmp_path,postgres_url):
    from oko.storage.source_groups import recover
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO state(key,value) SELECT 'foreign-'||n,'value' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','source-network')")
            store.db._db.execute('ANALYZE state')
        trace=[];store.db.set_trace_callback(trace.append)
        try:recover(store)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT key FROM state WHERE key LIKE'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('source-recovery-seen:%',)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,scan_rows(plan)
    finally:store.db.close()

def test_remove_feed_job_control_refreshes_shared_source_state(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),1,'failure',code='source-network')
        with transaction(store.db):store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:a:fetch' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.remove_feed('a')
            store.jobs.remove_feed('a',{'p'})
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:b:fetch' AND finished IS NULL").fetchone()
        assert row and row['id']==old and json.loads(row['body'])['state']=='current'
    finally:store.db.close()

def test_recovery_preserves_provable_legacy_owner_occurrence(tmp_path,postgres_url):
    import json
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','old','failed','failure','source-network')")
            body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:source-network:old:fetch','background',%s,1)",(json.dumps(body),))
        recover(store)
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:new:fetch' AND finished IS NULL").fetchone()
        assert row and row[0]=='legacy'
    finally:store.db.close()

def test_recovery_does_not_steal_active_legacy_source_group(tmp_path,postgres_url):
    import json
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('old','old','Old','default','rss','rss','connected','[]'),('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true),('old','q',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','old','failed','failure','source-network'),('fetch','q','old','failed','failure','source-network')")
            body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id='q')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:source-network:old:fetch','background',%s,1)",(json.dumps(body),))
        recover(store)
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:old:fetch' AND finished IS NULL").fetchone()
        new=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:new:fetch' AND finished IS NULL").fetchone()
        assert old and old[0]=='legacy' and new and new[0]!='legacy'
    finally:store.db.close()

def test_recovery_preserves_multiple_ambiguous_legacy_groups_after_get(tmp_path,postgres_url):
    import json
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true),('new','q',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','old-a','failed','failure','source-network'),('fetch','q','old-b','failed','failure','source-network')")
            bodies=[]
            for item,owner in [('p','old-a'),('q','old-b')]:
                body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id=item)],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
                bodies.append((owner,json.dumps(body)))
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(owner,'job:source-network:'+owner+':fetch',json.dumps(body)))
        recover(store)
        store.problems.snapshot(2)
        for name,body in bodies:
            row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(name,)).fetchone()
            assert row['finished'] is None and row['body']==body
    finally:store.db.close()

def test_protected_legacy_group_is_not_claimed_by_later_source_job(tmp_path,postgres_url):
    import json
    from oko.storage.source_groups import recover
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('new','new','New','default','rss','rss','connected','[]'),('old-a','old-a','Old','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true),('new','q',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','old-a','failed','failure','source-network'),('fetch','q','old-b','failed','failure','source-network')")
            body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:source-network:old-a:fetch','background',%s,1)",(json.dumps(body),))
        recover(store)
        with store.db:
            store.db.execute("UPDATE feed_publications SET is_primary=false WHERE feed_id='new' AND publication_id='p'")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('old-a','p',true)")
            job=store.db.execute("UPDATE jobs SET status='running',generation=1 WHERE kind='fetch' AND item='p' RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','p',1),2,'failure',code='source-network')
        store.problems.snapshot(3)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='legacy'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()

def test_existing_canonical_and_legacy_source_rows_do_not_collide(tmp_path,postgres_url):
    import json
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','old','failed','failure','source-network')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('canonical','job:source-network:new:fetch','background',%s,2),('legacy','job:source-network:old:fetch','background',%s,1)",(json.dumps(body),json.dumps(body)))
        recover(store)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='legacy' AND finished IS NULL").fetchone()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:new:fetch' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_stale_legacy_marker_does_not_protect_new_occurrence(tmp_path,postgres_url):
    import hashlib,json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    identity='job:source-network:p:fetch';key='source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest()
    try:
        body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old',%s,'background',%s,1,2,'resolved'),('new',%s,'background',%s,3,NULL,NULL)",(identity,json.dumps(body),identity,json.dumps(body)))
            store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-source-legacy-v1',identity,'old'])))
        store.jobs.retry(Job(job,'fetch','p',1),4,'failure',code='source-network')
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        assert row and row[0]=='new'
    finally:store.db.close()

def test_legacy_marker_gc_is_guarded_and_transactional(tmp_path,postgres_url):
    import hashlib,json
    from oko.storage.source_groups import cleanup_legacy_protected
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    identity='job:source-network:p:fetch';key='source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest()
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old',%s,'background','{}',1,2,'resolved')",(identity,))
            store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-source-legacy-v1',identity,'old'])))
            store.db.execute("INSERT INTO state(key,value) VALUES ('source-legacy-protected:foreign','do-not-touch')")
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                cleanup_legacy_protected(store,None)
                assert not store.db.execute('SELECT 1 FROM state WHERE key=%s',(key,)).fetchone()
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT 1 FROM state WHERE key=%s',(key,)).fetchone()
        with transaction(store.db):cleanup_legacy_protected(store,None)
        assert not store.db.execute('SELECT 1 FROM state WHERE key=%s',(key,)).fetchone()
        assert store.db.execute("SELECT value FROM state WHERE key='source-legacy-protected:foreign'").fetchone()[0]=='do-not-touch'
    finally:store.db.close()

def test_legacy_marker_gc_cursor_rolls_back(tmp_path,postgres_url):
    import hashlib,json
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    identity='job:source-network:p:fetch';key='source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest()
    try:
        with store.db:store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-source-legacy-v1',identity,'missing'])))
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.read_versions.advance(now=0,wall=10)
                raise RuntimeError('rollback')
        assert store.read_versions._source_legacy_gc_cursor is None
        assert store.db.execute('SELECT 1 FROM state WHERE key=%s',(key,)).fetchone()
    finally:store.db.close()

def test_new_legacy_occurrence_replaces_only_stale_matching_marker(tmp_path,postgres_url):
    import hashlib,json
    from oko.storage.source_groups import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    identity='job:source-network:old-a:fetch';marker='source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest()
    try:
        body=dict(code='source-network',operation='producer:status',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='source-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true),('new','q',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','old-a','failed','failure','source-network'),('fetch','q','old-b','failed','failure','source-network')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old',%s,'background',%s,1,2,'resolved'),('new',%s,'background',%s,3,NULL,NULL)",(identity,json.dumps(body),identity,json.dumps(body)))
            store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-source-legacy-v1',identity,'old'])))
        recover(store)
        assert json.loads(store.db.execute('SELECT value FROM state WHERE key=%s',(marker,)).fetchone()[0])==['oko-source-legacy-v1',identity,'new']
        store.problems.snapshot(4)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='new'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()

def test_legacy_marker_gc_is_bounded_with_foreign_state(tmp_path,postgres_url):
    import hashlib,json
    from oko.storage.source_groups import cleanup_legacy_protected
    from oko.storage.transaction import transaction
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO state(key,value) SELECT 'foreign-'||n,'value' FROM generate_series(1,10000)n")
            entries=[]
            for n in range(250):
                identity='job:source-network:p-'+str(n)+':fetch'
                entries.append(('source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest(),json.dumps(['oko-source-legacy-v1',identity,'missing-'+str(n)])))
            store.db.executemany('INSERT INTO state(key,value) VALUES (%s,%s)',entries)
            store.db._db.execute('ANALYZE state')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):cursor,done=cleanup_legacy_protected(store,None)
        finally:store.db.set_trace_callback(None)
        assert cursor and not done
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'source-legacy-protected:%%'").fetchone()[0]==150
        query=next(q for q in trace if q.startswith("SELECT key,value FROM state WHERE key LIKE 'source-legacy-protected:"))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,()).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,scan_rows(plan)
    finally:store.db.close()


def test_group_remains_until_last_job_finishes_without_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('owner','owner','Owner','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('a',0),('b',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('owner','a',true),('owner','b',true)")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('feed-delete','a','running',1,'failure','source-network'),('feed-delete','b','running',1,'failure','source-network') RETURNING id,item").fetchall()
        current=store.problems.snapshot(1)['current']
        old=next(p['id'] for p in current if p['code']=='source-network')
        for index,row in enumerate(rows):
            with transaction(store.db):store.jobs.finish(Job(row['id'],'feed-delete',row['item'],1))
            saved=store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()
            assert (saved['finished'] is None)==(index==0)
    finally:store.db.close()

def test_restart_after_youtube_pause_reopens_source_group_before_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    clock=[1.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='source-network')
        clock[0]=2;store.youtube.blocked(2,100)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()
        store.db.close();clock[0]=1000
        store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_disabling_youtube_captions_finishes_source_group_before_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('f','p',true,true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'failure',code='source-network')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:download_media' AND finished IS NULL").fetchone()[0]
        store.jobs.prepare_youtube('f',False)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'source-youtube-timed:%%'").fetchone()
    finally:store.db.close()

def test_job_preimage_ignores_unrelated_publications(tmp_path,postgres_url):
    from oko.storage.source_groups import job_evidence_many
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('p-2','p-1')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p-1','failed','failure','source-network') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE publications');store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:assert job_evidence_many(store,[(job,None)])
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT j.id,j.kind,j.item,coalesce(v.code,j.problem_code)'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,([job],[None])).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,scan_rows(plan)
    finally:store.db.close()

@pytest.mark.parametrize('mode',['existing_source','incoming_source','reservation_non_source'])
def test_source_takes_graph_lock_before_processor_reservation_and_rolls_back(mode,tmp_path,postgres_url):
    existing_source=mode=='existing_source'
    import threading
    from oko.storage import memberships
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    import psycopg
    second=Store(tmp_path/'second',lambda _:None,database_url=postgres_url)
    first=psycopg.connect(postgres_url,autocommit=False)
    entered=threading.Event()
    errors=[]
    try:
        with first.transaction():
            row=first.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('fetch','p','running',1,%s,'old') RETURNING id",('source-old' if existing_source else None,)).fetchone()
        job=Job(row[0],'fetch','p',1)
        if mode=='reservation_non_source':
            with first.transaction():first.execute("UPDATE youtube_access SET processor_reservation_id='processor-reserved-test' WHERE id=1")
        original=second.problems.processor_resume_candidate
        def probe(now,*,pointer=None):
            entered.set()
            return original(now,pointer=pointer)
        second.problems.processor_resume_candidate=probe
        def worker():
            try:
                with transaction(second.db):
                    second.jobs.fail(job,'new',code='source-network') if mode=='incoming_source' else second.jobs.finish(job)
                    raise RuntimeError('rollback test')
            except RuntimeError as exc:
                if str(exc)!='rollback test': errors.append(exc)
            except BaseException as exc:
                errors.append(exc)
        with first.transaction():
            first.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            thread=threading.Thread(target=worker)
            thread.start()
            assert not entered.wait(0.3)
            first.execute("UPDATE youtube_access SET processor_reservation_id=processor_reservation_id WHERE id=1")
        thread.join(10)
        assert not thread.is_alive()
        assert not errors
        assert entered.is_set()
        row=first.execute('SELECT status,problem_code FROM jobs WHERE id=%s',(job.id,)).fetchone()
        assert tuple(row)==('running','source-old' if existing_source else None)
    finally:
        first.close();second.db.close()

def test_claim_reloads_source_after_selected_row_becomes_stale(tmp_path,postgres_url):
    from oko.storage import source_groups
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            row=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,generation) VALUES ('fetch','p','f','pending',1) RETURNING *").fetchone()
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET error='offline',problem_code='source-network' WHERE id=%s",(row['id'],))
            source_groups.sync_jobs(store,[row['id']],1)
        claimed=store.jobs._claim(row)
        assert claimed is not None
        current=store.db.execute('SELECT source_event_owned,status FROM jobs WHERE id=%s',(row['id'],)).fetchone()
        assert tuple(current)==(True,'running')
        problem=store.db.execute("SELECT body::jsonb->>'state' FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity'='job:source-network:f:fetch'").fetchone()
        assert problem is not None and problem[0]=='retrying'
    finally:store.db.close()
