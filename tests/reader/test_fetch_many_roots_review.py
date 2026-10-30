import json
from tests.support.storage import Store


def test_recovery_shared_fetch_has_bounded_roundtrips(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id) SELECT 'f-'||n FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'r-'||n,0 FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('target',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'r-'||n,true FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'r-'||n,'target' FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:article-limit:f-'||n||':fetch:target','background',jsonb_build_object('code','article-limit','section','object','title','t','consequence','c','next_step','n','state','current','operation','producer:fetch','objects',jsonb_build_array(jsonb_build_object('kind','material','id','r-'||n)))::text,1 FROM generate_series(1,30) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        print('sql statements:',len(sql))
        assert len(sql)<500,len(sql)
    finally:store.db.close()


def test_recovery_shared_fetch_moved_owners_is_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id) SELECT 'f-'||n FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'r-'||n,0 FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('target',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'r-'||n,true FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'r-'||n,'target' FROM generate_series(1,30) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:article-limit:old-'||n||':fetch:target','background',jsonb_build_object('code','article-limit','section','object','title','t','consequence','c','next_step','n','state','current','operation','producer:fetch','objects',jsonb_build_array(jsonb_build_object('kind','material','id','r-'||n)))::text,1 FROM generate_series(1,30) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        print('sql statements:',len(sql))
        assert len(sql)<500,len(sql)
    finally:store.db.close()


def test_ambiguous_legacy_identity_does_not_finish_unrelated_active_material(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,outcome,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None
        assert json.loads(row['body'])==body
    finally:store.db.close()


def test_status_fetch_legacy_guard_is_batched(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'job:article-limit:feed:fetch:item-'||n,'background',jsonb_build_object('code','article-limit','section','object','title','x','consequence','c','next_step','n','operation','producer:status','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','item-'||n)))::text,1 FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.snapshot(2)
        finally:store.db.set_trace_callback(None)
        lookups=[q for q in sql if q.lstrip().startswith('SELECT target.root FROM unnest')]
        assert len(lookups)<=3,len(lookups)
    finally:store.db.close()


def test_recovery_maps_old_occurrence_to_its_matching_root(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-x'),('root-y'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','article-limit')")
            for identity,problem_id,root in [('old-z','z-for-x','root-x'),('old-a','a-for-y','root-y')]:
                body=dict(code='article-limit',section='object',title='t',consequence='c',next_step='n',state='current',operation='producer:fetch',objects=[dict(kind='material',id=root)])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(problem_id,'job:article-limit:'+identity+':fetch:article',json.dumps(body)))
        store.problems.recover_fetch_problems()
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:x:fetch:article' AND finished IS NULL").fetchone()[0]=='z-for-x'
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:y:fetch:article' AND finished IS NULL").fetchone()[0]=='a-for-y'
    finally:store.db.close()


def test_fetch_legacy_guard_ignores_unrelated_active_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','article-limit' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.active_fetch_roots({'target'})
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.lstrip().startswith('SELECT target.root FROM unnest'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(['target'],)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_misleading_fetch_identity_with_live_different_job_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('ghost')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true),('feed','ghost',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit'),('fetch','ghost','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        store.problems.snapshot(2)
        old=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert old['finished'] is None and json.loads(old['body'])==body
    finally:store.db.close()


def test_recovery_hundred_moved_roots_has_bounded_sql(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id) SELECT 'f-'||n FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'r-'||n,0 FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('target',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'r-'||n,true FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'r-'||n,'target' FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:article-limit:old-'||n||':fetch:target','background',jsonb_build_object('code','article-limit','section','object','title','t','consequence','c','next_step','n','state','current','operation','producer:fetch','objects',jsonb_build_array(jsonb_build_object('kind','material','id','r-'||n)))::text,1 FROM generate_series(1,100) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_problems()
        finally:store.db.set_trace_callback(None)
        print('sql statements:',len(sql))
        assert len(sql)<100,len(sql)
    finally:store.db.close()


def test_done_fetch_error_must_protect_ambiguous_legacy(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id) VALUES ('feed')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','done','failure','article-limit')")
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,outcome,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None
        assert json.loads(row['body'])==body
    finally:store.db.close()


def test_done_fetch_error_protects_linked_root_only_with_complete_failure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('root'),('target')")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','target')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','done','failure','article-limit')")
        assert store.problems.active_fetch_roots({'root'})=={'root'}
        with store.db:store.db.execute("UPDATE jobs SET error=NULL WHERE kind='fetch' AND item='target'")
        assert store.problems.active_fetch_roots({'root'})==set()
        with store.db:store.db.execute("UPDATE jobs SET error='failure',problem_code=NULL WHERE kind='fetch' AND item='target'")
        assert store.problems.active_fetch_roots({'root'})==set()
    finally:store.db.close()
