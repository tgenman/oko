import json
from tests.support.storage import Store


def test_ambiguous_legacy_status_does_not_issue_query_per_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'p-'||n,'job:delete:feed:fetch:item-'||n||':delete:part','background',
                jsonb_build_object('code','delete','operation','producer:status','title','T','consequence','C','next_step','N','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','item-'||n||':delete:part')))::text,1
                FROM generate_series(1,1000) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.snapshot(2)
        finally:store.db.set_trace_callback(None)
        queries=[q for q in sql if 'JOIN jobs j ON j.kind=p.kind AND j.item=p.item' in q]
        assert len(queries)<10, len(queries)
    finally:store.db.close()


def test_isolated_foreign_occurrence_survives_owner_change(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        original='job:delete:feed:fetch:item:delete:part'
        target='job:delete:newfeed:fetch:item:delete:part'
        old=dict(id=original,code='delete',section='object',title='old',consequence='c',next_step='n',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        foreign=dict(old,title='foreign',operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')])
        with store.db:store.problems.sync_candidate(original,old,1)
        store.problems.reconcile([foreign],2,feeds={})
        before=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(original,)).fetchone()[0]
        changed=dict(foreign,id=target,_legacy_ids={original})
        store.problems.reconcile([changed],3,feeds={})
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",(target,)).fetchone()
        assert after and after[0]==before
    finally:store.db.close()
