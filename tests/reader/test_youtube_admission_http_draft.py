from tests.support.youtube_action import youtube_action
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.youtube_admission_jobs import recover_flags


def test_virtual_expiry_http_etag_and_xmin_across_tick(tmp_path,postgres_url):
    clock=[100.]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            recover_flags(store)
            store.youtube_admission_ready=True
            youtube_action(store, 'blocked', 100,900)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        def row():
            return tuple(app.state.host.require_store().db.execute("SELECT id,finished,xmin::text FROM reader_problems WHERE identity='youtube:admission'").fetchone())
        saved=client.portal.call(row)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200,before.text
        found=[p for p in before.json()['problems']['current'] if p['code']=='youtube_rate_limited']
        assert len(found)==1 and found[0]['id']==saved[0]
        assert client.portal.call(row)==saved
        clock[0]=1001
        due=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert due.status_code==200,due.text
        assert not [p for p in due.json()['problems']['current'] if p['code']=='youtube_rate_limited']
        assert any(p['id']==saved[0] for p in due.json()['problems']['recent'])
        assert client.portal.call(row)==saved
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(wall=clock[0]))
        after=client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']})
        assert after.status_code==304,after.text
        reread=client.get('/api/v3/status/details')
        assert reread.headers['etag']==due.headers['etag']
        assert reread.json()['problems']==due.json()['problems']


def test_pause_extension_keeps_occurrence_and_replaces_boundary(tmp_path,postgres_url):
    clock=[100.]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def block():
            store=app.state.host.require_store();youtube_action(store, 'blocked', clock[0],900)
            app.state.host.execution.reader_advance=None
        client.portal.call(block)
        first=client.get('/api/v3/status/details');assert first.status_code==200
        old=next(p for p in first.json()['problems']['current'] if p['code']=='youtube_rate_limited')
        clock[0]=500
        client.portal.call(block)
        extended=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert extended.status_code==200
        new=next(p for p in extended.json()['problems']['current'] if p['code']=='youtube_rate_limited')
        assert new['id']==old['id'] and new['retry_at']!=old['retry_at']
        clock[0]=1001
        assert client.get('/api/v3/status/details',headers={'If-None-Match':extended.headers['etag']}).status_code==304
