from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from tests.support.storage import Store

def test_fetch_identity_error_is_persisted_before_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert row is not None
    finally:store.db.close()

def test_fetch_identity_get_does_not_rewrite_owned_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert before
        assert any(p['id']==before['id'] for p in store.problems.snapshot(100)['current'])
        after=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()

def test_profile_visibility_transfers_fetch_problem_without_get(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        identity='job:identity-unconfirmed:f:fetch:p'
        old=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert old
        service=ConnectionService(lambda:store)
        assert not service.checked('personal',1,202,2)
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert service.checked('personal',1,101,3)
        again=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert again and again[0]!=old[0]
    finally:store.db.close()

def test_restart_recovers_legacy_fetch_problem_before_get(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    legacy=dict(code='identity-unconfirmed',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,reason='unconfirmed')
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','unconfirmed','identity-unconfirmed')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:identity-unconfirmed:f:fetch:p','background',%s,1)",(json.dumps(legacy),))
            store.db.execute("DELETE FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert row and row['id']=='old' and json.loads(row['body']).get('operation')=='producer:fetch'
    finally:store.db.close()

def test_restart_finishes_suppressed_legacy_fetch_without_get(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    legacy=dict(code='identity-unconfirmed',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,reason='unconfirmed')
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'identity_mismatch',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','unconfirmed','identity-unconfirmed')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:identity-unconfirmed:f:fetch:p','background',%s,1)",(json.dumps(legacy),))
            store.db.execute("DELETE FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is not None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
    finally:store.db.close()

def test_status_does_not_scan_ten_thousand_owned_identity_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,identity_event_owned) SELECT 'fetch','p-'||n,'failed','err','identity-unconfirmed',true FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        statements=[];store.db.set_trace_callback(statements.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in statements if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()

def test_profile_transition_does_not_scan_ten_thousand_foreign_jobs(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','f','failed','err','identity-unconfirmed')")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'fetch','foreign-'||n,'other-'||n,'failed','err','identity-unconfirmed' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE feed_publications')
        statements=[];store.db.set_trace_callback(statements.append)
        try:assert not ConnectionService(lambda:store).checked('personal',1,202,2)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in statements if 'WITH roots AS MATERIALIZED' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['f'],['f'])).fetchone()[0][0]['Plan']
        print('profile transition scan rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()

def test_profile_transition_hundred_affected_jobs_query_cost(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,100)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,100)n")
            rows=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'fetch','p-'||n,'f','failed','err','identity-unconfirmed' FROM generate_series(1,100)n RETURNING id").fetchall()
            for row in rows:store.problems.sync_fetch(row['id'],[],100)
        statements=[];store.db.set_trace_callback(statements.append)
        try:assert not ConnectionService(lambda:store).checked('personal',1,202,2)
        finally:store.db.set_trace_callback(None)
        print('profile transition 100 jobs SQL',len(statements))
        assert len(statements)<500,len(statements)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='identity-unconfirmed' AND body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()[0]==0
        reopened=[];store.db.set_trace_callback(reopened.append)
        try:assert ConnectionService(lambda:store).checked('personal',1,101,3)
        finally:store.db.set_trace_callback(None)
        print('profile reopen 100 jobs SQL',len(reopened))
        assert len(reopened)<500,len(reopened)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='identity-unconfirmed' AND body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()[0]==100
    finally:store.db.close()

def test_profile_suppresses_only_matching_linked_root(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','telegram','telegram','connected','[]'),('b','b','B','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('a',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('ra'),('rb'),('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','ra',true),('b','rb',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('ra','p'),('rb','p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        ids={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:identity-unconfirmed:%%' AND finished IS NULL")}
        assert set(ids)=={'job:identity-unconfirmed:a:fetch:p','job:identity-unconfirmed:b:fetch:p'}
        service=ConnectionService(lambda:store)
        assert not service.checked('personal',1,202,2)
        current={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:identity-unconfirmed:%%' AND finished IS NULL")}
        assert current=={'job:identity-unconfirmed:b:fetch:p':ids['job:identity-unconfirmed:b:fetch:p']}
        assert service.checked('personal',1,101,3)
        current={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:identity-unconfirmed:%%' AND finished IS NULL")}
        assert set(current)==set(ids) and current['job:identity-unconfirmed:b:fetch:p']==ids['job:identity-unconfirmed:b:fetch:p']
    finally:store.db.close()

def test_profile_suppression_rolls_back_with_checked_state(tmp_path,postgres_url):
    import pytest
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                assert not ConnectionService(lambda:store).checked('personal',1,202,2)
                assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
                raise RuntimeError('rollback')
        after=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
        assert store.db.execute("SELECT state FROM telegram_profiles WHERE role='personal'").fetchone()[0]=='ready'
    finally:store.db.close()

def test_foreign_canonical_is_not_rewritten_or_finished_by_get(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=dict(code='identity-unconfirmed',section='object',title='foreign',consequence='saved',next_step='wait',objects=[dict(kind='material',id='foreign')],action=None,operation='producer:delete',state='current',next_at=None)
        raw=json.dumps(foreign)
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:identity-unconfirmed:f:fetch:p','background',%s,1)",(raw,))
            store.jobs.retry(Job(jid,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        store.problems.snapshot(100)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['body']==raw and row['finished'] is None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(jid),)).fetchone()
    finally:store.db.close()

def test_identity_unconfirmed_http_etag_and_read_only(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store();app.state.host.execution.reader_advance=None
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
                store.db.execute("INSERT INTO publications(id) VALUES ('p')")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
                store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
            return tuple(store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone())
        before=client.portal.call(setup)
        response=client.get('/api/v3/status/details');assert response.status_code==200,response.text
        assert any(problem['id']==before[0] for problem in response.json()['problems']['current'])
        assert client.get('/api/v3/status/details',headers={'If-None-Match':response.headers['etag']}).status_code==304
        def saved():return tuple(app.state.host.require_store().db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone())
        assert client.portal.call(saved)==before

def test_primary_owner_change_preserves_identity_occurrence_before_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','telegram','telegram','connected','[]'),('b','b','B','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','telegram','p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(jid,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:identity-unconfirmed:a:fetch:p' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        new=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:identity-unconfirmed:b:fetch:p' AND finished IS NULL").fetchone()
        assert new and new[0]==old
    finally:store.db.close()

def test_profile_checked_waits_graph_before_profile_row(tmp_path,postgres_url):
    import threading,psycopg
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    other=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[]
    try:
        with transaction(store.db):store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
        def update():
            try:ConnectionService(lambda:store).checked('personal',1,202,2)
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with other.transaction():
            other.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            t=threading.Thread(target=update);t.start()
            assert not done.wait(.3)
            other.execute("SET LOCAL lock_timeout='500ms'")
            other.execute("UPDATE telegram_profiles SET checked=checked WHERE role='personal'")
        t.join(5);assert done.is_set() and not errors
    finally:other.close();store.db.close()

def test_identity_recovery_batches_thousand_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed','failure','identity-unconfirmed' FROM generate_series(1,1000)n")
        statements=[];store.db.set_trace_callback(statements.append)
        try:store.problems.recover_fetch_identity_unconfirmed()
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM jobs WHERE identity_event_owned AND problem_code='identity-unconfirmed'").fetchone()[0]==1000
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='identity-unconfirmed'").fetchone()[0]==1000
        print('identity recovery 1000 SQL',len(statements))
        assert len(statements)<500,len(statements)
    finally:store.db.close()

def test_profile_switch_http_etag_tracks_suppression_and_reopen(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.services.connections import ConnectionService
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store();app.state.host.execution.reader_advance=None
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
                store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
                store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
                store.db.execute("INSERT INTO publications(id) VALUES ('p')")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
                store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        client.portal.call(setup)
        first=client.get('/api/v3/status/details');assert first.status_code==200
        def checked(user):return ConnectionService(lambda:app.state.host.require_store()).checked('personal',1,user,2)
        assert not client.portal.call(checked,202)
        second=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert second.status_code==200
        assert second.headers['etag']!=first.headers['etag']
        assert client.portal.call(checked,101)
        third=client.get('/api/v3/status/details',headers={'If-None-Match':second.headers['etag']});assert third.status_code==200
        assert third.headers['etag']!=second.headers['etag']

def test_recovery_profile_suppression_preserves_other_root_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','telegram','telegram','connected','[]'),('b','b','B','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('ra'),('rb'),('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','ra',true),('b','rb',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('ra','p'),('rb','p')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(jid,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
            before={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:identity-unconfirmed:%%' AND finished IS NULL")}
            profile=dict(code='identity-unconfirmed',section='action',title='p',consequence='p',next_step='p',objects=[dict(kind='feed',id='a')],action=None,operation='producer:profile',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('profile-old','profile:a','background',%s,1)",(json.dumps(profile),))
            store.db.execute("UPDATE jobs SET identity_event_owned=false WHERE id=%s",(jid,))
            store.db.execute("DELETE FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'")
        store.problems.recover_fetch_identity_unconfirmed()
        active={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:identity-unconfirmed:%%' AND finished IS NULL")}
        assert active=={'job:identity-unconfirmed:b:fetch:p':before['job:identity-unconfirmed:b:fetch:p']}
    finally:store.db.close()

def test_profile_transition_thousand_affected_jobs_bounded(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:1000)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
            rows=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'fetch','p-'||n,'f','failed','err','identity-unconfirmed' FROM generate_series(1,1000)n RETURNING id").fetchall()
            for row in rows:store.problems.sync_fetch(row['id'],[],1000)
        statements=[];store.db.set_trace_callback(statements.append)
        try:assert not ConnectionService(lambda:store).checked('personal',1,202,2)
        finally:store.db.set_trace_callback(None)
        print('profile transition 1000 jobs SQL',len(statements))
        assert len(statements)<2500,len(statements)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='identity-unconfirmed' AND body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()[0]==0
        reopened=[];store.db.set_trace_callback(reopened.append)
        try:assert ConnectionService(lambda:store).checked('personal',1,101,3)
        finally:store.db.set_trace_callback(None)
        print('profile reopen 1000 jobs SQL',len(reopened))
        assert len(reopened)<2500,len(reopened)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='identity-unconfirmed' AND body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_identity_recovery_restart_after_committed_page(tmp_path, postgres_url):
    import pytest
    store = Store(tmp_path, lambda _: None, database_url=postgres_url, wall_clock=lambda: 100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','restart-'||n,'failed','failure','identity-unconfirmed' FROM generate_series(1,110)n")
        def fail(point):
            if point == 'fetch_identity_unconfirmed_recovery_batch':
                raise RuntimeError('restart')
        store.fault = fail
        with pytest.raises(RuntimeError, match='restart'):
            store.problems.recover_fetch_identity_unconfirmed()
        old = {r['identity']: r['id'] for r in store.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='identity-unconfirmed'")}
        assert len(old) == 100
        store.fault = lambda _: None
        store.problems.recover_fetch_identity_unconfirmed()
        new = {r['identity']: r['id'] for r in store.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='identity-unconfirmed'")}
        assert len(new) == 110
        assert all(new[k] == v for k, v in old.items())
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'").fetchone()
    finally:
        store.db.close()
