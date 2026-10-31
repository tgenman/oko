import json
from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_recovery_rekeys_event_owned_isolated_fetch(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v3'")
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        rows=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert [row[0] for row in rows]==['old']
    finally:store.db.close()


def test_recovery_preserves_each_isolated_root_on_code_change(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-a'),('root-b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','root-a',true),('b','root-b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-a','article'),('root-b','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            for feed,root in [('a','root-a'),('b','root-b')]:
                body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:'+feed+':fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id=root)])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",('old-'+feed,'job:fetch-isolated:'+feed,json.dumps(body)))
        store.problems.recover_fetch_problems()
        rows=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert {row[0] for row in rows}=={'old-a','old-b'}
    finally:store.db.close()


def test_repair_already_recovered_database_keeps_original_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            old=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            newer=dict(code='other',operation='producer:fetch',fetch_job_id=str(job),fetch_owner='article',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1),('new','job:other:article:fetch:article','background',%s,2)",(json.dumps(old),json.dumps(newer)))
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-problems-recovered-v2'").fetchone()
        store.problems.recover_fetch_problems()
        active=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert [r[0] for r in active]==['old']
        assert store.db.execute("SELECT finished,outcome FROM reader_problems WHERE id='new'").fetchone()[1]=='irrelevant'
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-isolated-work-recovered-v1'").fetchone()
    finally:store.db.close()


def test_repair_explicit_work_code_does_not_merge_different_errors(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            old=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=False,objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1)",(json.dumps(old),))
        store.problems.recover_fetch_problems()
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is not None
        rows=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'code'='other' AND finished IS NULL").fetchall()
        assert len(rows)==1 and rows[0][0]!='old'
    finally:store.db.close()


def test_repair_rolls_back_batch_and_resumes_without_loss(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            old=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            newer=dict(code='other',operation='producer:fetch',fetch_job_id=str(job),fetch_owner='article',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1),('new','job:other:article:fetch:article','background',%s,2)",(json.dumps(old),json.dumps(newer)))
        before=[tuple(r) for r in store.db.execute('SELECT id,identity,body,finished,outcome FROM reader_problems ORDER BY id')]
        original=store.problems.recover_fetch_batch
        def fail(rows,now):
            original(rows,now)
            raise RuntimeError('crash')
        monkeypatch.setattr(store.problems,'recover_fetch_batch',fail)
        with pytest.raises(RuntimeError,match='crash'):store.problems.recover_fetch_isolated_work()
        assert [tuple(r) for r in store.db.execute('SELECT id,identity,body,finished,outcome FROM reader_problems ORDER BY id')]==before
        assert not store.db.execute("SELECT 1 FROM state WHERE key='fetch-isolated-work-recovered-v1'").fetchone()
        monkeypatch.setattr(store.problems,'recover_fetch_batch',original)
        store.problems.recover_fetch_isolated_work()
        assert store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]=='old'
    finally:store.db.close()


