from tests.support.storage import Store
from oko.storage.fetch_job_problems import candidates


def test_projection_fetch_source_failure_for_owner(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','source-connection') RETURNING id").fetchone()[0]
        result=candidates(store,job,1)
        assert len(result)==1 and result[0]['id']=='job:source-connection:feed:fetch'
        assert result[0]['objects']==[dict(kind='material',id='article')]
    finally:store.db.close()


def test_projection_expands_only_related_linked_roots(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-x'),('root-y'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','source-connection') RETURNING id").fetchone()[0]
        result=candidates(store,job,1)
        assert {entry['id'] for entry in result}=={'job:source-connection:x:fetch','job:source-connection:y:fetch'}
        assert {entry['objects'][0]['id'] for entry in result}=={'root-x','root-y'}
    finally:store.db.close()


def test_projection_ignores_ten_thousand_unrelated_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','source-connection') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        traced=[];store.db.set_trace_callback(traced.append)
        try: assert candidates(store,job,1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in traced if q.lstrip().startswith('SELECT j.id,j.kind'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,([job],)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_projection_matches_existing_status_for_direct_and_shared_roots(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-x'),('root-y'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','source-connection') RETURNING id").fetchone()[0]
        legacy=[row for row in store.problems.candidates(1) if row.get('operation')=='producer:fetch']
        projected=candidates(store,job,1)
        assert sorted((p['id'],p['objects'],p['state']) for p in projected)==sorted((p['id'],p['objects'],p['state']) for p in legacy)
    finally:store.db.close()
