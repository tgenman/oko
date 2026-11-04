from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_pause_rolls_back_timer_occurrence_and_flags_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','other_web','p')")
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        before=store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE finished IS NULL AND body::jsonb->>\'fetch_job_id\'=%s',(str(job),)).fetchall()
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.youtube.start(100)
                assert store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
                raise RuntimeError('rollback')
        after=store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE finished IS NULL AND body::jsonb->>\'fetch_job_id\'=%s',(str(job),)).fetchall()
        assert [tuple(row) for row in before]==[tuple(row) for row in after]
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
        assert store.youtube.ready_at()<=100
    finally:store.db.close()


def test_http_etag_stable_when_virtual_fetch_is_materialized(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
                store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
                store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
            store.problems.recover_youtube_null_fetch();store.youtube.start(100)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup);clock[0]=111
        before=client.get('/api/v3/status/details');assert before.status_code==200
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=111,wall=111))
        after=client.get('/api/v3/status/details');assert after.status_code==200
        assert before.json()==after.json()
        assert before.headers['etag']==after.headers['etag']
