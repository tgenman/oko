import json
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows

def test_duplicate_markers_are_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='source-network',operation='producer:fetch',source_group_identity='job:source-network:p:fetch',objects=[dict(kind='material',id='p')]))
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 's-'||n,'job:s-'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        query="SELECT identity,body FROM reader_problems WHERE finished IS NULL AND octet_length(body::jsonb->>'source_group_identity')<=1000 AND body::jsonb->>'source_group_identity'=%s LIMIT 2"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('job:source-network:p:fetch',)).fetchone()[0][0]['Plan']
        print('scanned rows:',scan_rows(plan))
        assert scan_rows(plan)<100
    finally:store.db.close()

def test_get_source_marker_lookup_is_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='source-network',operation='producer:fetch',source_group_identity='job:source-network:p:fetch',objects=[dict(kind='material',id='p')],section='object',title='old',consequence='saved',next_step='retry',state='current',next_at=None,action=None))
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','source-network')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 's-'||n,'job:s-'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.candidates(2)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT requested.canonical'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['job:source-network:p:fetch'],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,scan_rows(plan)
    finally:store.db.close()
