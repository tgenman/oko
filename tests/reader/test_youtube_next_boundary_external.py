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
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
                raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','pending','failed',NULL)")
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


def test_shorter_next_at_does_not_change_etag_during_longer_block(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):store.db.execute('UPDATE youtube_access SET next_at=200,blocked_until=300 WHERE id=1')
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200
        clock[0]=201
        assert client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']}).status_code==304
        clock[0]=301
        assert client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']}).status_code==200


def test_rollback_youtube_start_keeps_status_etag(tmp_path,postgres_url):
    import pytest
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        before=client.get('/api/v3/status/details')
        def mutate():
            store=app.state.host.require_store()
            with pytest.raises(RuntimeError,match='rollback'):
                with transaction(store.db):
                    store.youtube.start(clock[0])
                    raise RuntimeError('rollback')
        client.portal.call(mutate)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']}).status_code==304


def test_boundary_probe_ignores_10000_unrelated_publications_and_jobs(tmp_path,postgres_url):
    from tests.support.storage import Store
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) SELECT 'p-'||n,'rss','p-'||n,0 FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','p-'||n,'done' FROM generate_series(1,10000)n")
            store.db.execute('UPDATE youtube_access SET next_at=200,blocked_until=0 WHERE id=1')
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.read_versions.time_boundaries()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'greatest(next_at,blocked_until)' in q)
        assert 'publications' not in query
        params=(store.clock(),100.0,100.0,100.0-86400,None,100.0,100.0-86400,100.0,100.0-86400)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,params).fetchone()[0][0]['Plan']
        print('status boundary rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()
