from tests.support.storage import Store


def test_refresh_feed_reactivates_cancelled_discover_processor(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','cancelled','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        store.jobs.refresh_feed('feed')
        assert store.problems.processor_current() is not None
    finally:store.db.close()
