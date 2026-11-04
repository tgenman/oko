"""Conditional HTTP delivery, storage identity and publication changes."""
import time
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, wait_for_recovery


def test_publication_etag_survives_restart_and_viewed_is_explicit(tmp_path):
    _, items = library(tmp_path, [1])
    previous = None
    for _ in range(2):
        app = traced_app(tmp_path)
        with TestClient(app, headers=HEADERS) as client:
            response = client.get('/api/v3/publication/' + items[0])
            assert response.status_code == 200
            assert response.json()['id'] == items[0]
            current = (response.headers['ETag'], client.get('/api/v3/status/details').headers['X-Oko-Storage-Id'])
            if previous:
                assert current == previous
            previous = current
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        assert client.post('/api/v3/materials/'+items[0]+'/viewed').status_code == 200
        assert client.get('/api/v3/publication/'+items[0], headers={'If-None-Match':previous[0]}).status_code == 404


def test_noop_write_and_other_folder_leave_publication_etag_unchanged(tmp_path):
    feeds, items = library(tmp_path,[1,1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        first=client.get('/api/v3/publication/'+items[0])
        page=client.get('/api/v3/cards',params={'feed':feeds[0]})
        assert client.patch('/api/v3/feeds/'+feeds[0],json={'title':'Synthetic 0'}).status_code==200
        assert client.post('/api/v3/materials/'+items[1]+'/viewed').status_code==200
        current=client.get('/api/v3/publication/'+items[0],headers={'If-None-Match':first.headers['ETag']})
        assert current.status_code==304 and not current.content
        assert client.get('/api/v3/cards',params={'feed':feeds[0]},headers={'If-None-Match':page.headers['ETag']}).status_code==304


def test_card_membership_changes_and_storage_mismatch_is_explicit(tmp_path):
    _,items=library(tmp_path,[2])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        page=client.get('/api/v3/cards')
        assert len(page.json()['items'])==2
        assert client.post('/api/v3/cards/read',json={'ids':[items[0]]}).status_code==204
        changed=client.get('/api/v3/cards',headers={'If-None-Match':page.headers['ETag']})
        assert changed.status_code==200 and len(changed.json()['items'])==1
        reset=client.get('/api/v3/publication/'+changed.json()['items'][0]['id'],headers={'X-Oko-Storage-Id':'00000000-0000-4000-8000-000000000000'})
        assert reset.status_code==409 and reset.json()['code']=='storage_changed'


def test_failed_viewed_write_keeps_the_card_page_unchanged(tmp_path):
    library(tmp_path,[2])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        initial=client.get('/api/v3/cards')
        identity=initial.json()['items'][0]['id']
        def install():
            db=app.state.host.require_store().db
            with db.access_lock:
                db._db.execute("CREATE FUNCTION pg_temp.reject_page_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic write failure'; END $$")
                db._db.execute('CREATE TRIGGER synthetic_page_failure BEFORE UPDATE OF feed_id ON publications FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_page_delete()')
        def remove():
            db=app.state.host.require_store().db
            with db.access_lock:
                db._db.execute('DROP TRIGGER synthetic_page_failure ON publications')
        client.portal.call(install)
        try:
            assert client.post('/api/v3/cards/read',json={'ids':[identity]}).status_code==500
        finally:
            client.portal.call(remove)
        after=client.get('/api/v3/cards',headers={'If-None-Match':initial.headers['ETag']})
        assert after.status_code==304
        assert client.get('/api/v3/cards').json()==initial.json()


def test_card_etag_changes_when_retry_becomes_due_without_background_write(tmp_path):
    _, items = library(tmp_path, [1])
    clock = [0.0]
    app = traced_app(tmp_path, clock=lambda: clock[0], wall_clock=lambda: 1800000000 + clock[0])
    with TestClient(app, headers=HEADERS) as client:
        async def hold():
            await app.state.host.execution._actions.acquire()
        client.portal.call(hold)
        try:
            def schedule():
                store = app.state.host.require_store()
                app.state.host.execution.reader_advance = None
                with store.db:
                    store.db.execute("UPDATE jobs SET next=10,reason_message='Synthetic wait',reason_code='source-connection' WHERE kind='fetch' AND item=%s", (items[0],))
            client.portal.call(schedule)
            first = client.get('/api/v3/cards')
            assert first.status_code == 200 and first.json()['items'][0]['loading'] is False
            assert client.get('/api/v3/cards', headers={'If-None-Match':first.headers['ETag']}).status_code == 304
            clock[0] = 11.0
            after = client.get('/api/v3/cards', headers={'If-None-Match':first.headers['ETag']})
            assert after.status_code == 200 and after.json()['items'][0]['loading'] is True
        finally:
            client.portal.call(app.state.host.execution._actions.release)


def test_card_etag_tracks_linked_content_without_parent_version_write(tmp_path):
    _, items = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        async def hold():
            await app.state.host.execution._actions.acquire()
        client.portal.call(hold)
        try:
            def add_link():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute("""INSERT INTO publications(id,url,platform,external_id,status,content,title,discovered_at)
                        SELECT 'linked','https://example.org/linked','other_web','linked','ready',content,title,discovered_at FROM publications WHERE id=%s""", (items[0],))
                    store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')", (items[0],))
            client.portal.call(add_link)
            first = client.get('/api/v3/cards')
            assert first.status_code == 200 and first.json()['items'][0]['changed'] is False
            def change_child():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute("UPDATE publications SET content=jsonb_set(content,'{extra,_reader,changed}','true'::jsonb) WHERE id='linked'")
            client.portal.call(change_child)
            after = client.get('/api/v3/cards', headers={'If-None-Match':first.headers['ETag']})
            assert after.status_code == 200 and after.json()['items'][0]['changed'] is True
        finally:
            client.portal.call(app.state.host.execution._actions.release)


def test_status_etag_expires_finished_problem_without_background_write(tmp_path):
    from tests.support.codex import CodexService, configure_codex
    wall = [1800000000.0]
    app = traced_app(tmp_path, codex_client=CodexService(), wall_clock=lambda: wall[0])
    with TestClient(app, headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses':True}).status_code == 200
        assert client.get('/api/v3/status/details').json()['problems']['current']
        configure_codex(client)
        finished = client.get('/api/v3/status/details')
        assert finished.json()['problems']['recent']
        async def hold():
            await app.state.host.execution._actions.acquire()
        client.portal.call(hold)
        try:
            client.portal.call(lambda: setattr(app.state.host.execution, 'reader_advance', None))
            stable = client.get('/api/v3/status/details')
            assert client.get('/api/v3/status/details', headers={'If-None-Match':stable.headers['ETag']}).status_code == 304
            wall[0] += 86400
            expired = client.get('/api/v3/status/details', headers={'If-None-Match':stable.headers['ETag']})
            assert expired.status_code == 200 and expired.json()['problems']['recent'] == []
        finally:
            client.portal.call(app.state.host.execution._actions.release)
