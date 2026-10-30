from tests.support.processing_jobs import save_derivative
from tests.support.processing_jobs import reconcile
"""Conditional HTTP delivery, storage identity and publication changes."""
import time
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing, wait_for_recovery


def changed_within_background_interval(client, url, etag):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        response=client.get(url,headers={'If-None-Match':etag})
        if response.status_code==200:return response
        assert response.status_code==304
        time.sleep(.02)
    raise AssertionError('Runtime did not publish the due transition within two seconds')


def test_unchanged_navigation_and_cards_return_no_body(tmp_path):
    feeds, _ = library(tmp_path, [26] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        first = client.get('/api/v3/navigation')
        assert first.status_code == 200
        response = client.get('/api/v3/navigation', headers={'If-None-Match':first.headers['ETag']})
        assert response.status_code == 304 and not response.content
        page=client.get('/api/v3/cards',params={'feed':feeds[0]})
        assert page.status_code==200
        assert len(page.json()['items'])==26
        assert all('theses' not in r and 'filter' not in r and 'snippet' not in r for r in page.json()['items'])
        unchanged=client.get('/api/v3/cards',params={'feed':feeds[0]},headers={'If-None-Match':page.headers['ETag']})
        assert unchanged.status_code==304 and not unchanged.content
        assert int(unchanged.headers['X-Test-SQL']) < 35
        assert not any('FROM settings' in q or 'SELECT * FROM items' in q for q in app.state.last_sql)


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


def test_feed_rename_changes_navigation_and_sql_rollback_keeps_it(tmp_path):
    import pytest
    feeds,_=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        first=client.get('/api/v3/navigation')
        assert client.patch('/api/v3/feeds/'+feeds[0],json={'title':'Renamed'}).status_code==200
        renamed=client.get('/api/v3/navigation',headers={'If-None-Match':first.headers['ETag']})
        assert renamed.status_code==200 and renamed.headers['ETag']!=first.headers['ETag']
        assert next(feed for folder in renamed.json()['folders'] for feed in folder['feeds'] if feed['id']==feeds[0])['title']=='Renamed'
        def rollback():
            store=app.state.host.require_store()
            with store.db:
                store.db.begin()
                store.db.execute("UPDATE feeds SET title='Uncommitted' WHERE id=%s",(feeds[0],))
                raise ValueError('rollback fixture')
        with pytest.raises(ValueError,match='rollback fixture'):
            client.portal.call(rollback)
        response=client.get('/api/v3/navigation',headers={'If-None-Match':renamed.headers['ETag']})
        assert response.status_code==304 and not response.content


def test_originals_inheritance_changes_publication_etag(tmp_path):
    feeds, items = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        first = client.get('/api/v3/publication/'+items[0])
        assert first.json()['originals'] is True
        assert client.patch('/api/v3/settings/oko/global', json={'originals':False}).status_code == 200
        changed = client.get('/api/v3/publication/'+items[0], headers={'If-None-Match':first.headers['ETag']})
        assert changed.status_code == 200 and changed.json()['originals'] is False
        assert client.patch('/api/v3/settings/feed/'+feeds[0], json={'originals':True}).status_code == 200
        override = client.get('/api/v3/publication/'+items[0], headers={'If-None-Match':changed.headers['ETag']})
        assert override.status_code == 200 and override.json()['originals'] is True


def test_status_etag_tracks_settings_without_status_trigger(tmp_path):
    library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        first=client.get('/api/v3/status/details')
        assert client.patch('/api/v3/settings/oko/global',json={'originals':False}).status_code==200
        changed=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['ETag']})
        assert changed.status_code==200 and changed.headers['ETag']!=first.headers['ETag']


def test_status_etag_tracks_history_progress_without_source_revision_change(tmp_path):
    feeds,_ = library(tmp_path,[1])
    app = traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        first = client.get('/api/v3/status/details')
        def change():
            store=app.state.host.require_store()
            before=store.subscriptions.source_revision()
            with store.db:
                store.subscriptions.history.warnings(feeds[0],['Synthetic history warning'])
            return before,store.subscriptions.source_revision()
        before,after = client.portal.call(change)
        assert before == after
        changed = client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['ETag']})
        assert changed.status_code==200
        assert any('Synthetic history warning' in history['history']['progress']['warnings'] for history in changed.json()['initial_histories'])


