import json
import pytest
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows


@pytest.mark.parametrize('existing',[False,True])
def test_action_first_page_with_mixed_receipts(tmp_path,postgres_url,existing):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            body=json.dumps(dict(title='Command',objects=[],action=None,discardable=True))
            store.db.execute("INSERT INTO reader_action_results(sequence,id,identity,body,started,result) SELECT n,'id-'||n,'identity-'||n,%s,1,CASE WHEN n%%10=0 THEN 'failed' ELSE 'succeeded' END FROM generate_series(1,100000) n",(body,))
            store.db.execute("INSERT INTO reader_action_results(sequence,id,identity,body,started,result) VALUES (-1,'negative','negative',%s,1,'failed'),(0,'zero','zero',%s,1,'failed')",(body,body))
            if existing:
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'action:identity-'||n,'action','{}',1 FROM generate_series(1,100000) n WHERE n%10=0")
            store.db._db.execute('ANALYZE reader_action_results')
            store.db._db.execute('ANALYZE reader_problems')
        sql="""SELECT r.sequence,r.identity FROM reader_action_results r
                WHERE r.result IN ('failed','unknown')
                AND NOT EXISTS (SELECT 1 FROM reader_action_results newer WHERE newer.identity=r.identity AND newer.sequence>r.sequence)
                AND NOT EXISTS (SELECT 1 FROM reader_problems p WHERE p.identity='action:'||r.identity
                    AND (p.finished IS NULL OR (p.outcome='irrelevant' AND p.finished>=r.started
                        AND p.body::jsonb->>'request_id'=r.id
                        AND p.body::jsonb->>'request_attempt' IS NOT DISTINCT FROM r.body::jsonb->>'request_attempt')))
                ORDER BY r.sequence LIMIT 100"""
        if existing:
            bounded=sql.replace("WHERE r.result IN ('failed','unknown')", "WHERE r.result IN ('failed','unknown') AND r.sequence<=0")
            alternative=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+bounded).fetchone()[0][0]
            p=alternative['Plan']
            print('ACTION_V1_ONLY',json.dumps(dict(rows=scan_rows(p),buffers=p.get('Shared Hit Blocks',0)+p.get('Shared Read Blocks',0),ms=alternative['Execution Time'])),flush=True)
            correlated=bounded.replace("AND newer.sequence>r.sequence)","AND newer.sequence>r.sequence OFFSET 0)").replace("IS NOT DISTINCT FROM r.body::jsonb->>'request_attempt')))","IS NOT DISTINCT FROM r.body::jsonb->>'request_attempt')) OFFSET 0)")
            alt=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+correlated).fetchone()[0][0]
            p=alt['Plan']
            print('ACTION_V1_CORRELATED',json.dumps(dict(rows=scan_rows(p),buffers=p.get('Shared Hit Blocks',0)+p.get('Shared Read Blocks',0),ms=alt['Execution Time'])),flush=True)
        explained=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql).fetchone()[0][0]
        plan=explained['Plan']
        print('ACTION_PAGE',json.dumps(dict(existing=existing,rows=scan_rows(plan),buffers=plan.get('Shared Hit Blocks',0)+plan.get('Shared Read Blocks',0),ms=explained['Execution Time'])),flush=True)
        def show(node):
            if 'Relation Name' in node:print('NODE',node['Node Type'],node['Relation Name'],node.get('Index Name'),node.get('Actual Rows'),node.get('Rows Removed by Filter'),node.get('Actual Loops'),flush=True)
            for child in node.get('Plans',[]):show(child)
        show(plan)
    finally:store.db.close()
