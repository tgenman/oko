import json
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows

def test_duplicate_group_markers_are_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=json.dumps(dict(code='material-incomplete',operation='producer:incomplete',incomplete_group_identity='incomplete:target',objects=[]))
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 's-'||n,'incomplete:isolated:'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        query="SELECT r.*,k.group_key FROM unnest(%s::text[]) AS k(group_key) CROSS JOIN LATERAL (SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete' AND body::jsonb->>'incomplete_group_identity'=k.group_key LIMIT 2) AS r"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['incomplete:target'],)).fetchone()[0][0]['Plan']
        print('scanned rows:',scan_rows(plan))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_ambiguous_markers_are_not_cleared_as_owned_schedule(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import scheduled_group_rows, sync_groups_time_batch
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='material-incomplete',operation='producer:incomplete',incomplete_group_identity='incomplete:target',objects=[]))
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,incomplete_due,incomplete_future) SELECT 's-'||n,'incomplete:isolated:'||n,'background',%s,1,200,'foreign' FROM generate_series(1,10000)n",(body,))
        assert scheduled_group_rows(store,['incomplete:target'])==[]
        with store.db:sync_groups_time_batch(store,['incomplete:target'],100)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE incomplete_future='foreign'").fetchone()[0]==10000
    finally:store.db.close()
