"""Known publication fetch admission under damaged and repaired histories."""
import asyncio
import json

import psycopg
import pytest

from oko.collection.contracts import SourceError
from oko.storage.transaction import transaction
from oko.subscriptions.history import InitialHistory
from tests.support.job_claim import claim_job
from tests.support.legacy_source import FetchResult
from tests.support.source import StructuredSource
from tests.support.storage import cleanup, database_url, owned_schemas
from tests.support.synthetic_material import material as Material
from tests.support.worker_library import collection_worker, worker_library


BAD=json.dumps(dict(mode='count',count=1,since=None,warnings='not-a-list'))


def setup_history(store,item):
    with transaction(store.db):
        store.subscriptions.history.configure('fixture',InitialHistory(count=1))
        store.subscriptions.history.enroll('fixture',[item],False)
        original=store.db.execute("SELECT selection FROM initial_history WHERE feed='fixture'").fetchone()[0]
        store.db.execute("UPDATE initial_history SET selection=%s,scan_complete=0 WHERE feed='fixture'",(BAD,))
        store.jobs.enqueue_fetch(item)
    return original


def test_structurally_valid_damaged_history_allows_known_fetch(tmp_path):
    async def scenario():
        previous=owned_schemas()
        class Source:
            changes=None
            async def fetch(self,publication):
                return FetchResult(Material('Updated','Synthetic fetched text'))
        store,item,_,_=worker_library(tmp_path)
        try:
            setup_history(store,item)
            worker=collection_worker(tmp_path,store,StructuredSource(Source()))
            job=claim_job(store,store.clock(),feeds=['fixture'],invalid_history_feeds=['fixture'])
            assert job and job.kind=='fetch' and job.item==item
            await worker._execute(job)
            row=store.db.execute('SELECT status FROM jobs WHERE id=%s',(job.id,)).fetchone()
            assert row['status']=='done'
            assert store.db.execute("SELECT selection FROM initial_history WHERE feed='fixture'").fetchone()[0]==BAD
            assert store.db.execute("SELECT count(*) FROM history_deferred_events WHERE feed='fixture' AND item=%s",(item,)).fetchone()[0]==1
            assert store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]['blocks'][0]['text']=='Synthetic fetched text'
        finally:
            store.db.close()
            cleanup(created_after=previous)
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['paused','selecting'])
def test_repair_is_rechecked_without_waiting_for_cached_invalid_feed(tmp_path,mode):
    previous=owned_schemas()
    store,item,_,_=worker_library(tmp_path)
    try:
        original=setup_history(store,item)
        # Leave a deferred event: it must not hide a real pause on repair.
        with transaction(store.db):
            store.subscriptions.history.saved(item,True)
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('other','rss','other','fixture')")
            store.jobs.enqueue_fetch('other')
        with psycopg.connect(database_url(tmp_path),autocommit=True) as other:
            other.execute('UPDATE initial_history SET selection=%s,paused=%s,scan_complete=%s WHERE feed=%s',
                          (original,1 if mode=='paused' else 0,1 if mode=='paused' else 0,'fixture'))
        job=claim_job(store,store.clock(),feeds=['fixture'],invalid_history_feeds=['fixture'])
        assert job and job.kind=='fetch' and job.item=='other'
        assert store.db.execute("SELECT status FROM jobs WHERE kind='fetch' AND item=%s",(item,)).fetchone()[0]=='pending'
    finally:
        store.db.close()
        cleanup(created_after=previous)


@pytest.mark.parametrize('failed',[False,True],ids=['late-page','late-error'])
def test_inflight_fetch_does_not_commit_after_repair_to_paused(tmp_path,failed):
    async def scenario():
        previous=owned_schemas()
        entered,release=asyncio.Event(),asyncio.Event()
        class Source:
            changes=None
            async def fetch(self,publication):
                entered.set()
                await release.wait()
                if failed:
                    raise SourceError('synthetic late failure',retryable=False)
                return FetchResult(Material('Updated','Late synthetic text'))
        store,item,_,_=worker_library(tmp_path)
        task=None
        try:
            original=setup_history(store,item)
            with transaction(store.db):
                store.subscriptions.history.saved(item,True)
            worker=collection_worker(tmp_path,store,StructuredSource(Source()))
            job=claim_job(store,store.clock(),feeds=['fixture'],invalid_history_feeds=['fixture'])
            assert job and job.kind=='fetch'
            task=asyncio.create_task(worker._execute(job))
            await asyncio.wait_for(entered.wait(),5)
            before=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            with psycopg.connect(database_url(tmp_path),autocommit=True) as other:
                other.execute("UPDATE initial_history SET selection=%s,paused=1,scan_complete=1 WHERE feed='fixture'",(original,))
            release.set()
            await asyncio.wait_for(task,5)
            task=None
            row=store.db.execute('SELECT status,generation,reason_message FROM jobs WHERE id=%s',(job.id,)).fetchone()
            assert row['status']=='pending' and row['generation']>job.generation and row['reason_message'] is None
            assert store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]==before
            assert store.db.execute("SELECT count(*) FROM history_deferred_events WHERE feed='fixture' AND item=%s",(item,)).fetchone()[0]==1
        finally:
            release.set()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            store.db.close()
            cleanup(created_after=previous)
    asyncio.run(scenario())
