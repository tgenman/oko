import asyncio
import json
import math
from types import SimpleNamespace
import pytest
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.workers.collection import CaptionAttempt,CaptionsDeferred
from tests.support.storage import Store


@pytest.mark.parametrize("deadline",[None,float("inf"),1e300])
def test_null_next_at_real_caption_attempt_defers_without_network(tmp_path,postgres_url,deadline):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100,clock=lambda:10)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'blocks':[],'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','pending_download')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.db.execute('UPDATE youtube_access SET next_at=%s WHERE id=1',(deadline,))
        async def run():
            execution=SimpleNamespace(_store=store,_actions=asyncio.Lock())
            worker=SimpleNamespace(media=SimpleNamespace(owner=lambda _: 'p'),execution=execution,_live=lambda:True,
                service=SimpleNamespace(caption_ready=store.youtube.ready_at),_clock=lambda:10,_wall_clock=lambda:100,_captions_active=None)
            attempt=CaptionAttempt(worker,store,Job(jid,'download_media','m',1))
            with pytest.raises(CaptionsDeferred):await attempt.fetch('synthetic-video')
            assert not attempt.attempted
        asyncio.run(run())
        row=store.db.execute('SELECT status,next FROM jobs WHERE id=%s',(jid,)).fetchone()
        assert row['status']=='pending' and row['next']>1e299
        assert store.db.execute('SELECT next_at FROM youtube_access WHERE id=1').fetchone()[0]==deadline
        assert store.work_summary()[0].retry_at is None
    finally:store.db.close()


@pytest.mark.parametrize('deadline',[float('inf'),float('nan'),1e300])
def test_unrepresentable_caption_retry_datetime_stays_unknown(deadline):
    from oko.workers.collection import caption_retry_datetime
    from oko.storage.reading import retry_time
    assert caption_retry_datetime(deadline) is None
    assert retry_time(deadline,10,100) is None


def test_finite_caption_retry_datetime_remains_exact():
    from datetime import datetime,timezone
    from oko.workers.collection import caption_retry_datetime
    from oko.storage.reading import retry_time
    assert caption_retry_datetime(200)==datetime.fromtimestamp(200,timezone.utc)
    assert retry_time(20,10,100)==110


def test_http_status_after_null_next_deferral_has_no_infinite_timestamp(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100,clock=lambda:10)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','pending_download')")
                jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                store.db.execute('UPDATE youtube_access SET next_at=NULL WHERE id=1')
            return jid
        jid=client.portal.call(setup)
        async def defer():
            store=app.state.host.require_store()
            worker=SimpleNamespace(media=SimpleNamespace(owner=lambda _: 'p'),execution=SimpleNamespace(_store=store,_actions=asyncio.Lock()),_live=lambda:True,
                service=SimpleNamespace(caption_ready=store.youtube.ready_at),_clock=lambda:10,_wall_clock=lambda:100,_captions_active=None)
            with pytest.raises(CaptionsDeferred):await CaptionAttempt(worker,store,Job(jid,'download_media','m',1)).fetch('synthetic-video')
        client.portal.call(defer)
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert response.json()['youtube']['admission']=='waiting' and response.json()['youtube']['retry_at'] is None


def test_unknown_next_at_does_not_hide_known_pause_expiry(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys,reservation
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'blocks':[],'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute('UPDATE youtube_access SET next_at=NULL,blocked_until=200 WHERE id=1')
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        saved=reservation(store,'incomplete:yt:p')
        assert saved is not None and saved['deadline']==200
    finally:store.db.close()


def test_restart_unknown_next_recovers_known_pause_boundary(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys,reservation
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'blocks':[],'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            save_keys(store.db,['p'])
            store.db.execute('UPDATE youtube_access SET next_at=NULL,blocked_until=200 WHERE id=1')
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100,defer_recovery=True)
        saved=reservation(store,'incomplete:yt:p')
        assert saved and saved['deadline']==200
    finally:store.db.close()


@pytest.mark.parametrize('blocked',[None,float('nan'),float('inf'),1e300])
def test_unknown_blocked_time_visible_until_explicit_completion(tmp_path,postgres_url,blocked):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    with transaction(store.db):store.db.execute('UPDATE youtube_access SET blocked_until=%s WHERE id=1',(blocked,))
    store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert response.json()['youtube']['admission']=='paused' and response.json()['youtube']['retry_at'] is None
        assert any(p['code']=='youtube_rate_limited' for p in response.json()['problems']['current'])
        actual=client.portal.call(lambda:app.state.host.require_store().db.execute('SELECT blocked_until FROM youtube_access WHERE id=1').fetchone()[0])
        if isinstance(blocked,float) and math.isnan(blocked):assert math.isnan(actual)
        else:assert actual==blocked
        client.portal.call(lambda:app.state.host.require_store().youtube.completed())
        after=client.get('/api/v3/status/details')
        assert after.status_code==200,after.text
        assert not any(p['code']=='youtube_rate_limited' for p in after.json()['problems']['current'])


def test_unknown_blocked_does_not_write_invalid_processor_reservation(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,processor_visible,processor_caption) VALUES ('download_media','m','failed',true,true)")
            store.db.execute('UPDATE youtube_access SET blocked_until=NULL WHERE id=1')
            store.problems.finish_processor_change([],None,100)
        assert store.db.execute('SELECT processor_reservation_id FROM youtube_access WHERE id=1').fetchone()[0] is None
        store.youtube.blocked(100,900)
        reserved=store.db.execute('SELECT processor_reservation_id FROM youtube_access WHERE id=1').fetchone()[0]
        assert reserved and store.db.execute('SELECT body::jsonb FROM reader_problems WHERE id=%s',(reserved,)).fetchone()
    finally:store.db.close()


def test_unknown_deadline_startup_does_not_scan_foreign_history(tmp_path,postgres_url,monkeypatch):
    from oko.storage.postgres_connection import PostgresConnection
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    with transaction(store.db):
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('rss','rss','R','default','rss','rss','connected',ARRAY[]::text[])")
        store.db.execute("INSERT INTO publications(id,viewed,complete,incomplete_class) SELECT 'p-'||n,0,1,1 FROM generate_series(1,10000)n")
        store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'rss','p-'||n,true FROM generate_series(1,10000)n")
        store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','p-'||n,'done' FROM generate_series(1,10000)n")
        store.db.execute('UPDATE youtube_access SET next_at=NULL WHERE id=1')
        store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE publications')
    store.db.close()
    trace=[];original=PostgresConnection.__init__
    def init(self,*args,**kwargs):
        original(self,*args,**kwargs);self.set_trace_callback(trace.append)
    monkeypatch.setattr(PostgresConnection,'__init__',init)
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100,defer_recovery=True)
    try:
        probes=[q for q in trace if q.startswith("SELECT p.id FROM feeds f JOIN publications") or q.startswith("SELECT p.id FROM jobs j JOIN media_files") or q.startswith("SELECT kind,item FROM jobs WHERE kind='fetch' AND incomplete_youtube_sensitive")]
        assert len(probes)==3
        for sql in probes:
            plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql).fetchone()[0][0]['Plan']
            assert scan_rows(plan)<100,plan
    finally:store.db.close()
