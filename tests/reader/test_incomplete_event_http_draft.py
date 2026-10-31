import json
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import save_keys


def test_status_deadline_virtual_and_tick_keep_ids_etag_and_body(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
                raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube','a',0,1,%s,0),('b','youtube','b',0,1,%s,0)",(raw,raw))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
                save_keys(store.db,['a','b'])
            store.youtube.blocked(clock[0],900)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200
        assert not [p for p in before.json()['problems']['current'] if p['code']=='material_incomplete']
        def saved_xmin():
            return [tuple(r) for r in app.state.host.require_store().db.execute("SELECT id,xmin::text FROM reader_problems WHERE incomplete_due IS NOT NULL ORDER BY id")]
        saved=client.portal.call(saved_xmin)
        assert len(saved)==2
        clock[0]=1001
        due=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert due.status_code==200,due.text
        problems=[p for p in due.json()['problems']['current'] if p['code']=='material_incomplete']
        assert len(problems)==2 and {p['id'] for p in problems}=={r[0] for r in saved}
        assert client.portal.call(saved_xmin)==saved
        def tick():app.state.host.require_store().read_versions.advance(wall=clock[0])
        client.portal.call(tick)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']}).status_code==304
        after=client.get('/api/v3/status/details')
        assert after.headers['etag']==due.headers['etag']
        assert after.json()['problems']==due.json()['problems']


def test_multiple_tick_pages_keep_status_body_and_etag_stable(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
                raw=json.dumps({'extra':{'_reader':{'full':False}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,120)n",(raw,))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,120)n")
            store.youtube.blocked(clock[0],900)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        initial=client.get('/api/v3/status/details')
        clock[0]=1001
        due=client.get('/api/v3/status/details',headers={'If-None-Match':initial.headers['etag']})
        assert due.status_code==200
        assert len([p for p in due.json()['problems']['current'] if p['code']=='material_incomplete'])==120
        def tick():app.state.host.require_store().read_versions.advance(wall=clock[0])
        for _ in range(2):
            client.portal.call(tick)
            cached=client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']})
            assert cached.status_code==304
            actual=client.get('/api/v3/status/details')
            assert actual.headers['etag']==due.headers['etag']
            assert actual.json()['problems']==due.json()['problems']


def test_due_group_update_keeps_id_and_etag_before_and_after_tick(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
                raw=json.dumps({'extra':{'_reader':{'full':False,'captions':'missing'}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube','a',0,1,%s,0),('b','youtube','b',0,1,%s,0)",(raw,raw))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','failed','bad',NULL)")
                save_keys(store.db,['a','b']);store.incomplete_draft.mark(['a','b'])
            store.youtube.start(clock[0]);app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        group=next(p for p in before.json()['problems']['current'] if p['code']=='material_incomplete')
        assert {o['material_id'] for o in group['affected']}=={'a','b'}
        clock[0]=111
        due=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert due.status_code==200
        changed=next(p for p in due.json()['problems']['current'] if p['code']=='material_incomplete')
        assert changed['id']==group['id'] and {o['material_id'] for o in changed['affected']}=={'b'}
        def tick():app.state.host.require_store().read_versions.advance(wall=clock[0])
        client.portal.call(tick)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']}).status_code==304
        after=client.get('/api/v3/status/details')
        assert after.headers['etag']==due.headers['etag'] and after.json()['problems']==due.json()['problems']


def test_due_group_closure_keeps_id_and_etag_before_and_after_tick(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
                raw=json.dumps({'extra':{'_reader':{'full':False}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube','a',0,1,%s,0)",(raw,))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true)")
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','failed','bad',NULL)")
                save_keys(store.db,['a']);store.incomplete_draft.mark(['a'])
            store.youtube.start(clock[0]);app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        problem=next(p for p in before.json()['problems']['current'] if p['code']=='material_incomplete')
        clock[0]=111
        due=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert due.status_code==200
        assert not any(p['id']==problem['id'] for p in due.json()['problems']['current'])
        closed=next(p for p in due.json()['problems']['recent'] if p['id']==problem['id'])
        assert closed['outcome']=='resolved'
        def tick():app.state.host.require_store().read_versions.advance(wall=clock[0])
        client.portal.call(tick)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':due.headers['etag']}).status_code==304
        after=client.get('/api/v3/status/details')
        assert after.headers['etag']==due.headers['etag'] and after.json()['problems']==due.json()['problems']


def test_unticked_virtual_closure_expires_consistently_after_24_hours(tmp_path,postgres_url):
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
                raw=json.dumps({'extra':{'_reader':{'full':False}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube','a',0,1,%s,0)",(raw,))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true)")
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','failed','bad',NULL)")
                save_keys(store.db,['a']);store.incomplete_draft.mark(['a'])
            store.youtube.start(clock[0]);app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        old=next(p['id'] for p in before.json()['problems']['current'] if p['code']=='material_incomplete')
        clock[0]=110+86401
        overdue=client.get('/api/v3/status/details')
        assert not any(p['id']==old for p in overdue.json()['problems']['current']+overdue.json()['problems']['recent'])
        def tick():app.state.host.require_store().read_versions.advance(wall=clock[0])
        client.portal.call(tick)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':overdue.headers['etag']}).status_code==304
        after=client.get('/api/v3/status/details')
        assert after.headers['etag']==overdue.headers['etag'] and after.json()['problems']==overdue.json()['problems']
