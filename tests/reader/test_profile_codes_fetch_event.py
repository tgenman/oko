import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store
from oko.services.connections import ConnectionService

CODES=('profile_not_configured','profile_unavailable','account_identity_mismatch')

@pytest.mark.parametrize('code',CODES)
def test_profile_code_fetch_event_and_suppression(tmp_path,postgres_url,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code=code)
        identity=f'job:{code}:f:fetch:p'
        before=store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert before is not None
        assert any(p['id']==before['id'] for p in store.problems.snapshot(100)['current'])
        assert tuple(before)==tuple(store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(before['id'],)).fetchone())
        service=ConnectionService(lambda:store)
        assert not service.checked('personal',1,202,2)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()[0] is not None
        assert service.checked('personal',1,101,3)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone() is not None
    finally:store.db.close()

@pytest.mark.parametrize('code',CODES)
@pytest.mark.parametrize('suppressed',[False,True])
def test_legacy_profile_code_recovered_without_losing_occurrence(tmp_path,postgres_url,code,suppressed):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    identity=f'job:{code}:f:fetch:p'
    legacy=dict(code=code,section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,reason='failure')
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            if suppressed:
                store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'identity_mismatch',1)")
                store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure',%s)",(code,))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(identity,json.dumps(legacy)))
            store.db.execute("DELETE FROM state WHERE key='fetch-profile-codes-recovered-v1'")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        row=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',('old',)).fetchone()
        assert row and row['id']=='old'
        assert (row['finished'] is not None)==suppressed
        if not suppressed:assert json.loads(row['body'])['operation']=='producer:fetch'
    finally:store.db.close()

@pytest.mark.parametrize('code',CODES)
def test_profile_code_suppression_rolls_back_atomically(tmp_path,postgres_url,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code=code)
        before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                assert not ConnectionService(lambda:store).checked('personal',1,202,2)
                raise RuntimeError('rollback')
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(before)==tuple(after)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
    finally:store.db.close()

@pytest.mark.parametrize('code',CODES)
def test_profile_code_primary_owner_change_preserves_occurrence(tmp_path,postgres_url,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','telegram','telegram','connected',ARRAY[]::text[]),('b','b','B','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','telegram','p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code=code)
        old=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(f'job:{code}:a:fetch:p',)).fetchone()[0]
        store.remove_feed('a')
        new=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(f'job:{code}:b:fetch:p',)).fetchone()
        assert new and new[0]==old
    finally:store.db.close()

@pytest.mark.parametrize('code',CODES)
def test_profile_code_keeps_foreign_identity_unchanged(tmp_path,postgres_url,code):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=dict(code=code,section='object',title='foreign',consequence='saved',next_step='wait',objects=[dict(kind='material',id='foreign')],action=None,operation='producer:delete',state='current',next_at=None)
        raw=json.dumps(foreign)
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign',%s,'background',%s,1)",(f'job:{code}:f:fetch:p',raw))
            store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code=code)
        store.problems.snapshot(100)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['body']==raw and row['finished'] is None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'fetch_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
    finally:store.db.close()

@pytest.mark.parametrize('code',CODES)
def test_profile_code_real_http_unchanged_get_and_etag(tmp_path,postgres_url,code):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store();app.state.host.execution.reader_advance=None
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
                store.db.execute("INSERT INTO publications(id) VALUES ('p')")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
                store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code=code)
            return tuple(store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE identity=%s AND finished IS NULL',(f'job:{code}:f:fetch:p',)).fetchone())
        before=client.portal.call(setup)
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(problem['id']==before[0] for problem in response.json()['problems']['current'])
        assert client.get('/api/v3/status/details',headers={'If-None-Match':response.headers['etag']}).status_code==304
        def saved():return tuple(app.state.host.require_store().db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(before[0],)).fetchone())
        assert client.portal.call(saved)==before

def test_profile_code_change_finishes_old_and_creates_new_before_get(tmp_path,postgres_url):
    from oko.storage.fetch_job_problems import previous_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code='profile_unavailable')
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:profile_unavailable:f:fetch:p' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            before=previous_candidates(store,job,101)
            store.db.execute("UPDATE jobs SET problem_code='account_identity_mismatch' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,before,101)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        new=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:account_identity_mismatch:f:fetch:p' AND finished IS NULL").fetchone()
        assert new and new[0]!=old
    finally:store.db.close()

@pytest.mark.parametrize('code',CODES)
def test_profile_code_suppression_changes_only_one_linked_root(tmp_path,postgres_url,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','telegram','telegram','connected',ARRAY[]::text[]),('b','b','B','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('a',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('ra'),('rb'),('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','ra',true),('b','rb',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('ra','p'),('rb','p')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'failure',code=code)
        query="SELECT id,identity FROM reader_problems WHERE body::jsonb->>'code'=%s AND body::jsonb->>'operation'='producer:fetch' AND finished IS NULL"
        old={r['identity']:r['id'] for r in store.db.execute(query,(code,))}
        a=f'job:{code}:a:fetch:p';b=f'job:{code}:b:fetch:p'
        assert set(old)=={a,b}
        svc=ConnectionService(lambda:store);assert not svc.checked('personal',1,202,2)
        current={r['identity']:r['id'] for r in store.db.execute(query,(code,))}
        assert current=={b:old[b]}
        assert svc.checked('personal',1,101,3)
        current={r['identity']:r['id'] for r in store.db.execute(query,(code,))}
        assert set(current)=={a,b} and current[b]==old[b]
    finally:store.db.close()

def test_profile_code_recovery_resumes_after_committed_batch(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-profile-codes-recovered-v1'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','legacy-'||n,'failed','failure','profile_unavailable' FROM generate_series(1,101)n")
        def fail(point):
            if point=='fetch_identity_unconfirmed_recovery_batch':raise RuntimeError('restart')
        store.fault=fail
        with pytest.raises(RuntimeError,match='restart'):store.problems.recover_fetch_identity_unconfirmed(additional=True)
        old={r['identity']:r['id'] for r in store.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='profile_unavailable'")}
        assert len(old)==100
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-profile-codes-recovered-v1'").fetchone() is None
        store.fault=lambda _:None
        store.problems.recover_fetch_identity_unconfirmed(additional=True)
        new={r['identity']:r['id'] for r in store.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='profile_unavailable'")}
        assert len(new)==101 and all(new[k]==v for k,v in old.items())
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-profile-codes-recovered-v1'").fetchone()
    finally:store.db.close()
