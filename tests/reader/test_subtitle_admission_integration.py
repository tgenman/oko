from tests.support.youtube_action import youtube_action
from oko.storage.transaction import transaction
from oko.domain.job import Job


def test_http_etag_stays_stable_when_admission_and_subtitle_materialize(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
                store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','youtube','p')")
                store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                youtube_action(store, 'blocked', 100,20);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            app.state.host.execution.reader_advance=None
        client.portal.call(setup);clock[0]=1001
        before=client.get('/api/v3/status/details');assert before.status_code==200
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=1001,wall=1001))
        after=client.get('/api/v3/status/details');assert after.status_code==200
        assert before.json()==after.json()
        assert before.headers['etag']==after.headers['etag']
