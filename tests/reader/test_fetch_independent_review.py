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


def test_ambiguous_legacy_identity_does_not_finish_unrelated_active_material(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id) VALUES ('feed')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,outcome,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None
        assert json.loads(row['body'])==body
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


def test_legacy_mixed_materials_are_not_silently_reassigned(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id) VALUES ('feed')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target'),dict(kind='material',id='unrelated')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None
        assert json.loads(row['body'])==body
    finally:store.db.close()


def test_mixed_legacy_materials_survive_get_and_repeated_recovery(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='target'),dict(kind='material',id='unrelated')])
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        store.problems.snapshot(2)
        with store.db:store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_marked_record_with_unattributable_object_is_not_overwritten(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit') RETURNING id").fetchone()[0]
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:fetch',fetch_job_id=str(job),objects=[dict(kind='material',id='target'),dict(kind='material',id='unrelated')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()