def test_repair_index_ignores_ten_thousand_foreign_problems(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            old=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1)",(json.dumps(old),))
            foreign=json.dumps({'code':'foreign','operation':'producer:status','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'foreign-'||n,'background',%s,1 FROM generate_series(1,10000)n",(foreign,))
            store.db._db.execute('ANALYZE reader_problems')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_fetch_isolated_work()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.startswith('SELECT id,body::jsonb->>'))
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('probe','job:fetch-isolated:probe','background',%s,1)",(json.dumps(old),))
            store.db._db.execute('ANALYZE reader_problems')
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        print('repair rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_repair_thousand_independent_occurrences_is_batched(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed','failure','other' FROM generate_series(1,1000)n")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'old-'||j.id,'job:fetch-isolated:'||j.id,'background',json_build_object('code','work-fetch','operation','producer:fetch',
                   'fetch_job_id',j.id::text,'fetch_job_identity','job:work-fetch:'||j.item||':fetch:'||j.item,
                   'fetch_implicit_code',true,'objects',json_build_array(json_build_object('kind','material','id',j.item)))::text,1
                FROM jobs j WHERE j.kind='fetch'""")
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.recover_fetch_isolated_work()
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL AND body::jsonb->>'code'='other'").fetchone()[0]==1000
        print('repair SQL statements',len(queries))
        assert len(queries)<250
    finally:store.db.close()


def test_recovery_two_roots_implicit_and_explicit_remain_distinct(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-a'),('root-b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','root-a',true),('b','root-b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-a','article'),('root-b','article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            for feed,root,implicit in [('a','root-a',True),('b','root-b',False)]:
                body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:'+feed+':fetch:article',fetch_implicit_code=implicit,objects=[dict(kind='material',id=root)])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",('old-'+feed,'job:fetch-isolated:'+feed,json.dumps(body)))
        store.problems.recover_fetch_isolated_work()
        rows=store.db.execute("SELECT id,finished FROM reader_problems WHERE id IN ('old-a','old-b') ORDER BY id").fetchall()
        assert rows[0]['finished'] is None and rows[1]['finished'] is not None
        current=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert len(current)==2 and 'old-a' in {r['id'] for r in current} and 'old-b' not in {r['id'] for r in current}
    finally:store.db.close()


import pytest
@pytest.mark.parametrize('variant',['wrong_item','wrong_marker_type','conflicting_alias'])
def test_repair_preserves_unverified_isolated_body(tmp_path,postgres_url,variant):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            if variant=='wrong_item':body['fetch_job_identity']='job:work-fetch:article:fetch:another'
            if variant=='wrong_marker_type':body['fetch_job_id']=job
            if variant=='conflicting_alias':body.update(status_job_identity='job:work-fetch:other:fetch:article',status_job_target='article')
            raw=json.dumps(body)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1)",(raw,))
        store.problems.recover_fetch_isolated_work()
        row=store.db.execute("SELECT identity,body,finished FROM reader_problems WHERE id='old'").fetchone()
        assert tuple(row)==('job:fetch-isolated:token',raw,None)
    finally:store.db.close()


def test_repair_preserves_foreign_occupied_target(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            old=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            foreign=json.dumps(dict(code='other',operation='producer:filter',objects=[dict(kind='material',id='article')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:token','background',%s,1),('foreign','job:other:article:fetch:article','background',%s,1)",(json.dumps(old),foreign))
        store.problems.recover_fetch_isolated_work()
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0]==foreign
        current=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert len(current)==1 and current[0]['id']=='old' and json.loads(current[0]['body'])['code']=='other'
    finally:store.db.close()


def test_repair_resumes_after_committed_page(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,201)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed','failure','other' FROM generate_series(1,201)n")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'old-'||j.id,'job:fetch-isolated:'||j.id,'background',json_build_object('code','work-fetch','operation','producer:fetch',
                   'fetch_job_id',j.id::text,'fetch_job_identity','job:work-fetch:'||j.item||':fetch:'||j.item,
                   'fetch_implicit_code',true,'objects',json_build_array(json_build_object('kind','material','id',j.item)))::text,1
                FROM jobs j WHERE j.kind='fetch'""")
        store.db.close()
        def fail(point):
            if point=='fetch_isolated_work_recovery_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):Store(tmp_path,fail,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL AND body::jsonb->>'code'='other'").fetchone()[0]==201
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-isolated-work-recovered-v1'").fetchone()
    finally:store.db.close()


def test_repair_duplicate_isolates_chooses_oldest_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-isolated-work-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','failure','other') RETURNING id").fetchone()[0]
            body=dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:article:fetch:article',fetch_implicit_code=True,objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:old','background',%s,1),('dup','job:fetch-isolated:dup','background',%s,2)",(json.dumps(body),json.dumps(body)))
        store.problems.recover_fetch_isolated_work()
        current=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchall()
        assert [r['id'] for r in current]==['old']
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='dup'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_index_migration_preserves_rows_and_rolls_back(postgres_database_url):
    import psycopg
    from oko.storage.postgres_migrations import Migration,migrate,verify
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:76])
        body=json.dumps(dict(code='work-fetch',operation='producer:fetch',fetch_job_id='1',fetch_job_identity='job:work-fetch:f:fetch:p',fetch_implicit_code=True,objects=[dict(kind='material',id='p')]))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-isolated:old','background',%s,1)",(body,))
        before=db.execute("SELECT id,identity,origin,body,created,finished,outcome FROM reader_problems WHERE id='old'").fetchone()
        migration=MIGRATIONS[76]
        failing=Migration(77,migration.name,migration.sql+'; SELECT nonexistent FROM reader_problems')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:76],failing))
        assert db.execute("SELECT to_regclass('reader_fetch_work_job_owner')").fetchone()[0] is None
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==76
        assert db.execute("SELECT id,identity,origin,body,created,finished,outcome FROM reader_problems WHERE id='old'").fetchone()==before
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
        assert db.execute("SELECT id,identity,origin,body,created,finished,outcome FROM reader_problems WHERE id='old'").fetchone()==before
