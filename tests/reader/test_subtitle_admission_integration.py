from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job

def test_wait_boundary_switches_admission_to_subtitle_without_get_write(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.blocked(100,20)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=store.problems.snapshot(100)
        assert any(p['code']=='youtube-admission' for p in before['current'])
        assert not any(p['code']=='work-download_media' for p in before['current'])
        trace=[];store.db.set_trace_callback(trace.append)
        try:after=store.problems.snapshot(1001)
        finally:store.db.set_trace_callback(None)
        assert not any(p['code']=='youtube-admission' for p in after['current'])
        assert any(p['code']=='work-download_media' for p in after['current'])
        assert not [sql for sql in trace if sql.lstrip().upper().startswith(('INSERT ','UPDATE ','DELETE '))]
    finally:store.db.close()


def test_unknown_admission_time_does_not_expose_subtitle_until_retimed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.db.execute('UPDATE youtube_access SET next_at=NULL WHERE id=1')
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=store.problems.snapshot(100)
        assert not any(p['code']=='work-download_media' for p in before['current'])
        store.youtube.start(100)
        assert any(p['code']=='work-download_media' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


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
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                store.youtube.blocked(100,20);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            app.state.host.execution.reader_advance=None
        client.portal.call(setup);clock[0]=1001
        before=client.get('/api/v3/status/details');assert before.status_code==200
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=1001,wall=1001))
        after=client.get('/api/v3/status/details');assert after.status_code==200
        assert before.json()==after.json()
        assert before.headers['etag']==after.headers['etag']


def test_unknown_strikes_and_deadline_keep_subtitle_reserved_until_success(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.db.execute('UPDATE youtube_access SET strikes=NULL,blocked_until=NULL WHERE id=1')
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            reserved=store.db.execute('SELECT id,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            assert reserved and reserved['subtitle_due']==float('inf')
        store.youtube.blocked(100,20)
        assert store.db.execute('SELECT blocked_until FROM youtube_access WHERE id=1').fetchone()[0] is None
        assert not any(p['code']=='work-download_media' for p in store.problems.snapshot(100)['current'])
        store.youtube.completed()
        assert any(p['id']==reserved['id'] for p in store.problems.snapshot(100)['current'])
    finally:store.db.close()
