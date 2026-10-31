import json
import pytest
from tests.support.storage import Store


def test_restart_preserves_legacy_ids_after_group_page_interruption(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key IN ('experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0 FROM generate_series(1,201)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,201)n")
        store.problems.snapshot(1)
        before={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'code'='material-incomplete' AND finished IS NULL")}
        assert len(before)==201
        store.db.close()
        def fault(point):
            if point=='experimental_incomplete_group_problem_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):Store(tmp_path,fault,database_url=postgres_url,wall_clock=lambda:100)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        after={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'code'='material-incomplete' AND finished IS NULL")}
        assert after==before
        assert store.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-problems-v1'").fetchone()
    finally:store.db.close()


def test_recovery_1000_groups_batches_and_ignores_10000_foreign_problems(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import recover_group_problems
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-problems-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
            foreign=json.dumps({'code':'foreign','operation':'producer:status','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'foreign-'||n,'background',%s,1 FROM generate_series(1,10000)n",(foreign,))
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        try:recover_group_problems(store)
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete' AND finished IS NULL").fetchone()[0]==1000
        print('recovery SQL',len(queries))
        assert len(queries)<250
        query=next(q for q in queries if q.startswith('SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND left(identity,11)'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        print('legacy index rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_group_recovery_preserves_empty_id_and_ambiguous_legacy(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key IN ('experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            body=dict(code='material-incomplete',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','incomplete:f:p','background',%s,1),('foreign','incomplete:other:a:b','background',%s,1)",(json.dumps(body),json.dumps(body)))
        foreign=tuple(store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE id='foreign'").fetchone())
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]==''
        assert tuple(store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE id='foreign'").fetchone())==foreign
    finally:store.db.close()


def test_malformed_legacy_incomplete_is_visible_without_mutation(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    body=json.dumps({'code':'material-incomplete','operation':'producer:status','objects':[None],'request_id':'bad id'})
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','incomplete:ambiguous:a:b','background',%s,1e300)",(body,))
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']=='legacy' and p['code']=='material_incomplete' and not p['affected'] for p in response.json()['problems']['current'])
    with psycopg.connect(postgres_url,autocommit=True) as db:
        assert db.execute("SELECT body,finished FROM reader_problems WHERE id='legacy'").fetchone()==(body,None)


def test_unknown_content_does_not_close_legacy_occurrence_during_recovery(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key IN ('experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':{'unknown':True}}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,2)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            body=dict(code='material-incomplete',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None)
            saved=json.dumps(body)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','incomplete:f:p','background',%s,1)",(saved,))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(saved,None)
        with pytest.raises(ValueError,match='Unrecognized publication completeness'):store.problems.snapshot(1)
    finally:store.db.close()


def test_group_recovery_page_uses_index_without_scanning_ten_thousand_groups(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||lpad(n::text,5,'0'),'rss','p-'||n,0,1,%s,0,'incomplete:f:p-'||lpad(n::text,5,'0'),'f',true FROM generate_series(1,10000)n",(raw,))
            store.db._db.execute('ANALYZE publications')
        q='SELECT incomplete_group_key COLLATE "C" AS incomplete_group_key FROM publications WHERE incomplete_group_key IS NOT NULL GROUP BY incomplete_group_key COLLATE "C" ORDER BY incomplete_group_key COLLATE "C" LIMIT 100'
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q).fetchone()[0][0]['Plan']
        print('group recovery first page rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_valid_body_with_invalid_created_is_not_overwritten(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key IN ('experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            body=dict(code='material-incomplete',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None)
            saved=json.dumps(body)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','incomplete:f:p','background',%s,1e300)",(saved,))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(saved,None)
        other=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'incomplete_group_identity'='incomplete:f:p' AND finished IS NULL").fetchone()
        assert other and other[0]!='old'
    finally:store.db.close()
