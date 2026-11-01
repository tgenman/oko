import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.collection.adapters import AdapterIssue,AdapterErrorKind


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
