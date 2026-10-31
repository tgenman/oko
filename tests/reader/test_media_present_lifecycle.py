import pytest
from oko.storage.transaction import transaction
from tests.support.storage import Store

@pytest.mark.parametrize('code',['','work-download_media'])
def test_present_work_media_is_persisted_and_status_is_read_only(tmp_path,postgres_url,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','pending','failure',%s) RETURNING id",(code,)).fetchone()[0]
            store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert before
        assert any(p['id']==before['id'] for p in store.problems.snapshot(1)['current'])
        after=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(before)==tuple(after)
    finally:store.db.close()

@pytest.mark.parametrize('code',['','work-download_media'])
def test_subtitles_work_is_event_owned_and_get_is_read_only(tmp_path,postgres_url,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','youtube:subtitles','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','pending','failure',%s) RETURNING id",(code,)).fetchone()[0]
            store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        row=store.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row is not None
        assert any(p['id']==row['id'] and p['code']=='work-download_media' for p in store.problems.snapshot(1)['current'])
        assert tuple(row)==tuple(store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
    finally:store.db.close()

def test_empty_and_explicit_media_codes_preserve_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            occurrence=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='other' WHERE id=%s",(job,))
            store.problems.sync_media_problem(job,previous,2)
            row=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
            assert row and row['id']==occurrence
            previous=managed_candidates(store,job,3)
            store.db.execute("UPDATE jobs SET problem_code='' WHERE id=%s",(job,))
            store.problems.sync_media_problem(job,previous,3)
            row=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
            assert row and row['id']==occurrence
    finally:store.db.close()

def test_explicit_work_media_to_other_code_is_new_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','work-download_media') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            occurrence=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='other' WHERE id=%s",(job,))
            store.problems.sync_media_problem(job,previous,2)
            row=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
            assert row and row['id']!=occurrence
            assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(occurrence,)).fetchone()[0] is not None
    finally:store.db.close()

def test_null_to_present_media_keeps_occurrence(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('download_media','media','running',1,'failure',NULL) RETURNING id").fetchone()[0]
        before=next(p['id'] for p in store.problems.snapshot(1)['current'] if p['code']=='work-download_media')
        store.jobs.retry(Job(job,'download_media','media',1),2,'specific',code='other')
        row=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row['id']==before
    finally:store.db.close()

def test_present_to_null_media_keeps_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code=NULL WHERE id=%s",(job,))
            store.problems.sync_media_problem(job,previous,2)
        assert any(p['id']==before for p in store.problems.snapshot(3)['current'])
    finally:store.db.close()

def test_recovery_batches_old_media_work_and_ignores_unrelated_null(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'image','x','file','unavailable' FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','m-'||n,'failed','failure','' FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:work-download_media:m-'||n||':download_media:m-'||n,'background',jsonb_build_object('code','work-download_media','operation','producer:status','objects',jsonb_build_array(jsonb_build_object('kind','material','id','p-'||n)))::text,n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','foreign-'||n,'failed','failure',NULL FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-old-'||n,'job:work-download_media:foreign-'||n||':download_media:foreign-'||n,'background',jsonb_build_object('code','work-download_media','operation','producer:status','objects',jsonb_build_array(jsonb_build_object('kind','material','id','foreign-'||n)))::text,n FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE reader_problems')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_media_problems()
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND body::jsonb->>'media_job_id' IS NOT NULL AND finished IS NULL").fetchone()[0]==1000
        assert len(sql)<300,len(sql)
        pages=[q for q in sql if q.startswith('SELECT id,identity,body,created FROM reader_problems')]
        assert len(pages)<10
        query=next(q for q in sql if q.startswith('SELECT id,identity,body,substring('))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(['m-1'],)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()

def test_unmarked_legacy_media_with_changed_code_is_preserved(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','other')")
            body=dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:media:download_media:media','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='old' AND finished IS NULL AND body=%s",(json.dumps(body),)).fetchone()
        new=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id' IS NOT NULL AND finished IS NULL").fetchone()
        assert new and new[0]!='old'
    finally:store.db.close()

def test_known_old_media_and_foreign_target_preserve_both(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','other') RETURNING id").fetchone()[0]
            old=dict(code='work-download_media',operation='producer:download_media',media_file_id='media',media_job_id=str(job),media_implicit_code=True,objects=[dict(kind='material',id='article')])
            foreign=dict(code='other',operation='producer:filter',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:media:download_media:media','background',%s,1),('foreign','job:other:media:download_media:media','background',%s,1)",(json.dumps(old),json.dumps(foreign)))
        store.problems.recover_media_problems()
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row[0]=='old'
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
    finally:store.db.close()

def test_null_to_present_preserves_isolated_media_occurrence(tmp_path,postgres_url):
    import json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('download_media','media','running',1,'failure',NULL) RETURNING id").fetchone()[0]
            foreign=dict(code='work-download_media',operation='producer:filter',objects=[dict(kind='material',id='other')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:media:download_media:media','background',%s,1)",(json.dumps(foreign),))
        before=next(p['id'] for p in store.problems.snapshot(2)['current'] if p['code']=='work-download_media' and p['id']!='foreign')
        store.jobs.retry(Job(job,'download_media','media',1),2,'specific',code='other')
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row['id']==before
    finally:store.db.close()

def test_http_reads_recovered_empty_media_without_write(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO publications(id) VALUES ('article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE body::jsonb->>'media_job_id' IS NOT NULL AND finished IS NULL").fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200 and any(p['id']==before[0] for p in response.json()['problems']['current'])
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before

def test_status_does_not_scan_ten_thousand_managed_media_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','foreign-'||n,'failed','failure','' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()

def test_legacy_media_recovers_when_new_owner_is_linked_root(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:media:download_media:media','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems()
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()

def test_subtitle_handoff_does_not_claim_processor_problem(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE media_files SET resource_key='youtube:subtitles' WHERE id='media'")
            store.db.execute("UPDATE jobs SET problem_code='processor-config' WHERE id=%s",(job,))
            store.problems.sync_media_problem(job,previous,2)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'code'='processor-config' AND body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_legacy_media_work_recovers_matching_linked_roots(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-a'),('root-b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','root-a',true),('b','root-b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-a','article'),('root-b','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
            for name,root in [('old-a','root-a'),('old-b','root-b')]:
                body=dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id=root)])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(name,'job:work-download_media:'+name+':download_media:media',json.dumps(body)))
        store.problems.recover_media_problems()
        rows=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert {row['id'] for row in rows}=={'old-a','old-b'}
    finally:store.db.close()

def test_recovery_preserves_legacy_orphan_media_id(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','orphan','failed','failure','') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='orphan')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:orphan:download_media:orphan','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems()
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()

def test_recovery_rekeys_event_owned_isolated_media(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',operation='producer:download_media',media_file_id='media',media_job_id=str(job),media_job_identity='job:work-download_media:media:download_media:media',media_implicit_code=True,objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-isolated:token','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems()
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert len(row)==1 and row[0][0]=='old'
    finally:store.db.close()

def test_recovery_two_roots_with_one_foreign_target_keeps_both_ids(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-a'),('root-b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','root-a',true),('b','root-b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-a','article'),('root-b','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
            for name,root in [('old-a','root-a'),('old-b','root-b')]:
                body=dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id=root)])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(name,'job:work-download_media:'+name+':download_media:media',json.dumps(body)))
            foreign=dict(code='work-download_media',operation='producer:fetch',objects=[dict(kind='material',id='root-b')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:b:download_media:media','background',%s,1)",(json.dumps(foreign),))
        store.problems.recover_media_problems()
        rows=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert {row[0] for row in rows}=={'old-a','old-b'}
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
    finally:store.db.close()
