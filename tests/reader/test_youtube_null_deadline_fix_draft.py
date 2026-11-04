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
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,content) VALUES ('p','youtube','p',0,%s)",(raw,))
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
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


def test_http_status_after_null_next_deferral_has_no_infinite_timestamp(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100,clock=lambda:10)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,content) VALUES ('p','youtube','p',0,'{}')")
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
