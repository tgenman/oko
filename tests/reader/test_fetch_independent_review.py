import json
from tests.support.storage import Store




def test_ambiguous_legacy_identity_does_not_finish_unrelated_active_material(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
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




def test_legacy_mixed_materials_are_not_silently_reassigned(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
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
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        store.problems.snapshot(2)
        with store.db:store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_marked_record_with_unattributable_object_is_not_overwritten(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','target',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','article-limit') RETURNING id").fetchone()[0]
            body=dict(code='article-limit',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:fetch',fetch_job_id=str(job),objects=[dict(kind='material',id='target'),dict(kind='material',id='unrelated')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:article-limit:feed:fetch:target','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()
