from tests.support.storage import Store
from oko.storage.job_types import Job


def test_explicit_work_fetch_to_other_code_creates_new_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','p','running',1,'first error','work-fetch') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        old=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()['id']
        store.jobs.retry(Job(job,'fetch','p',1),2,'different error',code='article-limit')
        before=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        new=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert before['finished'] is not None and new and new['id']!=old
    finally:store.db.close()


def test_other_explicit_to_work_fetch_creates_new_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('fetch','p','running',1,'first error','article-limit') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        old=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()['id']
        store.jobs.retry(Job(job,'fetch','p',1),2,'different error',code='work-fetch')
        before=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        new=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert before['finished'] is not None and new and new['id']!=old
    finally:store.db.close()

def test_other_code_to_explicit_work_fetch_creates_new_occurrence(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','fail','other') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='work-fetch' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,2)
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row[0]!=old
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()

def test_recovery_keeps_known_explicit_work_as_separate_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','fail','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_implicit_code=False,objects=[dict(kind='material',id='p')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-fetch:p:fetch:p','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        old=store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert old['finished'] is not None and row and row[0]!='old'
    finally:store.db.close()
