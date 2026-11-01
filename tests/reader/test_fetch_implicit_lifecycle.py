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
