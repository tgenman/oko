import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.collection.adapters import AdapterIssue,AdapterErrorKind


@pytest.mark.parametrize('code',['identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'])
def test_adapter_profile_error_is_saved_before_status_get(tmp_path,postgres_url,code):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue(code,'failure',AdapterErrorKind.temporary))
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row
    finally:s.db.close()


def test_profile_visibility_suppresses_and_restores_media_error(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            s.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('profile_unavailable','failure',AdapterErrorKind.temporary))
        old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        service=ConnectionService(lambda:s)
        assert not service.checked('personal',1,202,2)
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert service.checked('personal',1,101,3)
        assert s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


@pytest.mark.parametrize('code',['identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'])
def test_recovery_preserves_unambiguous_legacy_profile_media(tmp_path,postgres_url,code):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-profile-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure',%s) RETURNING id",(code,)).fetchone()[0]
            body=json.dumps(dict(code=code,section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(f'job:{code}:m:download_media:m',body))
        s.problems.recover_media_profile_problems()
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]=='old'
    finally:s.db.close()


def test_recovery_closes_unambiguous_legacy_when_profile_already_suppresses(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-profile-problems-recovered-v1'")
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'identity_mismatch',1)")
            s.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            s.problems.sync_profiles(100)
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='profile_unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:profile_unavailable:f:download_media:m','background',%s,1)",(body,))
        s.problems.recover_media_profile_problems()
        assert s.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is not None
        assert s.db.execute('SELECT identity_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:s.db.close()


def test_finish_does_not_close_one_of_ambiguous_profile_occurrences(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('download_media','m','running',1,'failure','profile_unavailable') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            body=json.loads(old['body']);body['media_job_identity']=old['identity']
            s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),old['id']))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('duplicate','job:media-isolated:other','background',%s,1)",(json.dumps(body),))
            before={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
            s.jobs.finish(Job(job,'download_media','m',1))
        after={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
        assert after==before
    finally:s.db.close()


def test_profile_media_adds_shared_root_without_get(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('b','p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
        with transaction(s.db):
            effects=CollectionEffects();assert publication_links.sync(s,'a',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
            s.jobs.apply_collection(effects)
        after={r['identity'] for r in s.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
        assert after=={'job:profile_unavailable:x:download_media:m','job:profile_unavailable:y:download_media:m'}
    finally:s.db.close()


def test_hidden_profile_media_gains_unsuppressed_root_without_get(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','telegram','telegram','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'identity_mismatch',1)")
            s.subscriptions.telegram.put_binding('y',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            s.problems.sync_profiles(100)
            s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('b','p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
        assert not s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        with transaction(s.db):
            effects=CollectionEffects();assert publication_links.sync(s,'a',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
            s.jobs.apply_collection(effects)
        after={r['identity'] for r in s.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
        assert after=={'job:profile_unavailable:x:download_media:m'}
    finally:s.db.close()


def test_profile_media_does_not_overwrite_foreign_or_protected_record(tmp_path,postgres_url):
    import hashlib,json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        identity='job:profile_unavailable:m:download_media:m'
        body=json.dumps(dict(code='profile_unavailable',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,foreign_evidence='preserve'))
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign',%s,'background',%s,1)",(identity,body))
            s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('profile_unavailable','failure',AdapterErrorKind.temporary))
        s.problems.snapshot(100)
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(body,None)
        assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_owned_profile_media_status_get_is_read_only(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('profile_unavailable','failure',AdapterErrorKind.temporary))
        before=[tuple(row) for row in s.db.execute('SELECT id,identity,body,finished,xmin::text FROM reader_problems ORDER BY id')]
        trace=[];s.db.set_trace_callback(trace.append)
        try:
            first=s.problems.snapshot(101);second=s.problems.snapshot(101)
        finally:s.db.set_trace_callback(None)
        after=[tuple(row) for row in s.db.execute('SELECT id,identity,body,finished,xmin::text FROM reader_problems ORDER BY id')]
        assert before==after
        assert [p['id'] for p in first['current'] if p['code']=='profile_unavailable']==[p['id'] for p in second['current'] if p['code']=='profile_unavailable']
        assert not any(q.lstrip().upper().startswith(('UPDATE ','INSERT ','DELETE ')) for q in trace)
    finally:s.db.close()


def test_profile_recovery_does_not_adopt_protected_legacy(tmp_path,postgres_url):
    import hashlib,json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        ident='job:profile_unavailable:m:download_media:m'
        body=json.dumps(dict(code='profile_unavailable',operation='producer:status',objects=[dict(kind='material',id='p')]))
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-profile-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable')")
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(ident,body))
            marker='fetch-null-protected:'+hashlib.sha256(b'old').hexdigest()
            s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2','old',ident])))
        s.problems.recover_media_profile_problems()
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
    finally:s.db.close()


def test_profile_media_recovery_resumes_after_page(tmp_path,postgres_url,monkeypatch):
    import pytest
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-profile-problems-recovered-v1'")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','orphan-'||n,'failed','failure','profile_unavailable' FROM generate_series(1,101)n")
        old=s.fault
        def crash(point):
            if point=='media_profile_problems_recovery_batch':raise RuntimeError('interrupted')
            return old(point)
        monkeypatch.setattr(s,'fault',crash)
        with pytest.raises(RuntimeError,match='interrupted'):s.problems.recover_media_profile_problems()
        before={r['id'] for r in s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_profile_event'='true'")}
        assert len(before)==100
        monkeypatch.setattr(s,'fault',old);s.problems.recover_media_profile_problems()
        after={r['id'] for r in s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_profile_event'='true'")}
        assert len(after)==101 and before.issubset(after)
    finally:s.db.close()


def test_profile_suppression_rolls_back_together_with_profile(tmp_path,postgres_url):
    import pytest
    from oko.services.connections import ConnectionService
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            s.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('profile_unavailable','failure',AdapterErrorKind.temporary))
        old=s.db.execute("SELECT id,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(s.db):
                assert not ConnectionService(lambda:s).checked('personal',1,202,2)
                assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()[0] is not None
                raise RuntimeError('rollback')
        row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
        assert row['body']==old['body'] and row['finished'] is None
        assert s.subscriptions.telegram.profile('personal')['state']=='ready'
    finally:s.db.close()


def test_different_profile_media_codes_open_new_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import previous_candidates
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(s.db):
            previous=previous_candidates(s,job,101)
            s.db.execute("UPDATE jobs SET problem_code='account_identity_mismatch' WHERE id=%s",(job,))
            s.problems.sync_media_problem(job,previous,101)
        current=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert current and current[0]!=old
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:s.db.close()


def test_unproven_finished_profile_legacy_stays_unchanged_after_get(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='profile_unavailable',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-profile-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','ready')")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','done',NULL,NULL)")
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:profile_unavailable:m:download_media:m','background',%s,1)",(body,))
        s.problems.recover_media_profile_problems();s.problems.snapshot(100)
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
    finally:s.db.close()


def test_profile_media_real_http_get_is_read_only_and_etag_stable(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            s=app.state.host.require_store();app.state.host.execution.reader_advance=None
            with transaction(s.db):
                s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
                s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('p','rss','p','https://example.test/p')")
                s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
                job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('profile_unavailable','failure',AdapterErrorKind.temporary))
            return tuple(s.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone())
        before=client.portal.call(setup)
        first=client.get('/api/v3/status/details');assert first.status_code==200
        assert any(p['id']==before[0] for p in first.json()['problems']['current'])
        second=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert second.status_code==304
        after=client.portal.call(lambda:tuple(app.state.host.require_store().db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(before[0],)).fetchone()))
        assert after==before


def test_profile_orphan_to_file_publish_moves_or_finishes_old_occurrence(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s)",(__import__('json').dumps({'blocks':[],'extra':{'_reader':{'title':'P'}}}),))
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
        before=s.db.execute("SELECT id,identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert before and before['identity']=='job:profile_unavailable:m:download_media:m'
        media=dict(id='m',publication_id='p',resource_key='image:1',source_locator='https://example.test/f',name='file',mime_type=None,size_bytes=None,storage_key=None,status='pending_download',reason='',resource_identity=None)
        with transaction(s.db):
            effects=CollectionEffects();s.publications.publish('p',({'blocks':[],'extra':{'_reader':{'title':'P'}}},[media]),'x',effects);s.jobs.apply_collection(effects)
        active=[dict(r) for r in s.db.execute("SELECT id,identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))]
        assert len(active)==1 and active[0]['identity']=='job:profile_unavailable:f:download_media:m'
    finally:s.db.close()


def test_profile_suppresses_one_shared_root_without_changing_other_id(tmp_path,postgres_url):
    from oko.services.connections import ConnectionService
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','telegram','telegram','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            s.subscriptions.telegram.put_binding('x',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            s.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
        before={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
        assert len(before)==2
        assert not ConnectionService(lambda:s).checked('personal',1,202,2)
        after={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
        assert after=={'job:profile_unavailable:y:download_media:m':before['job:profile_unavailable:y:download_media:m']}
    finally:s.db.close()


def test_recovery_suppresses_one_legacy_root_without_changing_other_id(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-profile-problems-recovered-v1'")
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','telegram','telegram','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'identity_mismatch',1)")
            s.subscriptions.telegram.put_binding('x',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            s.problems.sync_profiles(100)
            s.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
            for feed,item in (('x','a'),('y','b')):
                body=json.dumps(dict(code='profile_unavailable',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id=item)],action=None,operation='producer:status',state='current',next_at=None))
                s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(f'old-{feed}',f'job:profile_unavailable:{feed}:download_media:m',body))
        s.problems.recover_media_profile_problems()
        assert s.db.execute("SELECT finished FROM reader_problems WHERE id='old-x'").fetchone()[0] is not None
        row=s.db.execute("SELECT id FROM reader_problems WHERE identity='job:profile_unavailable:y:download_media:m' AND finished IS NULL").fetchone()
        assert row and row[0]=='old-y'
    finally:s.db.close()
