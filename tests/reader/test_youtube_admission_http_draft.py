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
            store.youtube.blocked(100,900)
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


def test_late_tick_after_retention_preserves_etag(tmp_path,postgres_url):
    clock=[100.]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            store.youtube.blocked(100,900)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200
        clock[0]=1000+86401
        gone=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert gone.status_code==200,gone.text
        assert not any(p['code']=='youtube_rate_limited' for p in gone.json()['problems']['current']+gone.json()['problems']['recent'])
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(wall=clock[0]))
        assert client.get('/api/v3/status/details',headers={'If-None-Match':gone.headers['etag']}).status_code==304


def test_unrepresentable_legacy_pause_is_visible_without_crashing(tmp_path,postgres_url):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    with transaction(store.db):store.db.execute("UPDATE youtube_access SET blocked_until='Infinity'::float8 WHERE id=1")
    store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        card=next(p for p in response.json()['problems']['current'] if p['code']=='youtube_rate_limited')
        assert 'Время следующей попытки недоступно' in card['consequence'] and card['retry_at'] is None
        assert client.portal.call(lambda:app.state.host.require_store().db.execute('SELECT blocked_until FROM youtube_access WHERE id=1').fetchone()[0])==float('inf')


def test_null_display_time_retains_valid_expiry_and_etag(tmp_path,postgres_url):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    with transaction(store.db):store.db.execute('UPDATE youtube_access SET blocked_until=200,next_at=NULL WHERE id=1')
    store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        client.portal.call(lambda:setattr(app.state.host.execution,'reader_advance',None))
        before=client.get('/api/v3/status/details')
        assert before.status_code==200,before.text
        card=next(p for p in before.json()['problems']['current'] if p['code']=='youtube_rate_limited')
        assert card['retry_at'] is None
        clock[0]=201
        due=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert due.status_code==200,due.text
        assert any(p['id']==card['id'] for p in due.json()['problems']['recent'])
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(wall=clock[0]))
        assert client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']}).status_code==304


def test_empty_legacy_id_stays_stable_through_expiry(tmp_path,postgres_url):
    import json
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    legacy=dict(code='youtube-admission',section='wait',title='YouTube ограничил запросы',consequence='Ожидают субтитров: 0.',next_step='Око повторит получение после паузы.',objects=[],action=None,operation='producer:status',state='retrying',next_at=200.)
    with transaction(store.db):
        store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
        store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','youtube:admission','background',%s,50)",(json.dumps(legacy),))
    store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        client.portal.call(lambda:setattr(app.state.host.execution,'reader_advance',None))
        before=client.get('/api/v3/status/details');assert before.status_code==200,before.text
        card=next(p for p in before.json()['problems']['current'] if p['code']=='youtube_rate_limited')
        assert card['id']
        clock[0]=201
        due=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']});assert due.status_code==200,due.text
        assert any(p['id']==card['id'] for p in due.json()['problems']['recent'])
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(wall=clock[0]))
        assert client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']}).status_code==304


def test_unrepresentable_unpaused_next_at_is_waiting_not_ready(tmp_path,postgres_url):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    with transaction(store.db):store.db.execute("UPDATE youtube_access SET blocked_until=0,next_at='Infinity'::float8 WHERE id=1")
    store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert response.json()['youtube']['admission']=='waiting' and response.json()['youtube']['retry_at'] is None
        direct=client.get('/api/v3/youtube')
        assert direct.status_code==200,direct.text
        assert direct.json()['admission']=='waiting' and direct.json()['retry_at'] is None


def test_pause_extension_keeps_occurrence_and_replaces_boundary(tmp_path,postgres_url):
    clock=[100.]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def block():
            store=app.state.host.require_store();store.youtube.blocked(clock[0],900)
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