def test_due_fetch_changes_publication_etag_without_dispatch(tmp_path):
    _,items=library(tmp_path,[1])
    clock=[0.0]
    app=traced_app(tmp_path,clock=lambda:clock[0],wall_clock=lambda:1800000000+clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def schedule():
            store=app.state.host.require_store()
            with store.db:
                store.db.execute("UPDATE jobs SET next=10,error='Synthetic wait',problem_code='source-connection' WHERE kind='fetch' AND item=%s",(items[0],))
        client.portal.call(schedule)
        url='/api/v3/publication/'+items[0]
        first=client.get(url)
        assert first.json()['loading'] is False
        assert client.get(url,headers={'If-None-Match':first.headers['ETag']}).status_code==304
        clock[0]=11.0
        after=changed_within_background_interval(client,url,first.headers['ETag'])
        assert after.headers['ETag']!=first.headers['ETag']
        assert after.json()['loading'] is True


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


def test_theses_completion_delivers_whole_publication(tmp_path):
    from oko.collection.contracts import Material
    _,items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code==200
        def complete():
            store=app.state.host.require_store()
            job=store.jobs.next_job(store.clock(),feeds=[store.item_feed(items[0])])
            assert job is not None and job.kind=='fetch'
            from tests.support.source import save_material
            save_material(store,job,Material('Synthetic','Long original text '*1000,captions='available'))
        client.portal.call(complete)
        first=client.get('/api/v3/publication/'+items[0])
        assert first.status_code==200
        def summarize():
            store=app.state.host.require_store()
            reconcile(store,'theses')
            job=store.jobs.next_job(store.clock(),ai_only=True)
            assert job is not None and job.kind=='theses'
            signature=store.processing.theses.signature(job.item)
            assert signature is not None
            save_derivative(store,job,signature,{'points':[{'text':'Synthetic summary','sources':[{'id':job.item,'url':'https://example.org/0/0'}]}]},'Synthetic summary',provenance=None)
        client.portal.call(summarize)
        current=client.get('/api/v3/publication/'+items[0],headers={'If-None-Match':first.headers['ETag']})
        assert current.status_code==200
        assert current.json()['theses']['result']['points']==['Synthetic summary']
        assert current.json()['text']==first.json()['text']
        assert current.json()['media']==first.json()['media']
        assert 'Long original text' in current.json()['text']


def test_stopped_get_returns_final_status_and_explicit_unavailability(tmp_path):
    _,items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        assert client.post('/api/v3/stop').status_code==200
        import time
        for _ in range(100):
            if app.state.host.execution._store is None:break
            time.sleep(.01)
        assert app.state.host.execution._store is None
        stopped=client.get('/api/v3/status')
        assert stopped.json()['runtime_state']=='stopped'
        unavailable=client.get('/api/v3/publication/'+items[0])
        assert unavailable.status_code==409 and unavailable.json()['code']=='runtime_stopped'
        assert client.get('/api/v3/status',headers={'If-None-Match':stopped.headers['ETag']}).status_code==304


def test_problem_expiry_advances_status_without_an_external_write(tmp_path):
    from tests.support.codex import CodexService,configure_codex
    wall=[1800000000.0]
    app=traced_app(tmp_path,codex_client=CodexService(),wall_clock=lambda:wall[0])
    with TestClient(app,headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code==200
        url='/api/v3/status/details'
        first=client.get(url)
        assert first.json()['problems']['current']
        configure_codex(client)
        finished=client.get(url)
        assert finished.json()['problems']['recent']
        wall[0]+=86399
        assert client.get(url,headers={'If-None-Match':finished.headers['ETag']}).status_code==304
        wall[0]+=1
        expired=changed_within_background_interval(client,url,finished.headers['ETag'])
        assert expired.json()['problems']['recent']==[]


def test_linked_feed_move_updates_lists_without_retransmitting_cards(tmp_path):
    feeds,_=library(tmp_path,[1,1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        folders=[f['folder_id'] for f in client.get('/api/v3/subscriptions').json()['feeds']]
        page=client.get('/api/v3/cards',params={'folder':folders[0]})
        assert len(page.json()['items'])==1
        first=client.get('/api/v3/navigation')
        assert client.patch('/api/v3/feeds/'+feeds[0],json={'folder_id':folders[1],'title':'Moved'}).status_code==200
        current=client.get('/api/v3/navigation',headers={'If-None-Match':first.headers['ETag']})
        assert current.status_code==200
        destination=next(folder for folder in current.json()['folders'] if folder['id']==folders[1])
        assert next(feed for feed in destination['feeds'] if feed['id']==feeds[0])['title']=='Moved'
        origin=next(folder for folder in current.json()['folders'] if folder['id']==folders[0])
        assert all(feed['id']!=feeds[0] for feed in origin['feeds'])
        changed=client.get('/api/v3/cards',params={'folder':folders[0]},headers={'If-None-Match':page.headers['ETag']})
        assert changed.status_code==200 and changed.json()['items']==[]


def test_card_pages_follow_batched_writes_and_survive_restart(tmp_path):
    for count in [100,200,400]:
        root=tmp_path/str(count)
        library(root,[count])
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client:
            page=client.get('/api/v3/cards',params={'limit':40})
            assert page.status_code==200
            first=page.json()
            assert first['total']==count and len(first['items'])==40
            assert client.get('/api/v3/cards',params={'limit':40},headers={'If-None-Match':page.headers['ETag']}).status_code==304
            selected=[card['id'] for card in first['items'][:10]]
            assert client.post('/api/v3/cards/read',json={'ids':selected}).status_code==204
            current=client.get('/api/v3/cards',params={'limit':40})
            assert current.json()['total']==count-10
            assert not set(selected)&{card['id'] for card in current.json()['items']}
        with TestClient(traced_app(root),headers=HEADERS) as reopened:
            assert reopened.get('/api/v3/cards').json()['total']==count-10



def test_publication_keeps_filter_provenance_across_restart(tmp_path):
    from tests.support.app import create_app
    from tests.support.filters import FilterSource,transport,setup_filters
    from tests.support.reader import wait_for
    options=dict(source=FilterSource(),token='test',api_only=True,ai_transport=transport([]))
    identity=None
    for _ in range(2):
        with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
            if identity is None:
                setup_filters(client)
                cards=wait_for(client,lambda:(v if len(v:=[client.get('/api/v3/publication/'+c['id']).json() for c in client.get('/api/v3/cards').json()['items']])==2 and all(c['filter']['state']=='ready' for c in v) else None))
                identity=cards[0]['id']
            expected=client.get('/api/v3/publication/'+identity).json()['filter']['result']['provenance']
            frame=client.get('/api/v3/publication/'+identity).json()
            actual=frame['filter']['result']['provenance']
            assert actual==expected=={'kind':'local','processor_id':'llamacpp','model':{'name':'local:latest','state':'known'}}


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
                db._db.execute('CREATE TRIGGER synthetic_page_failure BEFORE DELETE ON feed_publications FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_page_delete()')
        def remove():
            db=app.state.host.require_store().db
            with db.access_lock:
                db._db.execute('DROP TRIGGER synthetic_page_failure ON feed_publications')
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
                    store.db.execute("UPDATE jobs SET next=10,error='Synthetic wait',problem_code='source-connection' WHERE kind='fetch' AND item=%s", (items[0],))
            client.portal.call(schedule)
            first = client.get('/api/v3/cards')
            assert first.status_code == 200 and first.json()['items'][0]['loading'] is False
            assert client.get('/api/v3/cards', headers={'If-None-Match':first.headers['ETag']}).status_code == 304
            clock[0] = 11.0
            after = client.get('/api/v3/cards', headers={'If-None-Match':first.headers['ETag']})
            assert after.status_code == 200 and after.json()['items'][0]['loading'] is True
        finally:
            client.portal.call(app.state.host.execution._actions.release)


def test_card_etag_changes_when_media_retry_becomes_due_without_background_write(tmp_path):
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
                    store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch' AND item=%s", (items[0],))
                    store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media',%s,'image','https://fixture.test/image','image','pending_download')", (items[0],))
                    store.db.execute("INSERT INTO jobs(kind,item,status,next) VALUES ('download_media','media','pending',10)")
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
                    store.db.execute("""INSERT INTO items(id,source_id,url,platform,external_id,complete,content,title)
                        SELECT 'linked','linked-source','https://example.org/linked','other_web','linked',1,content,title FROM items WHERE id=%s""", (items[0],))
                    store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')", (items[0],))
            client.portal.call(add_link)
            first = client.get('/api/v3/cards')
            assert first.status_code == 200 and first.json()['items'][0]['changed'] is False
            def change_child():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute("UPDATE items SET content=jsonb_set(content::jsonb,'{extra,_reader,changed}','true'::jsonb)::text WHERE id='linked'")
            client.portal.call(change_child)
            after = client.get('/api/v3/cards', headers={'If-None-Match':first.headers['ETag']})
            assert after.status_code == 200 and after.json()['items'][0]['changed'] is True
        finally:
            client.portal.call(app.state.host.execution._actions.release)


def test_status_etag_changes_when_retry_becomes_due_without_background_write(tmp_path):
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
                    store.db.execute("UPDATE jobs SET next=10,error='Synthetic wait',problem_code='source-connection' WHERE kind='fetch' AND item=%s", (items[0],))
            client.portal.call(schedule)
            first = client.get('/api/v3/status/details')
            assert first.status_code == 200, first.text
            assert client.get('/api/v3/status/details', headers={'If-None-Match':first.headers['ETag']}).status_code == 304
            clock[0] = 11.0
            after = client.get('/api/v3/status/details', headers={'If-None-Match':first.headers['ETag']})
            assert after.status_code == 200
        finally:
            client.portal.call(app.state.host.execution._actions.release)


def test_status_etag_changes_when_youtube_wait_expires_without_background_write(tmp_path):
    library(tmp_path, [1])
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
                    store.db.execute('UPDATE youtube_access SET blocked_until=%s WHERE id=1', (1800000010,))
            client.portal.call(schedule)
            first = client.get('/api/v3/status/details')
            assert first.status_code == 200 and first.json()['youtube']['admission'] == 'paused'
            assert client.get('/api/v3/status/details', headers={'If-None-Match':first.headers['ETag']}).status_code == 304
            clock[0] = 11.0
            after = client.get('/api/v3/status/details', headers={'If-None-Match':first.headers['ETag']})
            assert after.status_code == 200 and after.json()['youtube']['admission'] != 'paused'
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
