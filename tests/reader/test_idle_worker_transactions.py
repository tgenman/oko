"""Idle workers must not open write transactions when no Job is due."""
import asyncio

from oko.storage.transaction import transaction
from tests.support.source import StructuredSource
from tests.support.synthetic_source import SyntheticSource
from tests.support.worker_library import collection_worker, worker_library, workers


def test_idle_collection_and_processing_do_not_open_write_transactions(tmp_path):
    store,_,execution,tasks=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('DELETE FROM jobs')
        collection=collection_worker(tmp_path,store,StructuredSource(SyntheticSource()))
        collection._started=True
        collection._source_revision=collection.service.source_revision()
        collection._collection_interval=10**9
        collection._last_collection=collection._clock()
        collection._last_media_cleanup=collection._clock()
        store.processing.background=True
        processing=workers(execution,tasks)[0]
        queries=[]
        store.db.set_trace_callback(queries.append)
        asyncio.run(collection.tick())
        collection._sources.clear()
        asyncio.run(collection.tick())
        asyncio.run(processing.tick())
        store.db.set_trace_callback(None)
        assert not [query for query in queries if query.upper().startswith(('BEGIN','COMMIT'))], queries
    finally:
        store.db.set_trace_callback(None)
        store.db.close()


def test_collection_progresses_without_dispatching_obsolete_cleanup(tmp_path):
    store,_,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('DELETE FROM jobs')
            for number in range(3):
                item=f'fresh-{number}'
                store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES (%s,'rss',%s,'fixture')",(item,item))
                store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('fetch',%s,'pending')",(item,))
                store.db.execute("INSERT INTO jobs(kind,item,status,reason_message) VALUES ('delete',%s,'pending','retry')",(f'old-{number}',))
        worker=collection_worker(tmp_path,store,StructuredSource(SyntheticSource()))
        worker._started=True
        worker._source_revision=worker.service.source_revision()
        worker._collection_interval=10**9
        worker._last_collection=worker._clock()
        worker._last_media_cleanup=worker._clock()
        seen=[]

        async def execute(job):
            seen.append(job.kind)
            with transaction(store.db):
                store.db.execute("UPDATE jobs SET status='done' WHERE id=%s",(job.id,))

        worker._execute=execute

        async def drive():
            for _ in range(3):
                await worker.tick()
                await worker.jobs._lanes['common']

        asyncio.run(drive())
        assert seen==['fetch','fetch','fetch']
        assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='delete' AND status='pending'").fetchone()[0]==3
    finally:
        store.db.close()
