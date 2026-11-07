import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_processor_change_invalidates_http_etag_and_rollback_preserves_it(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        def seed():
            store=app.state.host.require_store()
            with store.db:
                return store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        job_id=client.portal.call(seed)
        first=client.get('/api/v3/status/details')
        assert first.status_code==200
        etag=first.headers['etag']
        assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        def fail_then_rollback():
            store=app.state.host.require_store()
            try:
                with transaction(store.db):
                    store.jobs.retry(Job(job_id,'read','feed',1),1,'missing',code='processor-config')
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        client.portal.call(fail_then_rollback)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        def retry():
            store=app.state.host.require_store()
            store.jobs.retry(Job(job_id,'read','feed',1),1,'missing',code='processor-config')
        client.portal.call(retry)
        updated=client.get('/api/v3/status/details',headers={'If-None-Match':etag})
        assert updated.status_code==200 and updated.headers['etag']!=etag
        assert any(p['code']=='processor_not_configured' for p in updated.json()['problems']['current'])
        statements=[]
        client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(statements.append))
        try:
            assert client.get('/api/v3/status/details',headers={'If-None-Match':updated.headers['etag']}).status_code==304
        finally:
            client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(None))
        assert not any('WHERE processor_visible ORDER BY' in sql for sql in statements)


def test_configure_codex_closes_last_preclaim_problem_before_status_get(tmp_path,postgres_url):
    from oko.processing.execution import CodexChoice, CodexSettings
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('read','feed','running',1,%s) RETURNING id",(json.dumps({'preclaim_blocked':True}),)).fetchone()[0]
        store.jobs.retry(Job(job_id,'read','feed',1),1,'missing',code='processor-config')
        choice=CodexChoice(model='synthetic',reasoning_effort='low',fast=False)
        store.configure_codex(CodexSettings(filter=choice,theses=choice))
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_mark_viewed_keeps_processor_problem_without_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,members) VALUES ('item','rss','item',0,'[]')")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='item'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'filter','item',1),1,'missing',code='processor-config')
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        assert store.viewed_set(['item'])
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(row[0],)).fetchone()
    finally:store.db.close()



def test_processor_problem_created_after_viewing_remains_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,members,feed_id) VALUES ('item','rss','item',1,'[]','feed')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'filter','item',1),1,'missing',code='processor-config')
        row=store.db.execute("SELECT body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        assert row is not None
        candidate=store.problems.processor_configuration_candidate(store.wall_clock())
        assert candidate is not None
        assert {'kind': 'material', 'id': 'item'} in candidate['objects']
    finally:store.db.close()

def test_http_etag_after_caption_deadline_without_host_tick(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with store.db:
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
                job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
            store.youtube.blocked(clock[0],900)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        paused=client.get('/api/v3/status/details')
        assert paused.status_code==200
        clock[0]=1001.0
        after=client.get('/api/v3/status/details',headers={'If-None-Match':paused.headers['etag']})
        assert after.status_code==200
        problem=next(p for p in after.json()['problems']['current'] if p['code']=='processor_not_configured')
        def before_tick():
            store=app.state.host.require_store()
            assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
            store.read_versions.advance(wall=clock[0])
            row=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
            assert row and row[0]==problem['id']
        client.portal.call(before_tick)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304
        committed=client.get('/api/v3/status/details')
        assert committed.headers['etag']==after.headers['etag']
        assert committed.json()['problems']==after.json()['problems']
