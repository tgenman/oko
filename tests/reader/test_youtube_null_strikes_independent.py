from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.transaction import transaction

def test_nullable_legacy_strikes_keeps_http_available(tmp_path,postgres_url):
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):store.db.execute('UPDATE youtube_access SET strikes=NULL WHERE id=1')
        client.portal.call(setup)
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert response.json()['youtube']['strikes'] is None
        response=client.get('/api/v3/youtube')
        assert response.status_code==200 and response.json()['strikes'] is None
        assert client.portal.call(lambda: app.state.host.require_store().db.execute('SELECT strikes FROM youtube_access WHERE id=1').fetchone()['strikes']) is None

def test_unknown_strikes_uses_conservative_backoff_and_success_resets(tmp_path,postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE youtube_access SET strikes=NULL,blocked_until=30000 WHERE id=1')
        store.youtube.blocked(100,0)
        row=store.db.execute('SELECT strikes,blocked_until FROM youtube_access WHERE id=1').fetchone()
        assert row['strikes'] is None and row['blocked_until']==30000
        store.youtube.completed()
        row=store.db.execute('SELECT strikes,blocked_until FROM youtube_access WHERE id=1').fetchone()
        assert row['strikes']==0 and row['blocked_until']==0
    finally:store.db.close()


def test_unknown_strikes_rollback_and_unknown_deadline(tmp_path,postgres_url):
    import pytest
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE youtube_access SET strikes=NULL,blocked_until=0 WHERE id=1')
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.youtube.blocked(100,0)
                raise RuntimeError('rollback')
        row=store.db.execute('SELECT strikes,blocked_until FROM youtube_access WHERE id=1').fetchone()
        assert row['strikes'] is None and row['blocked_until']==0
        with transaction(store.db):store.db.execute('UPDATE youtube_access SET blocked_until=NULL WHERE id=1')
        store.youtube.blocked(100,0)
        row=store.db.execute('SELECT strikes,blocked_until FROM youtube_access WHERE id=1').fetchone()
        assert row['strikes'] is None and row['blocked_until'] is None
    finally:store.db.close()


def test_worker_handles_unknown_strikes_without_shortening_backoff(tmp_path):
    from tests.reader.test_youtube_access import Clock, Captions, app, add, heartbeat, eventually
    from oko.collection.youtube.access import CaptionsBlocked
    from datetime import datetime
    clock=Clock()
    captions=Captions(clock,[CaptionsBlocked(),'complete'])
    with TestClient(app(tmp_path,clock,captions),headers=HEADERS) as client:
        def unknown():
            store=client.app.state.host.require_store()
            with transaction(store.db):store.db.execute('UPDATE youtube_access SET strikes=NULL WHERE id=1')
        client.portal.call(unknown)
        add(client)
        heartbeat(client)
        def paused():
            state=client.get('/api/v3/youtube').json()
            if state['admission']=='paused':return state
            clock.advance(1)
            return None
        state=eventually(paused)
        assert state['strikes'] is None
        deadline=datetime.fromisoformat(state['retry_at'].replace('Z','+00:00')).timestamp()
        assert deadline==captions.calls[0][1]+21600
        clock.advance(deadline-clock.wall)
        eventually(lambda: len(captions.calls)==2 and client.get('/api/v3/youtube').json()['strikes']==0)
