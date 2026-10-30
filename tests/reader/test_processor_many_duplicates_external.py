from tests.support.storage import Store


def test_many_legacy_processor_groups_recover_in_batches(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'p-'||n,'processor:configuration-isolated:'||n,'background',
                jsonb_build_object('code','processor-config','processor_group_identity','processor:configuration','section','action','title','x','consequence','c','next_step','n','state','current','operation','producer:filter','objects','[]'::jsonb)::text,n
                FROM generate_series(1,1000) n""")
        statements=[];store.db.set_trace_callback(statements.append)
        try:
            with store.db:store.problems.recover_processor_duplicates(2000)
        finally:store.db.set_trace_callback(None)
        print('SQL statements to recover 1000 duplicate groups:',len(statements))
        assert len(statements)<100,len(statements)
    finally:store.db.close()


def test_duplicate_recovery_preserves_oldest_and_rolls_back_batches(tmp_path,postgres_url):
    import pytest
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('read','feed','failed','x','processor-config',true)")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'p-'||n,'processor:configuration-isolated:'||n,'background',
                jsonb_build_object('code','processor-config','processor_group_identity','processor:configuration','section','action','title','x','consequence','c','next_step','n','state','current','operation','producer:filter','objects','[]'::jsonb)::text,n
                FROM generate_series(1,201) n""")
        def fail(point):
            if point=='processor_duplicate_recovery_batch':raise RuntimeError('interrupted')
        store.fault=fail
        with pytest.raises(RuntimeError,match='interrupted'):
            with transaction(store.db):store.problems.recover_processor_duplicates(2000)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'processor:configuration-isolated:%' AND finished IS NULL").fetchone()[0]==201
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.problems.processor_current()['id']=='p-1'
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'processor:configuration-isolated:%' AND outcome='irrelevant'").fetchone()[0]==200
    finally:store.db.close()


def test_keyset_duplicate_recovery_handles_ties_empty_id_and_unrelated_history(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    from oko.storage.problems import _PROCESSOR_VALID_SQL
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'p-'||n,'processor:configuration-isolated:'||n,'background',
                jsonb_build_object('code','processor-config','processor_group_identity','processor:configuration','section','action','title','x','consequence','c','next_step','n','state','current','operation','producer:filter','objects','[]'::jsonb)::text,1
                FROM generate_series(1,1001) n""")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT '', 'processor:configuration-isolated:empty','background',body,1 FROM reader_problems WHERE id='p-1'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'job:other:'||n,'background','{}',1 FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE reader_problems')
        query="SELECT id,identity,created FROM reader_problems WHERE "+_PROCESSOR_VALID_SQL+" AND body::jsonb->>'processor_group_identity'='processor:configuration' AND identity!='processor:configuration' AND (created,id COLLATE \"C\")>(%s,%s) ORDER BY created,id COLLATE \"C\" LIMIT 100"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(1,'p-500')).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<=100,plan
        with store.db:store.problems.recover_processor_duplicates(2000)
        assert store.problems.processor_current()['id']==''
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'processor:configuration-isolated:%' AND outcome='irrelevant'").fetchone()[0]==1001
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'foreign-%' AND finished IS NULL").fetchone()[0]==10000
    finally:store.db.close()
