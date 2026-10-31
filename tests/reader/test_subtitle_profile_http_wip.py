from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.collection.adapters import AdapterIssue,AdapterErrorKind

def test_profile_subtitle_real_http_get_is_read_only_and_etag_stable(tmp_path,postgres_url):
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
                s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('profile_unavailable','failure',AdapterErrorKind.temporary))
            return tuple(s.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone())
        before=client.portal.call(setup)
        first=client.get('/api/v3/status/details');assert first.status_code==200
        assert any(p['id']==before[0] for p in first.json()['problems']['current'])
        second=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert second.status_code==304
        after=client.portal.call(lambda:tuple(app.state.host.require_store().db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(before[0],)).fetchone()))
        assert after==before
