import json
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.transaction import transaction


def test_caption_next_at_boundary_invalidates_status_etag(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
                raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,content) VALUES ('p','youtube','p',0,%s)",(raw,))
                store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('download_media','m','pending','failed',NULL)")
                store.db.execute("UPDATE youtube_access SET next_at=200,blocked_until=0 WHERE id=1")
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200
        clock[0]=199.0
        assert client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']}).status_code==304
        clock[0]=201.0
        after=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        uncached=client.get('/api/v3/status/details')
        assert uncached.status_code==200
        assert uncached.json()['problems']['current'] != before.json()['problems']['current']
        assert after.status_code==200
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304
