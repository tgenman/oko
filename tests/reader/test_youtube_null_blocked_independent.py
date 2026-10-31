import psycopg
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store

def test_nullable_legacy_blocked_until_does_not_prevent_startup(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:80])
        db.execute('UPDATE youtube_access SET blocked_until=NULL WHERE id=1')
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        assert store.db.execute('SELECT blocked_until FROM youtube_access WHERE id=1').fetchone()[0] is None
    finally:store.db.close()

def test_nan_blocked_until_never_launches_caption_network(tmp_path,postgres_url):
    import asyncio
    from types import SimpleNamespace
    from oko.storage.transaction import transaction
    from oko.storage.job_types import Job
    from oko.workers.collection import CaptionAttempt,CaptionsDeferred
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100,clock=lambda:10)
    calls=[]
    class Client:
        async def fetch(self,video):calls.append(video);return 'synthetic'
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1)")
            jid=store.db.execute("SELECT id FROM jobs WHERE item='m' AND kind='download_media'").fetchone()[0]
            store.db.execute("UPDATE youtube_access SET next_at=0,blocked_until='NaN'::float8 WHERE id=1")
        async def run():
            worker=SimpleNamespace(media=SimpleNamespace(owner=lambda _:'p'),execution=SimpleNamespace(_store=store,_actions=asyncio.Lock()),_live=lambda:True,
                service=SimpleNamespace(caption_ready=store.youtube.ready_at,caption_config=lambda:{},caption_started=store.youtube.start),
                _clock=lambda:10,_wall_clock=lambda:100,_captions_active=None,_youtube_captions=Client())
            with pytest.raises(CaptionsDeferred):await CaptionAttempt(worker,store,Job(jid,'download_media','m',1)).fetch('video')
        asyncio.run(run())
        assert not calls
    finally:store.db.close()
