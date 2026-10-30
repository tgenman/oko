import psycopg
from tests.support.storage import Store


def test_recovery_holds_advisory_lock_until_batch_commit(tmp_path,postgres_url):
    from oko.storage.incomplete_index import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='incomplete-class-recovered-v1'")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content) VALUES ('p','rss','p',0,1,'{}')")
        checks=[]
        with psycopg.connect(postgres_url,autocommit=True) as rival:
            def trace(query):
                if query.startswith("SELECT 1 FROM state WHERE key='incomplete-class-recovered-v1'"):
                    with rival.transaction():
                        acquired=rival.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))',(746582091,)).fetchone()[0]
                        checks.append(acquired)
            store.db.set_trace_callback(trace)
            try:recover(store)
            finally:store.db.set_trace_callback(None)
            assert checks and all(not flag for flag in checks),checks
            with rival.transaction():
                assert rival.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))',(746582091,)).fetchone()[0]
    finally:store.db.close()
