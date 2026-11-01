from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


def test_success_during_wait_cancels_future_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            assert store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            store.jobs.finish(claimed)
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        assert not any(p['code']=='work-download_media' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_timer_pause_and_reserved_episode_roll_back_atomically(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=[tuple(row) for row in store.db.execute('SELECT id,identity,body,finished,subtitle_due FROM reader_problems ORDER BY id')]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.youtube.start(100)
                assert store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
                raise RuntimeError('rollback')
        assert [tuple(row) for row in store.db.execute('SELECT id,identity,body,finished,subtitle_due FROM reader_problems ORDER BY id')]==before
        assert store.youtube._limits()['next_at']==0
    finally:store.db.close()


def test_event_owned_subtitle_etag_changes_exactly_at_deadline(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                store.youtube.start(100)
                store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200
        clock[0]=109
        assert client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']}).status_code==304
        clock[0]=111
        after=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert after.status_code==200
        assert len(after.json()['problems']['current'])>len(before.json()['problems']['current'])
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=111,wall=111))
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304


def test_restart_after_deadline_preserves_reserved_virtual_id(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            reserved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
        store.db.close();clock[0]=111
        store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        assert any(p['id']==reserved for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()
