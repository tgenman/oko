import json
from tests.support.storage import Store


def test_refresh_done_fetch_updates_its_explicit_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete) VALUES ('item','rss','item',0,0)")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='item'")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','done','failure','fetch-failed') RETURNING id").fetchone()[0]
            store.problems.sync_fetch(job,[],1)
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()
        assert json.loads(before['body'])['next_step']=='Повторите загрузку вручную.'
        store.jobs.refresh_feed('feed')
        after=store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone()
        assert after and json.loads(after['body'])['next_step']=='Око повторит попытку автоматически.'
    finally:store.db.close()
