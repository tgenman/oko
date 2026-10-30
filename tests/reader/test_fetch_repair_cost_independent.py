import json
import pytest
from tests.support.storage import Store

def test_single_job_many_distinct_legacy_rows_has_bounded_queries(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'old-'||n,'job:fetch-isolated:'||n,'background',(%s::jsonb || jsonb_build_object('legacy_variant',n))::text,1 FROM generate_series(1,1000)n""",(json.dumps(body),))
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.recover_fetch_isolated_work()
        finally:store.db.set_trace_callback(None)
        marked=[q for q in queries if q.startswith('SELECT r.id,r.identity,r.body,r.created,target.marker')]
        print('full per-job lookups:',len(marked))
        assert 1<=len(marked)<=2
    finally:store.db.close()


def test_job_boundary_pagination_preserves_ambiguous_rows_and_processes_following_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,201)n")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed','failure','other' FROM generate_series(1,201)n RETURNING id,item").fetchall()
            first=rows[0]
            for job in rows[1:]:
                body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job['id']),fetch_job_identity='job:work-fetch:'+job['item']+':fetch:'+job['item'],fetch_implicit_code=True,objects=[dict(kind='material',id=job['item'])])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",('old-'+str(job['id']),'job:fetch-isolated:'+str(job['id']),json.dumps(body)))
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(first['id']),fetch_job_identity='job:work-fetch:'+first['item']+':fetch:'+first['item'],fetch_implicit_code=True,objects=[dict(kind='material',id=first['item'])])
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
              SELECT 'amb-'||n,'job:fetch-isolated:amb-'||n,'background',(%s::jsonb || jsonb_build_object('legacy_variant',n))::text,1 FROM generate_series(1,150)n""",(json.dumps(body),))
        before=[tuple(row) for row in store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE id LIKE 'amb-%%' ORDER BY id")]
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.recover_fetch_isolated_work()
        finally:store.db.set_trace_callback(None)
        assert [tuple(row) for row in store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE id LIKE 'amb-%%' ORDER BY id")]==before
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL AND body::jsonb->>'code'='other'").fetchone()[0]==200
        lookups=[q for q in queries if q.startswith('SELECT r.id,r.identity,r.body,r.created,target.marker')]
        assert len(lookups)<=4,len(lookups)
    finally:store.db.close()


@pytest.mark.parametrize('identical',[False,True])
def test_ten_thousand_one_job_is_bounded_and_preserves_data(tmp_path,postgres_url,identical):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            if identical:
                store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||lpad(n::text,5,'0'),'job:fetch-isolated:'||n,'background',%s,n FROM generate_series(1,10000)n""",(json.dumps(body),))
            else:
                store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||lpad(n::text,5,'0'),'job:fetch-isolated:'||n,'background',(%s::jsonb||jsonb_build_object('legacy_variant',n))::text,n FROM generate_series(1,10000)n""",(json.dumps(body),))
            store.db._db.execute('ANALYZE reader_problems')
        before=store.db.execute("SELECT md5(string_agg(body,'|' ORDER BY id)) AS digest FROM reader_problems WHERE id LIKE 'old-%%'").fetchone()['digest']
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.recover_fetch_isolated_work()
        finally:store.db.set_trace_callback(None)
        lookup=next(q for q in queries if q.startswith('SELECT r.id,r.identity,r.body,r.created,target.marker'))
        assert 'LIMIT 101' in lookup
        marker=[str(job)]
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+lookup,(marker,)).fetchone()[0][0]['Plan']
        print('variant',identical,'sql',len(queries),'lookup rows after repair',scan_rows(plan))
        if identical:
            winner_query=next(q for q in queries if q.startswith('SELECT id,identity,body,created,body::jsonb') and 'ORDER BY created' in q)
            assert 'LIMIT 1' in winner_query
            assert sum('LIMIT 400' in q and q.startswith('UPDATE reader_problems SET finished=') for q in queries)==26
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==1
            assert store.db.execute("SELECT id FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]=='old-00001'
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NOT NULL AND outcome='irrelevant'").fetchone()[0]==9999
        else:
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==10000
            after=store.db.execute("SELECT md5(string_agg(body,'|' ORDER BY id)) AS digest FROM reader_problems WHERE id LIKE 'old-%%'").fetchone()['digest']
            assert before==after
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='other' AND finished IS NULL").fetchone()[0]==1
        assert len([q for q in queries if q.startswith('SELECT r.id,r.identity,r.body,r.created,target.marker')])==1
    finally:store.db.close()


@pytest.mark.parametrize('extra',[{'legacy_variant':17},{'status_job_target':'article'}])
def test_same_code_opaque_event_body_is_not_rewritten(tmp_path,postgres_url,extra):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')],**extra)
            raw=json.dumps(body)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:old','background',%s,1)",(raw,))
        store.problems.recover_fetch_isolated_work()
        assert tuple(store.db.execute("SELECT identity,body,finished FROM reader_problems WHERE id='old'").fetchone())==('job:fetch-isolated:old',raw,None)
    finally:store.db.close()


def test_large_duplicate_group_rolls_back_and_retries(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||lpad(n::text,5,'0'),'job:fetch-isolated:'||n,'background',%s,n FROM generate_series(1,10000)n",(json.dumps(body),))
        original=store.problems.recover_fetch_batch
        def fail(rows,now):
            original(rows,now)
            raise RuntimeError('rollback')
        monkeypatch.setattr(store.problems,'recover_fetch_batch',fail)
        with pytest.raises(RuntimeError,match='rollback'):store.problems.recover_fetch_isolated_work()
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==10000
        assert not store.db.execute("SELECT 1 FROM state WHERE key='fetch-isolated-work-recovered-v1'").fetchone()
        monkeypatch.setattr(store.problems,'recover_fetch_batch',original)
        store.problems.recover_fetch_isolated_work()
        assert store.db.execute("SELECT id FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]=='old-00001'
    finally:store.db.close()


def test_repair_holds_transaction_lock_during_page(tmp_path,postgres_url):
    import psycopg
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
        seen=[]
        with psycopg.connect(postgres_url,autocommit=True) as other:
            def trace(query):
                if query.startswith("SELECT 1 FROM state WHERE key='fetch-isolated-work-recovered-v1'"):
                    with other.transaction():
                        seen.append(other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))',(746582093,)).fetchone()[0])
            store.db.set_trace_callback(trace)
            try:store.problems.recover_fetch_isolated_work()
            finally:store.db.set_trace_callback(None)
            assert seen and all(not flag for flag in seen)
            with other.transaction():assert other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))',(746582093,)).fetchone()[0]
    finally:store.db.close()


def test_semantically_equal_but_byte_distinct_legacy_bodies_are_not_collapsed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            a=json.dumps(body);b=json.dumps(body,separators=(',',':'))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('a','job:fetch-isolated:a','background',%s,1),('b','job:fetch-isolated:b','background',%s,2)",(a,b))
        store.problems.recover_fetch_isolated_work()
        rows=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE id IN ('a','b') ORDER BY id").fetchall()
        assert [tuple(row) for row in rows]==[('a',a,None),('b',b,None)]
    finally:store.db.close()
