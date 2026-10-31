from oko.storage.transaction import transaction
from tests.support.storage import Store

def test_empty_fetch_error_saved_by_event_and_read_only_on_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('article','rss','article',0)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','pending',NULL,'') RETURNING id").fetchone()[0]
            store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert before
        assert any(p['id']==before['id'] for p in store.problems.snapshot(1)['current'])
        after=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(before)==tuple(after)
    finally:store.db.close()

def test_empty_and_explicit_fetch_codes_keep_occurrence(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='model-invalid' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,2)
            row=store.db.execute("SELECT id,body::jsonb->>'code' AS code FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
            assert row and row['id']==before and row['code']=='model-invalid'
            previous=managed_candidates(store,job,3)
            store.db.execute("UPDATE jobs SET problem_code='' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,3)
            row=store.db.execute("SELECT id,body::jsonb->>'code' AS code FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
            assert row and row['id']==before and row['code']=='work-fetch'
    finally:store.db.close()

def test_empty_fetch_recovery_batches_and_excludes_foreign_history(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','article-'||n,'failed','failure','' FROM generate_series(1,1000)n")
            body=json.dumps(dict(code='foreign',operation='producer:status',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'job:foreign:feed:filter:item-'||n,'background',%s,n FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'fetch_job_id' IS NOT NULL AND finished IS NULL").fetchone()[0]==1000
        pages=[q for q in sql if q.startswith('SELECT id,identity,body,created FROM reader_problems')]
        assert len(pages)<10
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+pages[0],()).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
        assert len(sql)<250,len(sql)
    finally:store.db.close()

def test_recovery_preserves_unmarked_work_fetch_when_code_changed(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other')")
            body=dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='article')],section='object',title='legacy',consequence='saved',next_step='retry',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:work-fetch:feed:fetch:article','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:other:feed:fetch:article' AND finished IS NULL").fetchone()
        assert row and row[0]!='legacy'
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='legacy' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_youtube_admission_keeps_managed_fetch_material(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:1)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','pending','failure','') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            store.incomplete_draft.mark_job('fetch','article')
            store.youtube.blocked(1,60)
        problem=next(p for p in store.problems.snapshot(2)['current'] if p['code']=='youtube-admission')
        assert dict(kind='material',id='article') in problem['objects']
    finally:store.db.close()


def test_youtube_admission_does_not_scan_unrelated_fetch_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:1)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','pending','failure','')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'pending','failure','' FROM generate_series(1,10000)n")
            store.incomplete_draft.mark_job('fetch','article')
            store.youtube.blocked(1,60)
            store.db._db.execute('ANALYZE jobs')
        problem=next(p for p in store.problems.snapshot(2)['current'] if p['code']=='youtube-admission')
        assert dict(kind='material',id='article') in problem['objects']
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) SELECT id FROM jobs WHERE youtube_admission_candidate ORDER BY id LIMIT 100').fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_present_to_null_fetch_handoff_keeps_occurrence(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code=NULL WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,2)
        assert any(p['id']==before for p in store.problems.snapshot(3)['current'])
    finally:store.db.close()

def test_null_to_present_fetch_handoff_keeps_occurrence(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','article','running',1,'failure',NULL) RETURNING id").fetchone()[0]
        before=next(p['id'] for p in store.problems.snapshot(1)['current'] if p['code']=='work-fetch')
        store.jobs.retry(Job(job,'fetch','article',1),2,'specific',code='other')
        rows=store.db.execute("SELECT id,body::jsonb->>'code' AS code FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchall()
        assert len(rows)==1 and rows[0]['id']==before and rows[0]['code']=='other'
    finally:store.db.close()

def test_null_to_present_preserves_isolated_occurrence(tmp_path,postgres_url):
    import json
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','article','running',1,'failure',NULL) RETURNING id").fetchone()[0]
            foreign=dict(code='work-fetch',operation='producer:filter',objects=[dict(kind='material',id='other')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-fetch:article:fetch:article','background',%s,1)",(json.dumps(foreign),))
        current=store.problems.snapshot(2)['current']
        before=next(p['id'] for p in current if p['code']=='work-fetch' and p['id']!='foreign')
        store.jobs.retry(Job(job,'fetch','article',1),2,'specific',code='other')
        row=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]==before
    finally:store.db.close()

def test_recovery_avoids_unrelated_null_fetch_legacy(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','article-'||n,'failed','failure',NULL FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','focus','failed','failure','')")
            body=json.dumps(dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'legacy-'||n,'job:work-fetch:feed:fetch:article-'||n,'background',%s,n FROM generate_series(1,10000)n",(body,))
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        pages=[q for q in sql if q.startswith('SELECT id,identity,body,created FROM reader_problems')]
        assert len(pages)<10,len(pages)
        from tests.support.postgres_work import scan_rows
        query=next(q for q in sql if q.startswith('SELECT r.*,target.item FROM unnest('))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(['focus'],)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()

def test_recovery_batches_thousand_matching_legacy_errors(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','article-'||n,'failed','failure','' FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'legacy-'||n,'job:work-fetch:article-'||n||':fetch:article-'||n,'background',jsonb_build_object('code','work-fetch','operation','producer:status','objects',jsonb_build_array(jsonb_build_object('kind','material','id','article-'||n)))::text,n FROM generate_series(1,1000)n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        count=store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'legacy-%%' AND body::jsonb->>'fetch_job_id' IS NOT NULL AND finished IS NULL").fetchone()[0]
        assert count==1000
        assert len(sql)<300,len(sql)
    finally:store.db.close()
