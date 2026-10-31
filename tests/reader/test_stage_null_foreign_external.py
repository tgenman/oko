import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction

@pytest.mark.parametrize('kind',['fetch','download_media'])
def test_other_job_error_code_change_does_not_crash(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('article','rss','article',0)")
            if kind=='download_media':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
            item='article' if kind=='fetch' else 'media'
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','broken','first-error') RETURNING id",(kind,item)).fetchone()[0]
            if kind=='fetch':
                from oko.storage.fetch_job_problems import managed_candidates
                sync=store.problems.sync_fetch
            else:
                from oko.storage.media_job_problems import managed_candidates
                sync=store.problems.sync_media_problem
            sync(job,[],1)
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE jobs SET problem_code='second-error' WHERE id=%s",(job,))
            sync(job,previous,2)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='second-error'").fetchone()[0]==1
    finally:store.db.close()

def test_v2_recovery_avoids_10000_unrelated_legacy_status_problems(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='foreign',operation='producer:status',objects=[dict(kind='material',id='p')]))
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'job:foreign:feed:fetch:item-'||n,'background',%s,n FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.recover_processing_problems()
        finally:store.db.set_trace_callback(None)
        pages=[q for q in queries if q.startswith('SELECT id,identity,body,created FROM reader_problems')]
        print('unrelated status pages',len(pages))
        assert len(pages)<10
        from tests.support.postgres_work import scan_rows
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+pages[0],()).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()
